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
