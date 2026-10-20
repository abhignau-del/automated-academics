// Lock a session, regenerate and see it stay put; unlock; and see the reason when no timetable can be made.
import { journey } from "../lib.mjs";

await journey("locking_and_reasons", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Pins sample";
  const { id: iid } = await api.post("/institutions", sample);
  const { page } = await person("user");

  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Pins sample" }).click();
  await page.locator("label", { hasText: "Time limit" }).locator("select").selectOption("10");
  await page.getByRole("button", { name: /Generate timetable/ }).click();
  await page.waitForSelector(".card", { timeout: 120000 });

  // lock one session
  await page.locator(".card").first().click();
  await page.getByRole("button", { name: /Lock in place/ }).click();
  await page.waitForSelector(".card.locked");
  check("the card shows as locked", (await page.locator(".card.locked").count()) >= 1);
  check("a locked card can't be dragged", (await page.locator(".card.locked").first().getAttribute("draggable")) === "false");
  const saved = await api.get(`/institutions/${iid}`);
  check("the pin is saved on the server", saved.pins.length === 1, JSON.stringify(saved.pins[0]));
  check("locking does not make the timetable stale", (await page.locator(".alert.stale").count()) === 0);
  check("the room can't be changed while it is locked", await page.locator(".side select").isDisabled());
  const pin = saved.pins[0];

  // the Data tab lists it
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Locked/ }).click();
  check("the Locked tab lists the pin", (await page.locator(".pinlist li").count()) === 1);
  await page.getByRole("tab", { name: /^Timetable/ }).click();

  // regenerate: the pinned session stays where it was
  const firstJob = (await api.get(`/institutions/${iid}/latest-job`)).id;
  await page.getByRole("button", { name: /Regenerate/ }).click();
  await page.getByText("Generating…").waitFor({ timeout: 10000 }).catch(() => {});
  await page.waitForFunction(() => !document.body.innerText.includes("Generating"), null, { timeout: 120000 });
  await page.waitForTimeout(500);
  const latest = await api.get(`/institutions/${iid}/latest-job`);
  check("a new timetable was generated", latest.id !== firstJob);
  const tt = await api.get(`/jobs/${latest.id}/timetable`);
  const p = tt.placements.find((x) => x.offering_id === pin.offering_id && x.session_index === pin.session_index);
  check("the locked session kept its day, lecture and room", p && p.day === pin.day && p.start === pin.start && p.room_id === pin.room_id);

  // unlock through the Data tab and save
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Locked/ }).click();
  await page.locator(".pinlist li").getByRole("button", { name: "Unlock" }).click();
  await page.getByRole("button", { name: /^Save/ }).first().click();
  await page.waitForTimeout(800);
  check("unlocking and saving removes the pin", (await api.get(`/institutions/${iid}`)).pins.length === 0);

  // a failed generate explains itself: two teachers who are each free only at the same lecture of one class
  const tiny = {
    name: "Clash explainer", calendar: { day_names: ["Mon"], lectures_per_day: 2, break_after: [] },
    rooms: [{ id: "R1", name: "Room 1", capacity: 40, kind: "classroom" }, { id: "R2", name: "Room 2", capacity: 40, kind: "classroom" }],
    faculty: [
      { id: "F1", name: "Teacher F1", department: "D", unavailable: [{ day: 0, lecture: 1 }], avoid: [], max_lectures_per_day: 6 },
      { id: "F2", name: "Teacher F2", department: "D", unavailable: [{ day: 0, lecture: 1 }], avoid: [], max_lectures_per_day: 6 },
    ],
    batches: [{ id: "B1", program: "P", level: "UG", semester: 1, section: "A", department: "D", strength: 30, group_of: null }],
    courses: [{ code: "C1", name: "Course 1", department: "D", credits: 3, category: "DSC", room_kind: "classroom" },
              { code: "C2", name: "Course 2", department: "D", credits: 3, category: "DSC", room_kind: "classroom" }],
    offerings: [{ id: "O1", course_code: "C1", faculty_id: "F1", batch_ids: ["B1"], sessions: [1] },
                { id: "O2", course_code: "C2", faculty_id: "F2", batch_ids: ["B1"], sessions: [1] }],
    weights: sample.weights, pins: [],
  };
  await api.post("/institutions", tiny);
  await page.reload();
  await page.locator(".inst-list button", { hasText: "Clash explainer" }).click();
  await page.getByRole("button", { name: /Generate timetable/ }).click();
  await page.waitForSelector(".alert[role=alert]", { timeout: 90000 });
  const msg = await page.locator(".alert[role=alert]").innerText();
  check("the failure names both teachers", /Teacher F1 is unavailable/.test(msg) && /Teacher F2 is unavailable/.test(msg), msg.slice(0, 120));
  check("the failure message keeps its line breaks", (await page.locator(".alert[role=alert]").evaluate((e) => getComputedStyle(e).whiteSpace)) === "pre-line");
});
