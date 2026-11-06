# Stale prices

Players see an old price-age chip, or a `prices-stale` issue is open. Work down this list. Each step is one command.

1. **How old are the live prices?** `snapshotAt` is the Price Snapshot, `fetchedAt` is our download.

   ```bash
   curl -s https://dragonlord.aion2planner.workers.dev/data/prices.json | python -c "import json,sys;d=json.load(sys.stdin);print(d['fetchedAt'],d.get('snapshotAt'))"
   ```

2. **What did the refresh runs do?** A run killed by its job limit ends `cancelled`, not `failure`.

   ```bash
   gh run list --workflow refresh-prices.yml --limit 10
   ```

3. **Which step was slow?** Use step timings. Job logs stay locked until every job in the run ends, and `queue-next` sleeps for up to an hour.

   ```bash
   gh api repos/FrancisLaranjo/Dragonlord-prices/actions/jobs/<job id> -q '.steps[] | "\(.name) \(.started_at) \(.completed_at)"'
   ```

   A normal Fetch takes about 10 minutes. About 30 means a full seed.

4. **Will the next run seed?** This is a dry run with no network. It reads the live `market.json` the next run will carry forward.

   ```bash
   curl -s https://dragonlord.aion2planner.workers.dev/data/market.json -o market.json
   python -I tools/fetch_prices.py --seed-plan market.json
   ```

   A few dozen requests is normal. About 9,000 is a full seed, which needs the 45-minute limit that `tools/check_seed_cost.js` guards.

5. **Which change caused it?** Commit dates are not merge times. List what reached `main` and when:

   ```bash
   git log --first-parent origin/main --format="%h %cI %s" --since=<day>
   ```

   The refresh checks out this repo's `main` at run time, so the first run after a merge is the first on the new code.
   It also pulls recipes, the Season Shop and the Daeva Pass from the live site, so a deploy of the private site repo
   can change a refresh too. Check that repo's merges for the same day.

The Fetch log prints a timed line per phase (`[  612s] seed: 9057 requests planned`). Read those first once the logs unlock.
