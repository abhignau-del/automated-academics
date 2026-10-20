// The core path: create from the sample, generate, look at the views, and download the PDF and Excel exports.
import { journey } from "../lib.mjs";

await journey("generate_and_export", {}, async ({ base, person, check }) => {
  const { page } = await person("user");
  await page.goto(base);
  check("starts empty", (await page.locator(".inst-list button").count()) === 0);
  await page.getByRole("button", { name: /\+ New/ }).click();
  await page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await page.waitForSelector(".inst-list button");
  await page.locator(".inst-list button").first().click();
  await page.getByRole("tab", { name: /^Timetable/ }).click();
  await page.getByRole("button", { name: /Generate timetable/ }).click();
  await page.waitForSelector(".card", { timeout: 120000 });
  check("a timetable appears", (await page.locator(".card").count()) > 0);
  await page.getByText("No clashes", { exact: true }).first().waitFor({ timeout: 30000 });
  check("with no clashes", true);

  for (const kind of ["Faculty", "Room"]) {
    await page.getByRole("tab", { name: kind, exact: true }).click();
    await page.waitForTimeout(300);
    check(`the ${kind.toLowerCase()} view shows sessions`, (await page.locator(".card").count()) > 0);
  }
  await page.getByRole("tab", { name: "Class", exact: true }).click();

  await page.getByRole("button", { name: /Export/ }).click();
  const hrefs = await page.locator('.menu-list a[role="menuitem"]').evaluateAll((els) => els.map((e) => [e.textContent, e.href]));
  check("the export menu offers PDF and Excel", hrefs.some(([t]) => /^PDF: this class/.test(t)) && hrefs.some(([t]) => /Excel workbook/.test(t)), hrefs.map((h) => h[0]).join(" | "));
  for (const [label, href] of hrefs) {
    const r = await fetch(href);
    const bytes = new Uint8Array(await r.arrayBuffer());
    const magic = String.fromCharCode(...bytes.slice(0, 4));
    check(`“${label}” downloads a real file`, r.ok && bytes.length > 1000 && (/PDF/.test(label) ? magic === "%PDF" : magic.startsWith("PK")), `${r.status}, ${bytes.length} bytes`);
  }
});
