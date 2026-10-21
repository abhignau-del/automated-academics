// A small harness for the browser journeys: it starts the real app (the built screen and the engine on one address,
// exactly as the packaged app does), opens a real browser, and reports checks as PASS / FAIL.
//
// Needs: the screen built (`npm run build:app` in ../frontend), Python with the backend installed, and a Chrome or Edge.
//   E2E_PYTHON       the Python to run the engine with (default: python)
//   E2E_EXECUTABLE   path of a Chrome/Chromium/Edge to use (default: Chrome, else Edge, found automatically)

import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(here, "..");
export const OUT = path.join(here, "out");

export const PW = "correct horse battery";

async function freePort() {
  return await new Promise((resolve, reject) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); });
    s.on("error", reject);
  });
}

/** Start the app on a free port with its own empty data folder. `auth` switches on sign-in (shared mode). */
export async function startApp({ auth = false, extraEnv = null } = {}) {
  const home = mkdtempSync(path.join(os.tmpdir(), "aa-e2e-"));
  const port = await freePort();
  const python = process.env.E2E_PYTHON || "python";
  const child = spawn(python, ["-m", "automated_academics.desktop", "--no-browser", "--port", String(port)], {
    cwd: path.join(ROOT, "backend"),
    env: { ...process.env, AA_HOME: home, AA_DB: "", AA_AUTH: auth ? "1" : "", ...(extraEnv ? extraEnv(port) : {}) },
    stdio: ["ignore", "ignore", "pipe"],
  });
  let err = "";
  child.stderr.on("data", (d) => { err += d; });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch(base + "/health")).ok) break; } catch { /* not up yet */ }
    if (child.exitCode !== null) throw new Error("the app exited early:\n" + err);
    await new Promise((r) => setTimeout(r, 500));
  }
  const html = await (await fetch(base + "/")).text().catch(() => "");
  if (!html.includes('id="root"')) throw new Error("the built screen was not found: run `npm run build:app` in frontend/ first");
  const stop = () => {
    try { child.kill(); } catch { /* gone */ }
    try { rmSync(home, { recursive: true, force: true }); } catch { /* still in use on Windows: leave it for the OS */ }
  };
  return { base, stop };
}

export async function launchBrowser() {
  const tries = [];
  if (process.env.E2E_EXECUTABLE) tries.push({ executablePath: process.env.E2E_EXECUTABLE });
  tries.push({ channel: "chrome" }, { channel: "msedge" });
  let last;
  for (const t of tries) {
    try { return await chromium.launch({ headless: true, args: process.env.CI ? ["--no-sandbox"] : [], ...t }); } catch (e) { last = e; }
  }
  throw new Error("no Chrome or Edge found; set E2E_EXECUTABLE. Last error: " + last?.message);
}

/**
 * Run one journey. `body({ base, browser, check, person, api })` does the work; `check(name, ok, detail)` records a result.
 * `person(label)` opens a separate browser (own cookies) with error capturing and auto-accepted dialogs.
 */
export async function journey(name, options, body) {
  const results = [];
  const check = (n, ok, detail = "") => { results.push(!!ok); console.log((ok ? "PASS " : "FAIL ") + n + (detail ? "  -> " + detail : "")); };
  const errors = [];
  const pages = [];
  let app, browser, failed = false;
  try {
    app = await startApp(options);
    browser = await launchBrowser();
    const person = async (label, viewport = { width: 1500, height: 1000 }) => {
      const ctx = await browser.newContext({ viewport });
      const page = await ctx.newPage();
      pages.push([label, page]);
      page.on("pageerror", (e) => errors.push(`${label}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error" && !/404|favicon|401|403|409/.test(m.text())) errors.push(`${label}: ${m.text()}`); });
      page.on("dialog", (d) => d.accept());
      return { ctx, page };
    };
    const api = {
      get: async (p) => (await fetch(app.base + p)).json(),
      post: async (p, body) => (await fetch(app.base + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json(),
      put: async (p, body) => (await fetch(app.base + p, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json(),
    };
    await body({ base: app.base, browser, check, person, api });
    check("no browser errors", errors.length === 0, errors.join(" | ").slice(0, 400));
  } catch (e) {
    failed = true;
    console.log("FAIL " + name + " crashed: " + (e?.message ?? e).toString().split("\n").slice(0, 6).join(" | "));
    mkdirSync(OUT, { recursive: true });
    for (const [label, page] of pages) await page.screenshot({ path: path.join(OUT, `${name}-${label}-failure.png`) }).catch(() => {});
  } finally {
    await browser?.close().catch(() => {});
    app?.stop();
  }
  const ok = !failed && results.length > 0 && results.every(Boolean);
  console.log(`${ok ? "OK" : "FAILED"} ${name}: ${results.filter(Boolean).length}/${results.length} checks`);
  process.exit(ok ? 0 : 1);
}

/** Wait until `fn()` is truthy (a poll, for things the server does in the background). */
export async function until(fn, { timeout = 120000, every = 500 } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, every));
  }
}

/** A tiny mail server that only listens: it keeps every message it is given, so a journey can read the e-mail the app sent. */
export async function startMailSink() {
  const messages = [];
  const server = net.createServer((sock) => {
    let data = "", inData = false, buf = "";
    const say = (t) => sock.write(t + "\r\n");
    say("220 sink ready");
    sock.on("data", (chunk) => {
      buf += chunk.toString("utf8");
      let i;
      while ((i = buf.indexOf("\r\n")) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (inData) {
          if (line === ".") { inData = false; messages.push(data); data = ""; say("250 queued"); }
          else data += line.replace(/^\.\./, ".") + "\n";
        } else if (/^(EHLO|HELO)/i.test(line)) say("250 sink");
        else if (/^DATA/i.test(line)) { inData = true; say("354 go on"); }
        else if (/^QUIT/i.test(line)) { say("221 bye"); sock.end(); }
        else say("250 ok");
      }
    });
    sock.on("error", () => {});
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { port: server.address().port, messages, stop: () => server.close() };
}
