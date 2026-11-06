// How long the price chain waits before it starts the next refresh.
// aion2.exchange takes its Price Snapshot once an hour at about :08 UTC, so a refresh that starts at :10 reads the
// newest one. A failed or cancelled refresh retries soon instead of losing a whole hour, backing off when it keeps
// failing so a broken source is not hammered every five minutes.
// Run: node tools/refresh_wait.js <refresh result> <now, unix seconds> [consecutive failures]   (prints seconds to sleep)
const START_MINUTE = 10, RETRY_SECONDS = 300, MAX_RETRY_SECONDS = 3600;

function waitSeconds(result, nowMs, failures = 0) {
  if (result !== "success") return Math.min(RETRY_SECONDS * 2 ** failures, MAX_RETRY_SECONDS);
  const next = new Date(nowMs);
  next.setUTCMinutes(START_MINUTE, 0, 0);
  if (next.getTime() < nowMs) next.setUTCHours(next.getUTCHours() + 1);
  return Math.ceil((next.getTime() - nowMs) / 1000);
}

if (require.main === module) {
  const [result, now, failures = "0"] = process.argv.slice(2);
  if (!result || !/^\d+$/.test(now || "") || !/^\d+$/.test(failures)) {
    console.error("usage: node tools/refresh_wait.js <result> <unix seconds> [consecutive failures]");
    process.exit(2);
  }
  console.log(waitSeconds(result, Number(now) * 1000, Number(failures)));
}

module.exports = { waitSeconds, START_MINUTE, RETRY_SECONDS, MAX_RETRY_SECONDS };
