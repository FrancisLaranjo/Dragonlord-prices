#!/usr/bin/env bash
# Downloads what fetch_prices.py reads from the live Dragon Lord Planner, so this repo never holds a copy that can
# drift from the site. The site's own repo is private; editing recipes or the Season Shop there reaches the next
# refresh once Cloudflare deploys it.
# Run: bash tools/pull_inputs.sh   (UPLOAD_BASE_URL defaults to the live site)
set -euo pipefail
base="${UPLOAD_BASE_URL:-https://dragonlord.aion2planner.workers.dev}"
base="${base%/}"
cd "$(dirname "$0")/.."
mkdir -p data
# market.json carries every material's daily closes forward. Without it the next Fetch would reseed every material
# (about 31 minutes), so a missing file fails the run instead of quietly starting over.
for f in data/recipes.json data/shop.json data/pass.json data/quna.json data/market.json quna.js; do
  curl -fsS -m 60 "$base/$f" -o "$f"
  echo "pulled $f ($(wc -c < "$f") bytes)"
done
# goods.json carries each Market Good's last listed day. It is optional: the first refresh that writes it has none.
if curl -fsS -m 60 "$base/data/goods.json" -o data/goods.json; then
  echo "pulled data/goods.json ($(wc -c < data/goods.json) bytes)"
else
  rm -f data/goods.json
  echo "no data/goods.json yet: every Market Good starts with no listed day"
fi
# prices.json is what a Server whose pages keep failing carries forward. Optional: without it that Server is left out.
if curl -fsS -m 60 "$base/data/prices.json" -o data/prices.json; then
  echo "pulled data/prices.json ($(wc -c < data/prices.json) bytes)"
else
  rm -f data/prices.json
  echo "no data/prices.json: a Server that fails this run has no previous prices to keep"
fi
