// A class lives in a named room: set it in the Classes table, generate, and every classroom lecture of that class is there;
// moving one out is reported; removing the room sends the class back to "any room".
import { journey } from "../lib.mjs";

await journey("home_rooms", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Home rooms sample";
  const { id: iid } = await api.post("/institutions", sample);
  const { page } = await person("user");

  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Home rooms sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Classes/ }).click();
  const row = (id) => page.locator(".etable tbody tr:not(.subrow)").filter({ has: page.locator(`input[value="${id}"]`) });
  const options = await row("MG-UG1").locator('[aria-label="home room"]').first().locator("option").allInnerTexts();
  check("the choices are classrooms and the hall, not labs", options.some((o) => /Classroom 1/.test(o)) && !options.some((o) => /Lab/.test(o)), options.join(" | "));
  await row("MG-UG1").locator('[aria-label="home room"]').first().selectOption("C3");
  await row("CS-PG1").locator('[aria-label="home room"]').first().selectOption("C2");
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  const saved = await api.get(`/institutions/${iid}`);
  check("the home rooms were saved", saved.batches.find((b) => b.id === "MG-UG1").home_room === "C3" && saved.batches.find((b) => b.id === "CS-PG1").home_room === "C2");
  check("another class is untouched", saved.batches.find((b) => b.id === "CS-UG1").home_room == null);

  await page.getByRole("tab", { name: /^Timetable/ }).click();
  await page.getByRole("button", { name: /Generate timetable/ }).click();
  await page.waitForSelector(".card", { timeout: 120000 });
  const job = (await api.get(`/institutions/${iid}/latest-job`)).id;
  const tt = await api.get(`/jobs/${job}/timetable`);
  const kind = (o) => saved.courses.find((c) => c.code === saved.offerings.find((x) => x.id === o).course_code).room_kind;
  const forClass = (id) => tt.placements.filter((p) => kind(p.offering_id) === "classroom" && saved.offerings.find((x) => x.id === p.offering_id).batch_ids.length === 1
    && saved.offerings.find((x) => x.id === p.offering_id).batch_ids[0] === id);
  const mg = forClass("MG-UG1"), pg = forClass("CS-PG1");
  check("MG-UG1 has classroom lectures and every one is in Classroom 3", mg.length > 0 && mg.every((p) => p.room_id === "C3"), `${mg.length} lectures`);
  check("CS-PG1's are all in Classroom 2", pg.length > 0 && pg.every((p) => p.room_id === "C2"), `${pg.length} lectures`);
  check("the timetable has no clashes and no home-room breaches", (await api.post(`/institutions/${iid}/validate`, tt)).ok === true);

  // a manual edit that takes a lecture out of the home room is reported
  const moved = tt.placements.map((p) => (p === mg[0] || (p.offering_id === mg[0].offering_id && p.session_index === mg[0].session_index) ? { ...p, room_id: "C4" } : p));
  const report = await api.post(`/institutions/${iid}/validate`, { ...tt, placements: moved });
  check("moving a lecture out of the home room is reported", report.conflicts.some((c) => /lives in Classroom 3/.test(c)), report.conflicts[0]);

  // removing the room sends the class back to "any room"
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Rooms/ }).click();
  const roomRow = page.locator(".etable tbody tr").filter({ has: page.locator('input[value="C3"]') });
  await roomRow.locator('[aria-label^="delete row"]').click();
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  const after = await api.get(`/institutions/${iid}`);
  check("deleting the room frees the class from it", !after.rooms.some((r) => r.id === "C3") && after.batches.find((b) => b.id === "MG-UG1").home_room == null);
});
