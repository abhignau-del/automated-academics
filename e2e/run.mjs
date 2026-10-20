// Run every journey in turn, each in its own process with its own app and data folder. Exit 1 if any fails.
//   node run.mjs                 all of them
//   node run.mjs times clocks    only journeys whose file name contains one of these words
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const wanted = process.argv.slice(2);
const files = readdirSync(path.join(here, "journeys")).filter((f) => f.endsWith(".mjs")).sort()
  .filter((f) => wanted.length === 0 || wanted.some((w) => f.includes(w)));
if (files.length === 0) { console.error("no journeys match"); process.exit(2); }

const summary = [];
for (const f of files) {
  console.log(`\n=== ${f} ===`);
  const started = Date.now();
  const r = spawnSync(process.execPath, [path.join(here, "journeys", f)], { stdio: "inherit", cwd: here });
  summary.push([f, r.status === 0, Math.round((Date.now() - started) / 1000)]);
}
console.log("\n--- summary ---");
for (const [f, ok, s] of summary) console.log(`${ok ? "PASS" : "FAIL"}  ${f}  (${s} s)`);
process.exit(summary.every(([, ok]) => ok) ? 0 : 1);
