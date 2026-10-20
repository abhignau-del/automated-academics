// Shared mode: first-run administrator, people, sharing, a view-only person, a stale save refused, sign-out.
import { journey, PW } from "../lib.mjs";

await journey("shared_mode", { auth: true }, async ({ base, person, check }) => {
  const signIn = async (page, user, pw = PW) => {
    await page.goto(base);
    await page.getByLabel("Username").fill(user);
    await page.getByLabel("Password").fill(pw);
    await page.getByRole("button", { name: "Sign in" }).click();
  };

  // ---- first run: create the administrator
  const A = await person("admin");
  await A.page.goto(base);
  await A.page.waitForSelector("text=Create the administrator");
  await A.page.getByLabel("Username").fill("root");
  await A.page.getByLabel("Your name").fill("The Admin");
  await A.page.getByLabel("Password", { exact: true }).fill(PW);
  await A.page.getByLabel("Password again").fill(PW);
  await A.page.getByRole("button", { name: "Create administrator" }).click();
  await A.page.waitForSelector("text=The Admin (administrator)");
  check("the first visit creates an administrator and signs in", true);

  // ---- add two people through the People dialog
  await A.page.getByRole("button", { name: /The Admin/ }).click();
  await A.page.getByRole("menuitem", { name: "People…" }).click();
  for (const name of ["bobby", "carol"]) {
    await A.page.getByLabel("new username").fill(name);
    await A.page.getByLabel("new name").fill(name[0].toUpperCase() + name.slice(1));
    await A.page.getByLabel("new person's password").fill(PW);
    await A.page.getByRole("button", { name: "Add", exact: true }).click();
    await A.page.waitForSelector(`td:has-text("${name}")`);
  }
  check("two people were added", (await A.page.locator(".userstable tbody tr").count()) === 3);
  await A.page.getByRole("button", { name: "Close" }).click();

  // ---- Bobby: a wrong password, then in; creates and shares an institution
  const B = await person("bobby");
  await signIn(B.page, "bobby", "not the password");
  await B.page.waitForSelector("text=wrong username or password");
  check("a wrong password is refused with a message", true);
  await B.page.getByLabel("Password").fill(PW);
  await B.page.getByRole("button", { name: "Sign in" }).click();
  await B.page.waitForSelector("text=Bobby");
  check("Bobby sees no institutions yet", (await B.page.locator(".inst-list button").count()) === 0);
  await B.page.getByRole("button", { name: /\+ New/ }).click();
  await B.page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await B.page.waitForSelector(".inst-list button");
  await B.page.locator(".inst-list button").first().click();
  await B.page.getByRole("tab", { name: /^Timetable/ }).click();
  await B.page.getByRole("button", { name: /Generate timetable/ }).click();
  await B.page.waitForSelector(".card", { timeout: 120000 });
  await B.page.getByRole("button", { name: "Share…" }).click();
  await B.page.getByLabel("username to share with").fill("carol");
  await B.page.getByLabel("access to give").selectOption("viewer");
  await B.page.getByRole("button", { name: "Share", exact: true }).click();
  await B.page.waitForSelector('td:has-text("Carol")');
  check("it was shared with Carol as a viewer", true);
  await B.page.getByRole("button", { name: "Close" }).click();

  // ---- Carol can look, not change
  const C = await person("carol");
  await signIn(C.page, "carol");
  await C.page.waitForSelector(".inst-list button");
  await C.page.locator(".inst-list button").first().click();
  await C.page.waitForSelector(".card", { timeout: 30000 });
  check("Carol sees the shared institution and its timetable", true);
  check("she has no Data tab", (await C.page.getByRole("tab", { name: /^Data/ }).count()) === 0);
  check("she has no Share button", (await C.page.getByRole("button", { name: "Share…" }).count()) === 0);
  check("she is told it is view only", (await C.page.locator("text=View only").count()) > 0);
  check("Generate is disabled for her", await C.page.getByRole("button", { name: /Regenerate|Generate timetable/ }).isDisabled());
  check("cards can't be dragged", (await C.page.locator('.card[draggable="true"]').count()) === 0);
  check("there is no Save or Undo", (await C.page.getByRole("button", { name: /Save changes|^Undo$/ }).count()) === 0);
  const status = await C.page.evaluate(async () => {
    const list = await (await fetch("/institutions", { credentials: "include" })).json();
    return (await fetch(`/institutions/${list[0].id}`, { method: "DELETE", credentials: "include" })).status;
  });
  check("the server refuses her delete even if she forged one", status === 403, String(status));

  // ---- two editors, one stale save
  const first = B.page;
  const second = await B.ctx.newPage();
  second.on("dialog", (d) => d.accept());
  await second.goto(base);
  await second.locator(".inst-list button").first().click();
  for (const pg of [first, second]) {
    await pg.getByRole("tab", { name: /^Data/ }).click();
    await pg.waitForSelector(".editor");
  }
  await first.getByLabel("Institution name").fill("Edited by tab one");
  await first.getByRole("button", { name: "Save changes" }).click();
  await first.waitForTimeout(900);
  await second.getByLabel("Institution name").fill("Edited by tab two");
  await second.getByRole("button", { name: "Save changes" }).click();
  await second.waitForSelector("text=Someone else saved changes");
  check("a stale save is refused with an explanation", true);
  const nameNow = await second.evaluate(async () => (await (await fetch("/institutions", { credentials: "include" })).json())[0].name);
  check("the first save was not overwritten", nameNow === "Edited by tab one", nameNow);
  await second.getByRole("button", { name: /Load their version/ }).click();
  await second.waitForTimeout(800);
  check("their version can be loaded", (await second.getByLabel("Institution name").inputValue()) === "Edited by tab one");
  await second.getByLabel("Institution name").fill("Edited by tab two");
  await second.getByRole("button", { name: "Save changes" }).click();
  await second.waitForTimeout(800);
  const after = await second.evaluate(async () => (await (await fetch("/institutions", { credentials: "include" })).json())[0].name);
  check("after reloading, the second save goes through", after === "Edited by tab two", after);

  // ---- signing out ends the session everywhere
  await first.getByRole("button", { name: /Bobby/ }).click();
  await first.getByRole("menuitem", { name: "Sign out" }).click();
  await first.waitForSelector("text=Sign in");
  check("signing out returns to the sign-in form", true);
  const stillIn = await second.evaluate(async () => (await fetch("/institutions", { credentials: "include" })).status);
  check("and ends the session in every tab", stillIn === 401, String(stillIn));
});
