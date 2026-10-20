// Publish a timetable by link: an editor makes the link, a stranger with no account opens it read-only, and it can be withdrawn.
import { journey, PW } from "../lib.mjs";

await journey("publish_links", { auth: true }, async ({ base, person, check }) => {
  const call = (page, method, p, body) => page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, credentials: "include", body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [method, p, body]);

  // administrator and an owner
  const A = await person("admin");
  await A.page.goto(base);
  await A.page.getByLabel("Username").fill("root");
  await A.page.getByLabel("Your name").fill("Admin");
  await A.page.getByLabel("Password", { exact: true }).fill(PW);
  await A.page.getByLabel("Password again").fill(PW);
  await A.page.getByRole("button", { name: "Create administrator" }).click();
  await A.page.waitForSelector("text=Admin (administrator)");
  await call(A.page, "POST", "/users", { username: "owenowner", password: PW, display_name: "Owen" });

  // the owner makes a timetable and publishes it for classes
  const O = await person("owner");
  await O.page.goto(base);
  await O.page.getByLabel("Username").fill("owenowner");
  await O.page.getByLabel("Password").fill(PW);
  await O.page.getByRole("button", { name: "Sign in" }).click();
  await O.page.getByRole("button", { name: /\+ New/ }).click();
  await O.page.getByRole("menuitem", { name: /Copy of the sample/ }).click();
  await O.page.waitForSelector(".inst-list button");
  await O.page.locator(".inst-list button").first().click();
  await O.page.getByRole("tab", { name: /^Timetable/ }).click();
  check("there is nothing to publish before a timetable exists", (await O.page.getByRole("button", { name: "Publish…" }).count()) === 0);
  await O.page.getByRole("button", { name: /Generate timetable/ }).click();
  await O.page.waitForSelector(".card", { timeout: 120000 });
  await O.page.getByRole("button", { name: "Publish…" }).click();
  check("classes are ticked by default and teachers are not", (await O.page.getByLabel("Class timetables").isChecked()) && !(await O.page.getByLabel("Teacher timetables").isChecked()));
  await O.page.getByLabel("link name").fill("Semester 1");
  await O.page.getByRole("button", { name: "Create link" }).click();
  const input = O.page.getByLabel("link Semester 1");
  await input.waitFor();
  const link = await input.inputValue();
  check("a link is made", /\/#\/p\/[A-Za-z0-9_-]{40,}$/.test(link), link.slice(0, 60));
  const token = link.split("/#/p/")[1];

  // a stranger: no cookies, no account
  const S = await person("student");
  await S.page.goto(link);
  await S.page.waitForSelector(".card", { timeout: 30000 });
  check("a stranger sees the timetable without signing in", (await S.page.locator(".card").count()) > 0);
  check("it says it is view only", /view only/.test(await S.page.locator(".public header").innerText()));
  check("only classes were published, so there is no Teacher or Room tab", (await S.page.getByRole("tab", { name: "Teacher" }).count()) === 0 && (await S.page.getByRole("tab", { name: "Room" }).count()) === 0);
  check("nothing can be dragged or edited", (await S.page.locator('.card[draggable="true"]').count()) === 0 && (await S.page.getByRole("button", { name: /Save|Generate|Share/ }).count()) === 0);
  const before = await S.page.locator("select[aria-label=Class]").inputValue();
  const options = await S.page.locator("select[aria-label=Class] option").evaluateAll((o) => o.map((x) => x.value));
  const other = options.find((v) => v !== before);
  await S.page.locator("select[aria-label=Class]").selectOption(other);
  await S.page.waitForTimeout(400);
  check("choosing another class puts it in the address, to bookmark", S.page.url().includes(`/batch/${encodeURIComponent(other)}`), S.page.url().slice(-40));
  const pdfHref = await S.page.getByRole("link", { name: /Download PDF: this class/ }).getAttribute("href");
  const pdf = await fetch(new URL(pdfHref, base));
  check("the class can be downloaded as a PDF", pdf.ok && String.fromCharCode(...new Uint8Array(await pdf.arrayBuffer()).slice(0, 4)) === "%PDF");
  check("but there is no Excel or whole-institution download", (await S.page.getByRole("link", { name: /Excel|everything/ }).count()) === 0);
  await S.page.getByRole("tab", { name: "List" }).click();
  check("the list layout shows the days", (await S.page.locator(".agenda h3").count()) > 0);

  // the views that were not published are not available even by guessing the address
  check("a teacher's timetable is refused", (await fetch(`${base}/public/${token}/views/faculty/CSF1`)).status === 404);
  check("the rest of the app still needs a sign-in", (await fetch(`${base}/institutions`)).status === 401);

  // a bookmarked class opens straight to it, and a phone gets the list
  const P = await person("phone", { width: 390, height: 800 });
  await P.page.goto(`${link}/batch/${options[1] ?? options[0]}`);
  await P.page.waitForSelector(".agenda", { timeout: 30000 });
  check("on a phone it opens on the list", true);
  check("at the bookmarked class", (await P.page.locator("select[aria-label=Class]").inputValue()) === (options[1] ?? options[0]));

  // a second link that includes teachers
  await O.page.getByLabel("link name").fill("Staff");
  await O.page.getByLabel("Teacher timetables").check();
  await O.page.getByRole("button", { name: "Create link" }).click();
  const staff = await O.page.getByLabel("link Staff").inputValue();
  const T = await person("staff");
  await T.page.goto(staff);
  await T.page.waitForSelector(".card", { timeout: 30000 });
  check("a link with teachers offers a Teacher tab", (await T.page.getByRole("tab", { name: "Teacher" }).count()) === 1);

  // withdrawing the first link ends it at once
  await O.page.locator("tr", { hasText: "Semester 1" }).getByRole("button", { name: "Withdraw" }).click();
  await O.page.waitForTimeout(600);
  const gone = await person("stranger-again");
  await gone.page.goto(link);
  await gone.page.waitForSelector("text=can't be shown", { timeout: 15000 });
  check("a withdrawn link says it can't be shown", true);
  check("and the other link still works", (await fetch(`${base}/public/${staff.split("/#/p/")[1]}`)).status === 200);
});
