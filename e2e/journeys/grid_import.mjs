// Import an existing timetable from grids (ordinary and sideways), keep it, see its clashes, and replace it with a generated one.
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { journey } from "../lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const make = (dir, name, extra = []) => {
  const file = path.join(dir, name);
  execFileSync(process.env.E2E_PYTHON || "python", [path.join(here, "..", "fixtures", "make_grid_fixture.py"), file, ...extra], { stdio: "ignore" });
  return file;
};

await journey("grid_import", {}, async ({ base, person, api, check }) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "aa-grid-"));
  const ordinary = make(dir, "ordinary.xlsx");
  const sideways = make(dir, "sideways.xlsx", ["--sideways"]);
  const { page } = await person("user");
  await page.goto(base);
  const before = (await api.get("/institutions")).length;

  // ---- ordinary grids
  await page.getByRole("button", { name: /\+ New/ }).click();
  await page.getByRole("menuitem", { name: "Import an existing timetable…" }).click();
  await page.locator('input[aria-label="timetable file"]').setInputFiles(ordinary);
  await page.getByRole("button", { name: "Read the timetable" }).click();
  await page.waitForSelector(".importreport");
  const text = await page.locator(".importreport").innerText();
  check("two classes are found", /Found\s*2 classes/.test(text), text.split("\n")[0]);
  check("four lectures a day, six days", /4\s*lectures a day, Mon, Tue, Wed, Thu, Fri, Sat/.test(text));
  check("the current timetable is scored, clashes included", /Your current timetable:\s*\d+ clash/.test(text));
  check("the repeated week on the second sheet is skipped", /same class drawn again|already read/.test(text));
  check("the parallel electives are listed", /parallel elective group/.test(text));
  check("a preview saves nothing", (await api.get("/institutions")).length === before);
  await page.getByLabel("institution name").fill("Imported Econ");
  await page.getByRole("button", { name: "Create institution" }).click();
  await page.waitForSelector(".card", { timeout: 30000 });
  check("it lands on the timetable with the existing sessions", (await page.locator(".card").count()) > 0);

  const mine = (await api.get("/institutions")).find((i) => i.name === "Imported Econ");
  check("the institution was created", !!mine);
  const latest = await api.get(`/institutions/${mine.id}/latest-job`);
  const tt = await api.get(`/jobs/${latest.id}/timetable`);
  check("the existing timetable was kept as the first timetable", tt.status === "IMPORTED" && tt.placements.length > 0, `${tt.status} ${tt.placements.length}`);
  await page.getByText(/\d+ problems?$/).first().waitFor({ timeout: 15000 });
  check("the screen shows the existing clashes honestly", true);

  await page.getByRole("button", { name: "Regenerate" }).click();
  await page.getByText("No clashes", { exact: true }).first().waitFor({ timeout: 120000 });
  check("regenerating replaces it with a clash-free timetable", true);

  // ---- a grid drawn sideways reads the same way
  await page.getByRole("button", { name: /\+ New/ }).click();
  await page.getByRole("menuitem", { name: "Import an existing timetable…" }).click();
  await page.locator('input[aria-label="timetable file"]').setInputFiles(sideways);
  await page.getByRole("button", { name: "Read the timetable" }).click();
  await page.waitForSelector(".importreport");
  const side = await page.locator(".importreport").innerText();
  check("the sideways grid is found as one class", /Found\s*1 class,/.test(side), side.split("\n")[0]);
  check("with the same week and times", /4\s*lectures a day, Mon, Tue, Wed, Thu, Fri, Sat/.test(side) && /26 lectures|\d+\s*lectures a week/.test(side));
  check("and no clashes in the grid as drawn", /Your current timetable:\s*no clashes/.test(side));
});
