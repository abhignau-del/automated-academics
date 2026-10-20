// Two offerings of the same course and teacher for different classes: suggested, combined, saved, solved and split again.
import { journey, until } from "../lib.mjs";

await journey("joint_classes", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Joint sample";
  sample.offerings = [
    { id: "J1", course_code: sample.courses[0].code, faculty_id: sample.faculty[0].id, batch_ids: [sample.batches[0].id], sessions: [1, 1, 1] },
    { id: "J2", course_code: sample.courses[0].code, faculty_id: sample.faculty[0].id, batch_ids: [sample.batches[1].id], sessions: [1, 1, 1] },
  ];
  sample.pins = [];
  const { id: iid } = await api.post("/institutions", sample);
  const { page } = await person("user");

  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Joint sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Offerings/ }).click();
  check("the panel suggests the pair", /1 suggested/.test(await page.locator(".jointpanel summary").innerText()));
  await page.locator(".jointpanel").getByRole("button", { name: "Combine" }).click();
  check("the two offerings became one", (await page.locator(".etable tbody tr:not(.subrow)").count()) === 1);
  const summary = await page.locator(".jointpanel summary").innerText();
  check("the panel now shows one shared and no suggestions", /1 shared now/.test(summary) && !/suggested/.test(summary));
  await page.getByRole("button", { name: /^Save/ }).first().click();
  await page.waitForTimeout(800);
  let saved = await api.get(`/institutions/${iid}`);
  check("saved as one offering with two classes", saved.offerings.length === 1 && saved.offerings[0].batch_ids.length === 2);

  const { job_id } = await api.post(`/institutions/${iid}/solve`, { time_limit_s: 10 });
  const job = await until(async () => { const j = await api.get(`/jobs/${job_id}`); return ["done", "failed"].includes(j.status) ? j : null; });
  const tt = await api.get(`/jobs/${job_id}/timetable`);
  check("it generates, once for both classes", job.status === "done" && tt.placements.length === 3, job.status);

  await page.reload();
  await page.locator(".inst-list button", { hasText: "Joint sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Offerings/ }).click();
  await page.locator('input[aria-label="select row 1"]').check();
  await page.getByRole("button", { name: "Split joint class" }).click();
  check("splitting gives two offerings again", (await page.locator(".etable tbody tr:not(.subrow)").count()) === 2);
  await page.getByRole("button", { name: /^Save/ }).first().click();
  await page.waitForTimeout(800);
  saved = await api.get(`/institutions/${iid}`);
  check("saved as two single-class offerings", saved.offerings.length === 2 && saved.offerings.every((o) => o.batch_ids.length === 1));
});
