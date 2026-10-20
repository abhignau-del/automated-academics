// A coordinator limited to one department: what is open to them, what is locked, and what the server refuses.
import { journey, PW } from "../lib.mjs";

await journey("department_scope", { auth: true }, async ({ base, person, check }) => {
  const signIn = async (page, user) => {
    await page.goto(base);
    await page.getByLabel("Username").fill(user);
    await page.getByLabel("Password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
  };
  const call = (page, method, p, body) => page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, credentials: "include", body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [method, p, body]);

  // administrator and two people (the screens for this are covered by the shared_mode journey)
  const A = await person("admin");
  await A.page.goto(base);
  await A.page.getByLabel("Username").fill("root");
  await A.page.getByLabel("Your name").fill("Admin");
  await A.page.getByLabel("Password", { exact: true }).fill(PW);
  await A.page.getByLabel("Password again").fill(PW);
  await A.page.getByRole("button", { name: "Create administrator" }).click();
  await A.page.waitForSelector("text=Admin (administrator)");
  for (const u of ["owenowner", "coralcs"]) await call(A.page, "POST", "/users", { username: u, password: PW, display_name: u });

  // the owner creates an institution and shares it with a CS-only editor through the dialog
  const O = await person("owner");
  await signIn(O.page, "owenowner");
  await O.page.getByRole("button", { name: /\+ New/ }).click();
  await O.page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await O.page.waitForSelector(".inst-list button");
  await O.page.locator(".inst-list button").first().click();
  await O.page.getByRole("tab", { name: /^Timetable/ }).click();
  await O.page.getByRole("button", { name: /Generate timetable/ }).click();
  await O.page.waitForSelector(".card", { timeout: 120000 });
  await O.page.getByRole("button", { name: "Share…" }).click();
  await O.page.getByLabel("username to share with").fill("coralcs");
  await O.page.getByLabel("access to give").selectOption("editor");
  check("the sample's departments are offered", (await O.page.locator(".deptpick").getByLabel("MG").count()) === 1);
  await O.page.locator(".deptpick").getByLabel("CS").check();
  await O.page.getByRole("button", { name: "Share", exact: true }).click();
  await O.page.waitForSelector('summary:has-text("Only CS")');
  check("the share dialog shows the limit", true);

  // the coordinator
  const C = await person("coral");
  await signIn(C.page, "coralcs");
  await C.page.locator(".inst-list button").first().click();
  await C.page.waitForSelector(".card", { timeout: 30000 });
  check("she sees the timetable", true);
  check("Generate is disabled for a limited editor", await C.page.getByRole("button", { name: /Regenerate|Generate timetable/ }).isDisabled());
  const movable = await C.page.locator('.card[draggable="true"]').count();
  const fixed = await C.page.locator('.card[draggable="false"]').count();
  check("some cards can be moved and some can't (the joint class)", movable > 0 && fixed > 0, `${movable} movable, ${fixed} fixed`);
  await C.page.locator("select[aria-label=View]").selectOption("MG-UG1");
  await C.page.waitForTimeout(300);
  check("another department's sessions can't be dragged", (await C.page.locator('.card[draggable="true"]').count()) === 0);
  await C.page.locator("select[aria-label=View]").selectOption("CS-UG3");
  await C.page.waitForTimeout(300);
  check("her own department's sessions can", (await C.page.locator('.card[draggable="true"]').count()) > 0);

  await C.page.getByRole("tab", { name: /^Data/ }).click();
  await C.page.waitForSelector(".scopenote");
  check("a note says what she may change", /CS/.test(await C.page.locator(".scopenote").innerText()));
  check("the settings are locked", await C.page.getByLabel("Institution name").isDisabled());
  await C.page.getByRole("tab", { name: /^Faculty/ }).click();
  const locked = await C.page.locator("tr.locked").count();
  const open = await C.page.locator(".etable tbody tr:not(.locked):not(.subrow)").count();
  check("MG teachers are locked and CS teachers are not", locked === 4 && open === 4, `${locked} locked, ${open} editable`);
  await C.page.getByRole("tab", { name: /^Rooms/ }).click();
  check("rooms can't be added", await C.page.getByRole("button", { name: /Add room/ }).isDisabled());

  // edit one of her own teachers on screen and save
  await C.page.getByRole("tab", { name: /^Faculty/ }).click();
  await C.page.locator(".etable tbody tr:not(.locked):not(.subrow)").first().locator('input[aria-label="name"]').fill("Dr Coordinator");
  await C.page.getByRole("button", { name: /Save changes/ }).click();
  await C.page.waitForTimeout(900);
  const list = await call(C.page, "GET", "/institutions");
  const iid = list.body[0].id;
  const inst = (await call(C.page, "GET", `/institutions/${iid}`)).body;
  check("her own change was saved", inst.faculty.some((f) => f.name === "Dr Coordinator"));

  // and the server refuses anything else, whatever the screen shows
  inst.faculty = inst.faculty.map((f) => (f.department === "MG" ? { ...f, name: "Tampered" } : f));
  const refused = await call(C.page, "PUT", `/institutions/${iid}`, inst);
  check("the server refuses a change to another department", refused.status === 403 && refused.body.detail.scoped === true, String(refused.status));
  check("and says what was not allowed", /MG/.test(refused.body.detail.message), refused.body.detail.message.slice(0, 90));
  check("and refuses generating", (await call(C.page, "POST", `/institutions/${iid}/solve`, { time_limit_s: 5 })).status === 403);
});
