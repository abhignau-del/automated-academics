// Ask for classes to stay in one room and to avoid early/late lectures; the quality panel reports both and they improve.
import { journey } from "../lib.mjs";

await journey("room_and_hour_goals", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Goals sample";
  const { id: iid } = await api.post("/institutions", sample);
  const solved = async () => {
    const { job_id } = await api.post(`/institutions/${iid}/solve`, { time_limit_s: 20 });
    for (let i = 0; i < 120; i++) {
      const j = await api.get(`/jobs/${job_id}`);
      if (j.status === "done" || j.status === "failed") break;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return api.get(`/jobs/${job_id}/timetable`);
  };
  const before = (await solved()).breakdown;
  check("both goals are off by default and measured anyway", sample.weights.room_changes === 0 && sample.weights.odd_hours === 0 && typeof before.room_changes === "number");

  const { page } = await person("user");
  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Goals sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Settings/ }).click();
  await page.getByLabel("Same classroom").fill("10");
  await page.getByLabel("Early / late lectures", { exact: true }).fill("10");
  await page.getByLabel("first lectures counted as early").fill("1");
  await page.getByLabel("last lectures counted as late").fill("2");
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  const saved = await api.get(`/institutions/${iid}`);
  check("the weights and the early/late counts were saved", saved.weights.room_changes === 10 && saved.weights.odd_hours === 10 && saved.calendar.early_count === 1 && saved.calendar.late_count === 2);

  await page.getByRole("tab", { name: /^Timetable/ }).click();
  await page.getByRole("button", { name: /^Regenerate$/ }).click();
  await page.waitForFunction(() => !document.body.innerText.includes("Generating…"), null, { timeout: 180000 });
  await page.waitForSelector("table.quality");
  await page.waitForTimeout(500);
  const row = async (label) => Number((await page.locator("table.quality tr", { hasText: label }).locator("td.num").first().innerText()).trim());
  const shownRooms = await row("Extra classrooms");
  const shownHours = await row("Early / late lectures");
  const after = (await api.get(`/institutions/${iid}/latest-job`)).id;
  const real = (await api.get(`/jobs/${after}/timetable`)).breakdown;
  check("the quality panel shows both goals, matching the saved timetable", shownRooms === real.room_changes && shownHours === real.odd_hours, `${shownRooms}/${shownHours}`);
  check("classes use fewer extra classrooms than without the goal", real.room_changes < before.room_changes, `${before.room_changes} -> ${real.room_changes}`);
  check("and fewer early or late lectures", real.odd_hours < before.odd_hours, `${before.odd_hours} -> ${real.odd_hours}`);
});
