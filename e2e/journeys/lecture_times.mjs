// Set lecture times, give a day its own, resize the day, see them on the grid, remove them.
import { journey } from "../lib.mjs";

await journey("lecture_times", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Times sample";
  const { id: iid } = await api.post("/institutions", sample);
  const { page } = await person("user");

  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Times sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Settings/ }).click();

  check("times start unset", (await page.getByRole("button", { name: "Set lecture times…" }).count()) === 1);
  await page.getByRole("button", { name: "Set lecture times…" }).click();
  await page.getByLabel("first lecture starts").fill("08:00");
  await page.getByLabel("minutes each").fill("50");
  await page.getByLabel("minutes between").fill("5");
  await page.getByLabel("minutes at a break").fill("20");
  await page.getByRole("button", { name: "Fill in" }).click();
  check("all seven lectures are filled in", (await page.locator(".timestable").first().locator("tbody tr").count()) === 7);
  check("lecture 1 is 08:00-08:50", (await page.getByLabel("default lecture 1 starts").inputValue()) === "08:00" && (await page.getByLabel("default lecture 1 ends").inputValue()) === "08:50");
  const l4end = await page.getByLabel("default lecture 4 ends").inputValue();
  const l5start = await page.getByLabel("default lecture 5 starts").inputValue();
  check("the break is longer than the usual gap", l5start > l4end, `${l4end} -> ${l5start}`);

  await page.getByLabel("default lecture 2 ends").fill("09:40");
  await page.getByRole("button", { name: "Sat: use different times" }).click();
  await page.getByLabel("Sat lecture 1 ends").fill("08:40");
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  let saved = await api.get(`/institutions/${iid}`);
  check("seven default times were saved", saved.calendar.times.length === 7 && saved.calendar.times[1].end === "09:40");
  check("Saturday's own times were saved", saved.calendar.day_times["5"]?.[0]?.end === "08:40");

  await page.getByLabel("lectures per day").first().fill("6");
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  saved = await api.get(`/institutions/${iid}`);
  check("with six lectures the times and Saturday's shrank too", saved.calendar.lectures_per_day === 6 && saved.calendar.times.length === 6 && saved.calendar.day_times["5"].length === 6);

  // put one session on Saturday for certain (a lock), so the check doesn't depend on where the solver happens to place things
  const inst = await api.get(`/institutions/${iid}`);
  const offering = inst.offerings.find((o) => o.id === "O-CS-UG1-T1");
  inst.pins = [{ offering_id: offering.id, session_index: 0, day: 5, start: 0, room_id: null }];
  await api.put(`/institutions/${iid}`, inst);
  await page.reload();
  await page.locator(".inst-list button", { hasText: "Times sample" }).click();
  await page.getByRole("tab", { name: /^Timetable/ }).click();
  await page.getByRole("button", { name: /Generate timetable/ }).click();
  await page.waitForSelector(".card", { timeout: 120000 });
  await page.getByRole("tab", { name: "Faculty", exact: true }).click();
  await page.locator("select[aria-label=View]").selectOption(offering.faculty_id);
  await page.waitForTimeout(300);
  check("the grid's first row shows 8:00–8:50", (await page.locator(".grid-time small").first().innerText()) === "8:00–8:50");
  check("Saturday's session carries its own time", (await page.locator(".cardtime").count()) > 0);
  await page.locator(".card").first().click();
  check("the side panel shows the time", /\d+:\d\d–\d+:\d\d/.test(await page.locator(".side").innerText()));

  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("button", { name: "Remove all lecture times" }).click();
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  saved = await api.get(`/institutions/${iid}`);
  check("times can be removed again", saved.calendar.times.length === 0 && Object.keys(saved.calendar.day_times).length === 0);
});
