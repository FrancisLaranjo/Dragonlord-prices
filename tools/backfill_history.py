"""One-off Price History backfill (ADR 0003): every Server's daily close and listings for every Market material, from
aion2.exchange's full range, as one data/history/YYYY-MM-DD.json per UTC day from HISTORY_FIRST_DAY up to yesterday.
The hourly refresh owns today. Each merged day is then folded into each Server's whole Price History,
data/history/server/<serverId>.json, the file the item chart reads.

Rerunnable. Each day merges into what is already stored, and a [close, listings] already stored is kept, so the
hourly snapshot of a day always beats the site's daily candle, and a second run uploads nothing.

Run: python -I tools/backfill_history.py                       writes data/history/ locally, touches nothing remote
     python -I tools/backfill_history.py --upload              also merges with and PUTs to the Worker
                                                               (needs UPLOAD_BASE_URL and UPLOAD_TOKEN)
     python -I tools/backfill_history.py --server 11101 --items 3 --out <dir>    a small trial run
About 9,000 requests (106 Servers by about 87 materials) for a full run.
"""
import argparse
import concurrent.futures
import os
import pathlib
import sys
import urllib.request

# python -I leaves the script's folder off sys.path; fetch_prices is the one place that talks to aion2.exchange.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_prices  # noqa: E402


def materials(sid):
    return [str(it["item_key"]) for it in fetch_prices.all_items(sid, "24h") if it["category"]["section_id"] == "materials"]


def candle_days(server_ids, item_cap):
    """{day: {serverId: {itemKey: [close, listings]}}} from the site's daily candles."""
    jobs = [(sid, key) for sid in server_ids for key in materials(sid)[:item_cap]]
    print(f"{len(jobs)} material series across {len(server_ids)} Servers", file=sys.stderr)

    def pull(job):
        sid, key = job
        try:
            return job, fetch_prices.daily_candles(sid, key)
        except Exception as err:
            print(f"skipped {sid}/{key}: {err}", file=sys.stderr)
            return job, {}

    days = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for (sid, key), series in pool.map(pull, jobs):
            for day, entry in series.items():
                days.setdefault(day, {}).setdefault(sid, {})[key] = entry
    return days


def merge(stored, candles):
    """Candles fill gaps only; every Server and material already stored keeps its value."""
    out = {sid: dict(items) for sid, items in candles.items()}
    for sid, items in stored.items():
        out.setdefault(sid, {}).update(items)
    return out


def put(base, token, key, path):
    req = urllib.request.Request(f"{base}/__upload/{key}", data=path.read_bytes(), method="PUT",
                                 headers={**fetch_prices.UA, "Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def stored(base, key, path):
    """The Worker's copy with --upload, else the local file; an empty dict when there is none."""
    return (fetch_prices.remote_json(base, key) if base else fetch_prices.local_json(path)) or {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upload", action="store_true", help="merge with the Worker's stored days and PUT the changed ones")
    ap.add_argument("--server", action="append", help="limit to this Server id (repeatable)")
    ap.add_argument("--items", type=int, help="limit to the first N materials per Server")
    ap.add_argument("--out", type=pathlib.Path, default=fetch_prices.ROOT / "data" / "history")
    args = ap.parse_args()

    base, token = os.environ.get("UPLOAD_BASE_URL", "").rstrip("/"), os.environ.get("UPLOAD_TOKEN", "")
    if args.upload and not (base and token):
        sys.exit("--upload needs UPLOAD_BASE_URL and UPLOAD_TOKEN")

    server_ids = [str(s["server_id"]) for s in fetch_prices.get("/servers")["servers"] if s["has_data"]]
    if args.server:
        server_ids = [sid for sid in server_ids if sid in set(args.server)]
    candles = candle_days(server_ids, args.items)
    today = fetch_prices.fetched_now()[:10]

    remote_base = base if args.upload else ""
    written = uploaded = 0
    merged_days = {}
    for day in sorted(d for d in candles if fetch_prices.HISTORY_FIRST_DAY <= d < today):
        path = args.out / f"{day}.json"
        local = fetch_prices.local_json(path) or {}
        remote = stored(remote_base, f"data/history/{day}.json", path) if args.upload else {}
        merged = merged_days[day] = merge(merge(remote, local), candles[day])
        if merged != local:
            fetch_prices.write_history(args.out, day, merged)
            written += 1
        if args.upload and merged != remote:
            put(base, token, f"data/history/{day}.json", path)
            uploaded += 1

    servers_written = servers_uploaded = 0
    for sid in server_ids:
        path = args.out / "server" / f"{sid}.json"
        before = stored(remote_base, f"data/history/server/{sid}.json", path)
        hist = fetch_prices.server_history_from_days(merged_days, sid, before)
        if not hist["days"]:
            continue
        if hist !=(fetch_prices.local_json(path) or {}):
            fetch_prices.write_history(args.out / "server", sid, hist)
            servers_written += 1
        if args.upload and hist != before:
            put(base, token, f"data/history/server/{sid}.json", path)
            servers_uploaded += 1
    print(f"{written} days written to {args.out}, {uploaded} days uploaded; "
          f"{servers_written} Server histories written, {servers_uploaded} uploaded")


if __name__ == "__main__":
    main()
