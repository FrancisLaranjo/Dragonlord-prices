// Runs tools/check_outage.py: a failing Server is skipped and keeps its previous prices, and the API breaker trips.
// Run: node tools/check_outage.js
const assert = require("assert");
const path = require("path");
const { spawnSync } = require("child_process");

for (const python of ["python3", "python"]) {
  const r = spawnSync(python, ["-I", path.join(__dirname, "check_outage.py")], { encoding: "utf8" });
  // Windows ships a python3 stub that only prints a Store hint and exits 9009.
  if (r.error || r.status === 9009) continue;
  assert.strictEqual(r.status, 0, `check_outage.py failed:\n${r.stdout}${r.stderr}`);
  console.log(r.stdout.trim());
  process.exit(0);
}
assert.fail("no python on PATH to run check_outage.py");
