// Forgot-password by e-mail: the app sends a real e-mail (to a small mail server this journey runs), the person opens the
// link from it, chooses a new password, and the link cannot be used twice.
import { journey, PW, startMailSink } from "../lib.mjs";

const sink = await startMailSink();
const NEW = "a brand new passphrase";

await journey("password_reset", {
  auth: true,
  extraEnv: (port) => ({
    AA_SMTP_HOST: "127.0.0.1", AA_SMTP_PORT: String(sink.port), AA_SMTP_SECURITY: "none",
    AA_SMTP_FROM: "timetable@college.test", AA_PUBLIC_URL: `http://127.0.0.1:${port}`,
  }),
}, async ({ base, person, check }) => {
  const call = (page, method, p, body) => page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, credentials: "include", body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [method, p, body]);

  const A = await person("admin");
  await A.page.goto(base);
  await A.page.getByLabel("Username").fill("root");
  await A.page.getByLabel("Your name").fill("Admin");
  await A.page.getByLabel("Password", { exact: true }).fill(PW);
  await A.page.getByLabel("Password again").fill(PW);
  await A.page.getByRole("button", { name: "Create administrator" }).click();
  await A.page.waitForSelector("text=Admin (administrator)");
  await call(A.page, "POST", "/users", { username: "forgetful", password: PW, display_name: "Fiona" });

  // the administrator gives Fiona an e-mail address in the People dialog
  await A.page.getByRole("button", { name: /Admin \(administrator\)/ }).click();
  await A.page.getByRole("menuitem", { name: "People…" }).click();
  const box = A.page.getByLabel("e-mail of forgetful");
  await box.fill("Fiona@College.test");
  await box.blur();
  await A.page.waitForTimeout(600);
  check("the address is saved, in lower case", (await call(A.page, "GET", "/users")).body.find((u) => u.username === "forgetful").email === "fiona@college.test");

  // Fiona forgets her password
  const F = await person("fiona");
  await F.page.goto(base);
  await F.page.getByRole("button", { name: "Forgot your password?" }).click();
  await F.page.getByLabel("Username or e-mail").fill("fiona@college.test");
  await F.page.getByRole("button", { name: "Send the link" }).click();
  await F.page.waitForSelector("text=is on its way");
  check("she is told a link is on its way", true);
  for (let i = 0; i < 40 && sink.messages.length === 0; i++) await new Promise((r) => setTimeout(r, 250));
  check("one e-mail arrived", sink.messages.length === 1);
  const mail = sink.messages[0] ?? "";
  check("it is addressed to her and says who it is for", /To: fiona@college\.test/i.test(mail) && /Hello Fiona/.test(mail));
  const link = /(http:\/\/127\.0\.0\.1:\d+\/#\/reset\/[A-Za-z0-9_-]+)/.exec(mail)?.[1];
  check("it holds a link back to the app", !!link, String(link).slice(0, 50));

  // a name that does not exist gets the same answer and no mail
  const G = await person("ghost");
  await G.page.goto(base);
  await G.page.getByRole("button", { name: "Forgot your password?" }).click();
  await G.page.getByLabel("Username or e-mail").fill("nobody-at-all");
  await G.page.getByRole("button", { name: "Send the link" }).click();
  await G.page.waitForSelector("text=is on its way");
  await new Promise((r) => setTimeout(r, 800));
  check("an unknown name gets the same answer and no e-mail", sink.messages.length === 1);

  // she opens the link and chooses a new password
  await F.page.goto(link);
  await F.page.getByLabel("New password", { exact: true }).fill(NEW);
  await F.page.getByLabel("New password again").fill(NEW);
  check("the secret is removed from the address bar", !F.page.url().includes("reset"));
  await F.page.getByRole("button", { name: "Change password" }).click();
  await F.page.waitForSelector("text=Your password was changed");
  await F.page.getByRole("button", { name: "Go to sign in" }).click();
  await F.page.getByLabel("Username").fill("forgetful");
  await F.page.getByLabel("Password").fill(NEW);
  await F.page.getByRole("button", { name: "Sign in" }).click();
  await F.page.waitForSelector("text=Fiona");
  check("she signs in with the new password", true);
  check("the old password no longer works", (await fetch(base + "/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "forgetful", password: PW }) })).status === 401);

  // the link works once
  const again = await fetch(base + "/auth/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: link.split("/#/reset/")[1], new: "another long passphrase" }) });
  check("the link cannot be used a second time", again.status === 400);

  // the administrator can see it in the activity log, without any secret
  await A.page.keyboard.press("Escape");
  const log = await call(A.page, "GET", "/audit?limit=200");
  const text = JSON.stringify(log.body);
  check("the activity log records the request and the reset", /asked for a password-reset e-mail/.test(text) && /chose a new password from an e-mailed link/.test(text));
  check("and holds neither the link nor the password", !text.includes(link.split("/#/reset/")[1]) && !text.includes(NEW));
});
sink.stop();
