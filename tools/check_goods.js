// Checks goods.json: each Market Good's Ask and last listed day, carried across refreshes (the planner's ADR 0005).
// Run: node tools/check_goods.js
const assert = require("assert");
const { spawnSync } = require("child_process");

const results = {
  "12319": { goods: {
    "510116011": ["Life Crystal", "Potions & serums", 49998, 25],
    "510800006": ["Shugo Transformation Candy", "Transformation candy", 9000, 0],
    "511490001": ["Seed of Detection", "Other items", 20000, 0],
  } },
};
const prev = { servers: { "12319": {
  "510800006": ["Shugo Transformation Candy", "Transformation candy", 10000, 50, "2026-11-01"],
  "511490001": ["Seed of Detection", "Other items", 20000, 0, null],
} } };
const code = `
import json, sys
sys.path.insert(0, ${JSON.stringify(__dirname)})
import fetch_prices
a = json.loads(sys.argv[1])
print(json.dumps(fetch_prices.goods_file(a["results"], "2026-11-08T12:00+00:00", a["prev"])))
`;
let out;
for (const python of ["python3", "python"]) {
  const r = spawnSync(python, ["-I", "-c", code, JSON.stringify({ results, prev })], { encoding: "utf8" });
  // Windows ships a python3 stub that only prints a Store hint and exits 9009.
  if (r.error || r.status === 9009) continue;
  assert.strictEqual(r.status, 0, `goods_file failed:\n${r.stderr}`);
  out = JSON.parse(r.stdout);
  break;
}
assert.ok(out, "no python on PATH to run fetch_prices.goods_file");
assert.strictEqual(out.day, "2026-11-08");
const rows = out.servers["12319"];
assert.deepStrictEqual(rows["510116011"], ["Life Crystal", "Potions & serums", 49998, 25, "2026-11-08"]);
console.log("ok: a Market Good listed today takes today's Ask and today as its listed day");
assert.deepStrictEqual(rows["510800006"], ["Shugo Transformation Candy", "Transformation candy", 10000, 0, "2026-11-01"]);
console.log("ok: one unlisted today keeps the Ask and day it was last seen listed");
assert.deepStrictEqual(rows["511490001"], ["Seed of Detection", "Other items", 20000, 0, null]);
console.log("ok: one never seen listed has no day, so the planner does not call it Sellable");
