// Checks when the price chain starts its next refresh.
// Run: node tools/check_refresh_wait.js
const assert = require("assert");
const fs = require("fs");
const { spawnSync } = require("child_process");
const path = require("path");
const { waitSeconds, START_MINUTE, RETRY_SECONDS, MAX_RETRY_SECONDS } = require("./refresh_wait.js");

const at = (h, m, s = 0) => Date.UTC(2026, 10, 5, h, m, s);

assert.strictEqual(waitSeconds("success", at(20, 22)), 48 * 60, "a refresh that ends at :22 waits for the next hour's :10");
assert.strictEqual(waitSeconds("success", at(20, 5)), 5 * 60, "a refresh that ends at :05 waits for this hour's :10");
assert.strictEqual(waitSeconds("success", at(20, 9, 30)), 30, "a refresh that ends at :09:30 waits 30 seconds");
assert.strictEqual(waitSeconds("success", at(20, 10)), 0, "a refresh that ends at :10 sharp starts the next at once");
assert.strictEqual(waitSeconds("success", at(20, 10, 1)), 3599, "a refresh that ends at :10:01 waits the rest of the hour");
assert.strictEqual(waitSeconds("success", at(23, 30)), 40 * 60, "the wait crosses midnight");
for (const result of ["failure", "cancelled", "skipped", ""]) {
  assert.strictEqual(waitSeconds(result, at(20, 22)), RETRY_SECONDS, `a first "${result}" at :22 retries in five minutes`);
  assert.strictEqual(waitSeconds(result, at(20, 3)), RETRY_SECONDS, `a first "${result}" at :03 retries in five minutes`);
}
console.log("ok: a successful refresh waits for the next :10 UTC, a first failure retries in five minutes");

assert.deepStrictEqual([0, 1, 2, 3, 4, 5, 20, 2000].map((n) => waitSeconds("failure", at(20, 22), n)),
  [300, 600, 1200, 2400, 3600, 3600, 3600, 3600], "each consecutive failure doubles the retry wait, capped at an hour");
assert.strictEqual(MAX_RETRY_SECONDS, 3600, "the retry cap is one hour");
assert.strictEqual(waitSeconds("success", at(20, 22), 5), 48 * 60, "past failures do not delay a successful refresh");
console.log("ok: repeated failures back off 300, 600, 1200, 2400, then 3600 seconds");

const run = (...args) => spawnSync(process.execPath, [path.join(__dirname, "refresh_wait.js"), ...args], { encoding: "utf8" });
const now = String(at(20, 22) / 1000);
assert.strictEqual(run("success", now).stdout.trim(), String(48 * 60), "the command prints the success wait in seconds");
assert.strictEqual(run("failure", now).stdout.trim(), String(RETRY_SECONDS), "the command defaults to zero failures");
assert.strictEqual(run("failure", now, "2").stdout.trim(), "1200", "the command takes consecutive failures as its third argument");
assert.notStrictEqual(run("success", "not-a-number").status, 0, "the command rejects a non-numeric now");
for (const bad of ["-1", "1.5", "two", ""]) {
  assert.notStrictEqual(run("failure", now, bad).status, 0, `the command rejects failures "${bad}"`);
}
console.log("ok: the command takes result, unix seconds and an optional failure count, and rejects bad input");

const workflow = fs.readFileSync(path.join(__dirname, "..", ".github", "workflows", "refresh-prices.yml"), "utf8");
const cron = workflow.match(/^\s*-\s*cron:\s*"(\S+)\s/m);
assert.ok(cron, "refresh-prices.yml has a schedule cron entry");
assert.strictEqual(Number(cron[1]), START_MINUTE, "the backup cron minute in refresh-prices.yml equals START_MINUTE in tools/refresh_wait.js");
console.log("ok: the backup cron runs at the same minute the chain waits for");
