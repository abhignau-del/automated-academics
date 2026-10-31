// Ask for a course's sessions to be kept on separate, non-consecutive days; the quality panel reports it and it improves.
import { journey } from "../lib.mjs";

await journey("course_spacing", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Spacing sample";
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
  check("the goal is off by default and measured anyway", sample.weights.day_spacing === 0 && typeof before.day_spacing === "number", `${before.day_spacing}`);

  const { page } = await person("user");
  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Spacing sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Settings/ }).click();
  await page.getByLabel("Course spacing").fill("10");
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  check("the weight was saved", (await api.get(`/institutions/${iid}`)).weights.day_spacing === 10);

  await page.getByRole("tab", { name: /^Timetable/ }).click();
  await page.getByRole("button", { name: /^Regenerate$/ }).click();
  await page.waitForFunction(() => !document.body.innerText.includes("Generating…"), null, { timeout: 180000 });
  await page.waitForSelector("table.quality");
  await page.waitForTimeout(500);
  const shown = Number((await page.locator("table.quality tr", { hasText: "Back-to-back days" }).locator("td.num").first().innerText()).trim());
  const after = (await api.get(`/institutions/${iid}/latest-job`)).id;
  const real = (await api.get(`/jobs/${after}/timetable`)).breakdown;
  check("the quality panel shows it, matching the saved timetable", shown === real.day_spacing, `${shown}`);
  check("courses meet on fewer consecutive days than without the goal", real.day_spacing < before.day_spacing, `${before.day_spacing} -> ${real.day_spacing}`);
});
