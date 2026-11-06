// Every check in one command. Needs data/market.json: run `bash tools/pull_inputs.sh` first.
// Run: node tools/check.js   (CI runs it on every pull request)
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const checks = fs.readdirSync(__dirname).filter(f => /^check_.+\.js$/.test(f)).sort();
let failed = 0;
for (const f of checks) {
  const r = spawnSync(process.execPath, [path.join(__dirname, f)], { encoding: "utf8" });
  if (r.status === 0) { console.log(`pass: ${f}`); continue; }
  failed++;
  console.log(`FAIL: ${f}\n${r.stdout}${r.stderr}`);
}
console.log(failed ? `${failed} of ${checks.length} failed` : `all ${checks.length} passed`);
process.exit(failed ? 1 : 0);
