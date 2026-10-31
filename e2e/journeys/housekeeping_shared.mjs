// Shared mode: the administrator trims the activity log and backs up / restores; the owner sees when a published link was last opened.
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { journey, PW } from "../lib.mjs";

await journey("housekeeping_shared", { auth: true }, async ({ base, person, check }) => {
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
  await call(A.page, "POST", "/users", { username: "owenowner", password: PW, display_name: "Owen" });

  // trimming the log
  await A.page.getByRole("button", { name: /Admin \(administrator\)/ }).click();
  await A.page.getByRole("menuitem", { name: "Activity log…" }).click();
  await A.page.getByLabel("days to keep").fill("10");
  check("the button is off below 30 days", await A.page.getByRole("button", { name: "Delete older entries" }).isDisabled());
  await A.page.getByLabel("days to keep").fill("60");
  await A.page.getByRole("button", { name: "Delete older entries" }).click(); // confirmation accepted by the test browser
  await A.page.getByText(/Deleted 0 entries/).waitFor({ timeout: 15000 });
  check("with nothing that old, it deletes nothing and says so", true);
  await A.page.getByRole("button", { name: "Verify integrity" }).click();
  await A.page.getByText(/^Intact/).waitFor({ timeout: 15000 });
  check("the log still verifies", true);
  await A.page.getByRole("button", { name: "Close" }).click();

  // the owner makes an institution and publishes it
  const O = await person("owner");
  await O.page.goto(base);
  await O.page.getByLabel("Username").fill("owenowner");
  await O.page.getByLabel("Password").fill(PW);
  await O.page.getByRole("button", { name: "Sign in" }).click();
  await O.page.getByRole("button", { name: /\+ New/ }).click();
  await O.page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await O.page.waitForSelector(".inst-list button");
  const iid = (await call(O.page, "GET", "/institutions")).body[0].id;
  const job = (await call(O.page, "POST", `/institutions/${iid}/solve`, { time_limit_s: 6 })).body.job_id;
  for (let i = 0; i < 90; i++) { if ((await call(O.page, "GET", `/jobs/${job}`)).body.status === "done") break; await O.page.waitForTimeout(1000); }
  await O.page.reload();
  await O.page.locator(".inst-list button").first().click();
  await O.page.getByRole("tab", { name: /^Timetable/ }).click();
  await O.page.waitForSelector(".card", { timeout: 60000 });
  await O.page.getByRole("button", { name: "Publish…" }).click();
  await O.page.getByLabel("link name").fill("Semester 1");
  await O.page.getByRole("button", { name: "Create link" }).click();
  const link = await O.page.getByLabel("link Semester 1").inputValue();
  check("a new link says it was never opened", /Never opened/.test(await O.page.getByLabel("last opened Semester 1").innerText()));

  const S = await person("student");
  await S.page.goto(link);
  await S.page.waitForSelector(".card", { timeout: 30000 });
  await O.page.getByRole("button", { name: "Close" }).click();
  await O.page.getByRole("button", { name: "Publish…" }).click();
  check("after a student opens it, it says it was just opened", /Last opened just now/.test(await O.page.getByLabel("last opened Semester 1").innerText()));
  await O.page.getByRole("button", { name: "Close" }).click();

  // backup and restore as the administrator
  await A.page.getByRole("button", { name: /Admin \(administrator\)/ }).click();
  await A.page.getByRole("menuitem", { name: "Back up and restore…" }).click();
  const href = await A.page.getByRole("link", { name: "Download a backup" }).getAttribute("href");
  const bytes = await A.page.evaluate(async (u) => Array.from(new Uint8Array(await (await fetch(u, { credentials: "include" })).arrayBuffer())), href);
  check("the backup is a database file", String.fromCharCode(...bytes.slice(0, 15)) === "SQLite format 3", `${bytes.length} bytes`);
  check("an owner cannot download a backup", (await call(O.page, "GET", "/admin/backup")).status === 403);
  const file = path.join(mkdtempSync(path.join(tmpdir(), "aa-bk-")), "backup.db");
  writeFileSync(file, Buffer.from(bytes));

  await call(O.page, "DELETE", `/institutions/${iid}`);
  check("the owner's institution is gone", (await call(O.page, "GET", "/institutions")).body.length === 0);

  await A.page.getByLabel("backup file").setInputFiles(file);
  await A.page.getByText(/This backup holds 1 institution/).waitFor({ timeout: 20000 });
  await A.page.getByRole("button", { name: "Restore this backup" }).click();
  await A.page.getByText(/Restored:/).waitFor({ timeout: 30000 });
  await A.page.waitForSelector('input[type="password"]', { timeout: 20000 }); // everyone is signed out and must sign in again
  check("after restoring, the administrator has to sign in again", true);
  await A.page.getByLabel("Username").fill("root");
  await A.page.getByLabel("Password").fill(PW);
  await A.page.getByRole("button", { name: "Sign in" }).click();
  await A.page.waitForSelector("text=Admin (administrator)");
  check("and the owner's institution is back", (await call(A.page, "GET", "/institutions")).body.length === 1);
  check("the restore is in the activity log", JSON.stringify((await call(A.page, "GET", "/audit?limit=20")).body).includes("backup.restored"));
});
