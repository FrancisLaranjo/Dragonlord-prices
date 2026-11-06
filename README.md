# Dragon Lord Planner price refresh

This repo keeps the live Market prices on https://dragonlord.aion2planner.workers.dev/ fresh. The site itself lives in a private repo.

It is public for one reason: GitHub Actions minutes are free for public repos. The refresh chain runs around the clock and uses about 1,500 minutes a day, far past a private repo's allowance.

## What a refresh does

1. `tools/pull_inputs.sh` downloads the recipes, Season Shop, Daeva Pass, `quna.js` and yesterday's `market.json` from the live site.
2. `tools/fetch_prices.py` downloads every Server's prices from aion2.exchange (about 10 minutes, about 31 on a full seed).
3. `tools/split_market.py` writes one file per Server.
4. The workflow PUTs every file to the site's Worker, which stores it in R2.
5. `queue-next` waits until just after the next hourly Price Snapshot and starts the next run. `tools/refresh_wait.js` owns that timing. GitHub's hourly cron is only a backup.

## Secrets

- `UPLOAD_TOKEN` is shared with the Worker, which refuses uploads without it.
- `UPLOAD_BASE_URL` is `https://dragonlord.aion2planner.workers.dev`.

## Checks

```bash
bash tools/pull_inputs.sh
node tools/check.js
```

When prices stop updating, follow [docs/stale-prices.md](docs/stale-prices.md).

All rights reserved. See [LICENSE](LICENSE).
