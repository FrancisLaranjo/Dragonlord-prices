// Checks that the refresh job's time limit covers a full seed of every material's daily range.
// A full seed killed every refresh for three hours on 2026-11-05: the limit was 30 minutes and the seed needed 31.
// Run: node tools/check_seed_cost.js
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const root = path.join(__dirname, "..");
// Measured on 2026-11-05: a normal Fetch takes about 10 minutes; the 9,083-request seed added about 21.
const NORMAL_FETCH_MINUTES = 10;
const SEED_REQUESTS_PER_MINUTE = 420;
const UPLOAD_AND_SETUP_MINUTES = 6;
const MARGIN_MINUTES = 5;

// A market.json with no days forces the full seed, whatever state the committed copy is in.
const market = JSON.parse(fs.readFileSync(path.join(root, "data", "market.json"), "utf8"));
const full = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "seed-")), "market.json");
fs.writeFileSync(full, JSON.stringify({ ...market, days: [] }));

let plan;
for (const python of ["python3", "python"]) {
  const r = spawnSync(python, ["-I", path.join(__dirname, "fetch_prices.py"), "--seed-plan", full, "2026-11-06"], { encoding: "utf8" });
  // Windows ships a python3 stub that only prints a Store hint and exits 9009.
  if (r.error || r.status === 9009) continue;
  assert.strictEqual(r.status, 0, `fetch_prices.py --seed-plan failed:\n${r.stderr}`);
  plan = JSON.parse(r.stdout);
  break;
}
assert.ok(plan, "no python on PATH to run fetch_prices.py --seed-plan");
assert.strictEqual(plan.requests, plan.materials, "an empty history seeds every material");

const workflow = fs.readFileSync(path.join(root, ".github", "workflows", "refresh-prices.yml"), "utf8");
const refreshJob = workflow.slice(workflow.indexOf("  refresh:"), workflow.indexOf("  queue-next:"));
const limit = Number(/timeout-minutes:\s*(\d+)/.exec(refreshJob)[1]);
const needed = NORMAL_FETCH_MINUTES + Math.ceil(plan.requests / SEED_REQUESTS_PER_MINUTE) + UPLOAD_AND_SETUP_MINUTES + MARGIN_MINUTES;
assert.ok(limit >= needed,
  `refresh timeout-minutes is ${limit}, but a full seed of ${plan.requests} requests needs about ${needed}. Raise it.`);
console.log(`ok: a full seed of ${plan.requests} requests needs about ${needed} minutes, under the ${limit}-minute limit`);
