// The single-user app: back up everything from the header, change things, then restore the backup and see the earlier state come back.
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { journey } from "../lib.mjs";

await journey("backup_single_user", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Kept in the backup";
  await api.post("/institutions", sample);

  const { page } = await person("user");
  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Kept in the backup" }).waitFor();
  await page.getByRole("button", { name: "Back up…" }).click();
  const href = await page.getByRole("link", { name: "Download a backup" }).getAttribute("href");
  const bytes = await page.evaluate(async (u) => Array.from(new Uint8Array(await (await fetch(u)).arrayBuffer())), href);
  check("the backup is a database file", String.fromCharCode(...bytes.slice(0, 15)) === "SQLite format 3", `${bytes.length} bytes`);
  const file = path.join(mkdtempSync(path.join(tmpdir(), "aa-bk-")), "backup.db");
  writeFileSync(file, Buffer.from(bytes));

  // change things after the backup
  sample.name = "Added after the backup";
  await api.post("/institutions", sample);
  check("both institutions exist now", (await api.get("/institutions")).length === 2);

  // choose the backup: the dialog says what it holds before anything is replaced
  await page.getByLabel("backup file").setInputFiles(file);
  await page.getByText(/This backup holds 1 institution/).waitFor({ timeout: 20000 });
  check("it says what the backup holds, and nothing changed yet", (await api.get("/institutions")).length === 2);

  // a file that is not a backup is refused with a plain message
  const junk = path.join(path.dirname(file), "notes.db");
  writeFileSync(junk, "just some text");
  await page.getByLabel("backup file").setInputFiles(junk);
  await page.getByRole("alert").waitFor();
  check("a file that is not a backup is refused", /not a backup/.test(await page.getByRole("alert").innerText()));

  await page.getByLabel("backup file").setInputFiles(file);
  await page.getByRole("button", { name: "Restore this backup" }).click();  // the confirmation is accepted by the test browser
  await page.getByText(/Restored:/).waitFor({ timeout: 30000 });
  const after = await api.get("/institutions");
  check("the later changes are gone and the backed-up institution is back", after.length === 1 && after[0].name === "Kept in the backup", after.map((i) => i.name).join(", "));
  await page.locator(".inst-list button", { hasText: "Kept in the backup" }).waitFor({ timeout: 15000 });
  check("the list shows only the backed-up institution after reloading", (await page.locator(".inst-list button").count()) === 1);
});
