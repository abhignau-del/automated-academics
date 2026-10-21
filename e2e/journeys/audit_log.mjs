// The activity log: an owner sees their institution's history, an administrator sees everything and can verify it,
// and a viewer has no History button at all.
import { journey, PW } from "../lib.mjs";

await journey("audit_log", { auth: true }, async ({ base, person, check }) => {
  const call = (page, method, p, body) => page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, credentials: "include", body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [method, p, body]);
  const signIn = async (who, name) => {
    const X = await person(who);
    await X.page.goto(base);
    await X.page.getByLabel("Username").fill(name);
    await X.page.getByLabel("Password").fill(PW);
    await X.page.getByRole("button", { name: "Sign in" }).click();
    return X;
  };

  const A = await person("admin");
  await A.page.goto(base);
  await A.page.getByLabel("Username").fill("root");
  await A.page.getByLabel("Your name").fill("Admin");
  await A.page.getByLabel("Password", { exact: true }).fill(PW);
  await A.page.getByLabel("Password again").fill(PW);
  await A.page.getByRole("button", { name: "Create administrator" }).click();
  await A.page.waitForSelector("text=Admin (administrator)");
  await call(A.page, "POST", "/users", { username: "owenowner", password: PW, display_name: "Owen" });
  await call(A.page, "POST", "/users", { username: "vickview", password: PW, display_name: "Vick" });

  // the owner creates an institution, shares it with a viewer and generates a timetable
  const O = await signIn("owner", "owenowner");
  await O.page.getByRole("button", { name: /\+ New/ }).click();
  await O.page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await O.page.waitForSelector(".inst-list button");
  await O.page.locator(".inst-list button").first().click();
  const iid = (await call(O.page, "GET", "/institutions")).body[0].id;
  await call(O.page, "PUT", `/institutions/${iid}/members/vickview`, { role: "viewer" });
  await O.page.getByRole("tab", { name: /^Timetable/ }).click();
  await O.page.getByRole("button", { name: /Generate timetable/ }).click();
  await O.page.waitForSelector(".card", { timeout: 120000 });

  await O.page.getByRole("button", { name: "History…" }).click();
  const dialog = O.page.getByRole("dialog", { name: "Activity log" });
  await dialog.locator("tbody tr").first().waitFor();
  const text = await dialog.innerText();
  check("the owner sees that Owen created the institution", /Owen created the institution/.test(text));
  check("and asked for a timetable, and that it finished", /Owen asked for a new timetable/.test(text) && /The solver finished a timetable/.test(text));
  check("and who was given access", /gave vickview viewer access/.test(text));
  check("an institution's history has no 'Where' column", (await dialog.locator("th", { hasText: "Where" }).count()) === 0);
  check("no password or token appears anywhere", !text.includes(PW));
  await dialog.getByLabel("Kind of activity").selectOption({ label: "Who has access" });
  await O.page.waitForTimeout(700);
  const filtered = await dialog.locator("tbody tr").allInnerTexts();
  check("choosing 'Who has access' leaves only that", filtered.length === 1 && /viewer access/.test(filtered[0]), filtered.join(" | ").slice(0, 80));
  const csv = await dialog.getByRole("link", { name: "Download CSV" }).getAttribute("href");
  const file = await O.page.evaluate(async (u) => (await fetch(u, { credentials: "include" })).text(), csv);
  check("the CSV holds the same rows", file.includes("viewer access") && !file.includes("created the institution"));
  await dialog.getByRole("button", { name: "Close" }).click();

  // a viewer has no History button, and the server refuses them too
  const V = await signIn("viewer", "vickview");
  await V.page.waitForSelector(".inst-list button");
  await V.page.locator(".inst-list button").first().click();
  await V.page.waitForSelector("text=View only");
  check("a viewer has no History button", (await V.page.getByRole("button", { name: "History…" }).count()) === 0);
  check("and the server refuses them", (await call(V.page, "GET", `/institutions/${iid}/audit`)).status === 403);

  // the administrator sees everything, including the sign-ins, and can verify the log
  await A.page.getByRole("button", { name: /Admin \(administrator\)/ }).click();
  await A.page.getByRole("menuitem", { name: "Activity log…" }).click();
  const all = A.page.getByRole("dialog", { name: "Activity log" });
  await all.locator("tbody tr").first().waitFor();
  const everything = await all.innerText();
  check("the administrator sees sign-ins as well", /signed in/.test(everything) && /Where/.test(everything));
  check("and the first set-up", /set up the first administrator/.test(everything));
  await all.getByRole("button", { name: "Verify integrity" }).click();
  await all.locator("text=/Intact: all \\d+ entries/").waitFor();
  check("the log verifies as intact", true);
  await all.getByLabel("Kind of activity").selectOption({ label: "Signing in" });
  await A.page.waitForTimeout(700);
  const signins = await all.locator("tbody tr").allInnerTexts();
  check("filtering to sign-ins hides data changes", signins.length > 0 && signins.every((t) => /sign|set up|password/i.test(t)));
});
