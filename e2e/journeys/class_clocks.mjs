// A class on its own clock: add a clock, give it to a class, see each class labelled with its own times, rename and remove.
import { journey } from "../lib.mjs";

await journey("class_clocks", {}, async ({ base, person, api, check }) => {
  const sample = await api.get("/institutions/starter?kind=sample");
  sample.name = "Clocks sample";
  const t = (a, b) => ({ start: a, end: b });
  sample.calendar.times = [0, 1, 2, 3, 4, 5, 6].map((i) => t(`${String(8 + i).padStart(2, "0")}:00`, `${String(8 + i).padStart(2, "0")}:50`));
  const { id: iid } = await api.post("/institutions", sample);
  const { page } = await person("user");

  await page.goto(base);
  await page.locator(".inst-list button", { hasText: "Clocks sample" }).click();
  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Settings/ }).click();

  await page.getByLabel("name of a new clock").fill("40-minute");
  await page.getByRole("button", { name: "Add a clock" }).click();
  await page.waitForSelector('[aria-label="name of clock 40-minute"]');
  check("the clock appears with a copy of the default times", (await page.getByLabel("40-minute lecture 1 ends").inputValue()) === "08:50");
  for (let i = 1; i <= 7; i++) await page.getByLabel(`40-minute lecture ${i} ends`).fill(`${String(7 + i).padStart(2, "0")}:40`);
  check("no class uses it yet", /0 classes use it/.test(await page.locator(".clocks").innerText()));

  // give it to a class in the Classes tab (before saving: the choice must already be offered)
  await page.getByRole("tab", { name: /^Classes/ }).click();
  const row = page.locator(".etable tbody tr:not(.subrow)").filter({ has: page.locator('input[value="MG-UG1"]') });
  await row.locator('[aria-label="clock"]').first().selectOption("40-minute");
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  const saved = await api.get(`/institutions/${iid}`);
  check("the clock was saved", Object.keys(saved.calendar.clocks).join() === "40-minute" && saved.calendar.clocks["40-minute"][0].end === "08:40");
  check("the class's clock was saved", saved.batches.find((x) => x.id === "MG-UG1").clock === "40-minute");
  check("another class is untouched", saved.batches.find((x) => x.id === "CS-UG1").clock == null);

  await page.getByRole("tab", { name: /^Timetable/ }).click();
  await page.getByRole("button", { name: /Generate timetable/ }).click();
  await page.waitForSelector(".card", { timeout: 120000 });
  const side = async () => page.locator(".grid-time small").first().innerText();
  await page.locator("select[aria-label=View]").selectOption("MG-UG1");
  await page.waitForTimeout(300);
  check("MG-UG1's side column uses its own clock", (await side()) === "8:00–8:40", await side());
  await page.locator("select[aria-label=View]").selectOption("CS-UG1");
  await page.waitForTimeout(300);
  check("CS-UG1's side column uses the default", (await side()) === "8:00–8:50", await side());

  await page.getByRole("tab", { name: /^Data/ }).click();
  await page.getByRole("tab", { name: /^Settings/ }).click();
  const name = page.getByLabel("name of clock 40-minute");
  await name.fill("short week");
  await name.press("Enter");
  await page.waitForSelector('[aria-label="name of clock short week"]');
  check("renaming keeps the class on the clock", /1 class use it/.test(await page.locator(".clocks").innerText()));
  await page.getByRole("button", { name: "Remove this clock" }).click();
  await page.getByRole("button", { name: /Save changes/ }).click();
  await page.waitForTimeout(900);
  const after = await api.get(`/institutions/${iid}`);
  check("removing the clock sends its classes back to the default", Object.keys(after.calendar.clocks).length === 0 && after.batches.every((x) => x.clock == null));
});
