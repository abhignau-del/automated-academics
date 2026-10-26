// A published link offers a calendar file once the term dates are set, and tells students what changed when it is pointed at a newer timetable.
import { journey, PW } from "../lib.mjs";

await journey("calendar_and_changes", { auth: true }, async ({ base, person, check }) => {
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

  const O = await person("owner");
  await O.page.goto(base);
  await O.page.getByLabel("Username").fill("owenowner");
  await O.page.getByLabel("Password").fill(PW);
  await O.page.getByRole("button", { name: "Sign in" }).click();
  await O.page.getByRole("button", { name: /\+ New/ }).click();
  await O.page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await O.page.waitForSelector(".inst-list button");
  const iid = (await call(O.page, "GET", "/institutions")).body[0].id;

  // lecture times (a calendar needs clock times), then a timetable
  const inst = (await call(O.page, "GET", `/institutions/${iid}`)).body;
  inst.calendar.times = Array.from({ length: inst.calendar.lectures_per_day }, (_, i) => ({ start: `${String(9 + i).padStart(2, "0")}:00`, end: `${String(9 + i).padStart(2, "0")}:50` }));
  check("lecture times were saved", (await call(O.page, "PUT", `/institutions/${iid}`, inst)).status === 200);
  const job = (await call(O.page, "POST", `/institutions/${iid}/solve`, { time_limit_s: 6 })).body.job_id;
  for (let i = 0; i < 90; i++) { if ((await call(O.page, "GET", `/jobs/${job}`)).body.status === "done") break; await O.page.waitForTimeout(1000); }
  await O.page.reload();
  await O.page.locator(".inst-list button").first().click();
  await O.page.getByRole("tab", { name: /^Timetable/ }).click();
  await O.page.waitForSelector(".card", { timeout: 60000 });

  // publish with term dates
  await O.page.getByRole("button", { name: "Publish…" }).click();
  await O.page.getByLabel("link name").fill("Semester 1");
  await O.page.getByLabel("term start", { exact: true }).fill("2026-11-02");
  await O.page.getByLabel("term end", { exact: true }).fill("2027-02-27");
  await O.page.getByRole("button", { name: "Create link" }).click();
  const link = await O.page.getByLabel("link Semester 1").inputValue();
  check("the link remembers the term dates", (await O.page.getByLabel("term start of Semester 1").inputValue()) === "2026-11-02");
  check("and says students can add it to a calendar", await O.page.getByText("Students can add it to their calendar.").count() === 1);

  // a student adds it to a calendar
  const S = await person("student");
  await S.page.goto(link);
  await S.page.waitForSelector(".card", { timeout: 30000 });
  const subscribe = await S.page.getByRole("link", { name: "Add to my calendar" }).getAttribute("href");
  check("there is an 'Add to my calendar' link that subscribes (webcal)", /^webcal:\/\/.+\/public\/.+\/calendar\.ics\?kind=batch&id=/.test(subscribe ?? ""), String(subscribe).slice(0, 60));
  const file = await S.page.getByRole("link", { name: "Download .ics file" }).getAttribute("href");
  const text = await S.page.evaluate(async (u) => (await fetch(u)).text(), file);
  check("the file is a calendar with weekly repeating events", text.startsWith("BEGIN:VCALENDAR") && /RRULE:FREQ=WEEKLY;UNTIL=20270227T235959/.test(text) && (text.match(/BEGIN:VEVENT/g) ?? []).length > 3);
  check("no update notice before anything changed", (await S.page.getByText("Timetable updated").count()) === 0);

  // the owner lists a holiday week; the calendar file then leaves it out and the page says so
  await O.page.getByLabel("break from for Semester 1").fill("2026-11-16");
  await O.page.getByLabel("break to for Semester 1").fill("2026-11-22");
  await O.page.getByLabel("break name for Semester 1").fill("Diwali week");
  await O.page.getByRole("button", { name: "Add break" }).click();
  await O.page.waitForSelector("li:has-text('Diwali week')");
  check("the break is listed under the link", (await O.page.locator("li:has-text('Diwali week')").count()) === 1);
  await S.page.reload();
  await S.page.waitForSelector(".card", { timeout: 30000 });
  check("the page lists the break for students", /Diwali week/.test(await S.page.getByLabel("no lectures").innerText()));
  const withBreak = await S.page.evaluate(async (u) => (await fetch(u)).text(), file);
  const events = (withBreak.match(/BEGIN:VEVENT/g) ?? []).length;
  check("every event now skips that week", (withBreak.match(/EXDATE:2026111[6-9]T|EXDATE:2026112[0-2]T/g) ?? []).length === events && events > 3, `${events} events`);
  await O.page.getByRole("button", { name: /remove break/ }).click();
  await O.page.waitForSelector("text=none");
  check("a break can be removed", !/EXDATE/.test(await S.page.evaluate(async (u) => (await fetch(u)).text(), file)));

  // the owner makes a changed timetable (one session moved, one dropped) and points the link at it
  const full = (await call(O.page, "GET", `/jobs/${job}/timetable`)).body.placements;
  const days = inst.calendar.day_names.length;
  const moved = { ...full[0], day: (full[0].day + 1) % days };
  const changed = (await call(O.page, "POST", `/institutions/${iid}/timetable/imported`, { placements: [moved, ...full.slice(1, -1)] })).body.job_id;
  check("a changed timetable was kept", !!changed);
  await O.page.reload();
  await O.page.locator(".inst-list button").first().click();
  await O.page.getByRole("tab", { name: /^Timetable/ }).click();
  await O.page.waitForSelector(".card", { timeout: 60000 });
  await O.page.getByRole("button", { name: "Publish…" }).click();
  await O.page.getByLabel("note for Semester 1").fill("Lab moves to Tuesday");
  await O.page.getByRole("button", { name: "Show the current timetable" }).click();
  await O.page.waitForTimeout(700);

  // the student sees what changed, on the class that moved
  const offering = inst.offerings.find((o) => o.id === full[0].offering_id);
  await S.page.goto(`${link}/batch/${encodeURIComponent(offering.batch_ids[0])}`);
  await S.page.reload();
  await S.page.waitForSelector("text=Timetable updated", { timeout: 30000 });
  const banner = await S.page.locator(".alert.note").innerText();
  check("the notice carries the publisher's note", /Lab moves to Tuesday/.test(banner), banner.slice(0, 80));
  check("the moved lecture is marked", (await S.page.getByText("New or moved").count()) >= 1);

  // another class that did not change says so
  const other = inst.batches.map((b) => b.id).find((b) => !offering.batch_ids.includes(b) && !full.slice(-1).some((p) => inst.offerings.find((o) => o.id === p.offering_id)?.batch_ids.includes(b)));
  if (other) {
    await S.page.goto(`${link}/batch/${encodeURIComponent(other)}`);
    await S.page.reload();
    await S.page.waitForSelector("text=Timetable updated", { timeout: 30000 });
    check("a class that did not change is told so", /Nothing changed for this class/.test(await S.page.locator(".alert.note").innerText()));
  }
});
