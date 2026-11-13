"""Pull Market prices for every server from aion2.exchange into data/prices.json and data/market.json.

Also pulls each Server's Kina Chest Quna Asks from /quna?server= into data/quna.json for the Quna tab,
plus each Server's Quna history (best Kina per Quna) via /quna/history reduced by Quna.bestKinaPerQunaHistory.
aion2.exchange collects Kina Chests on very few Servers, so the rest take their Asks and history from questlog.gg.

Run: python -I tools/fetch_prices.py
Dry run of the seed, no network: python -I tools/fetch_prices.py --seed-plan data/market.json [YYYY-MM-DD]
The hourly workflow runs this and uploads the files to the Worker (R2). data/market.json carries every material's
price, listings and 24-hour and 7-day change, plus 30 days of daily closing prices. The history is carried
forward from the live data/market.json, so it grows by a day each day. A failed Quna history pull keeps that
Server's previous history from data/quna.json the same way.

Each run also writes today's Price History day, data/history/YYYY-MM-DD.json (UTC), holding
{serverId: {itemKey: [close, listings]}} for every Market material (ADR 0003). The workflow PUTs it every hour, so
the day's last run wins and past days are never rewritten. tools/backfill_history.py fills the days before.
It also folds today into each Server's whole Price History, data/history/server/<serverId>.json holding
{"days": [...], "items": {itemKey: [[close, listings] or null per day]}}, so the item chart reads one file per
Server instead of one all-Server file per day. A Server with no stored file is rebuilt from the day objects.
"""
import concurrent.futures
import datetime
import email.utils
import http.client
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://aion2.exchange/api"
UA = {"User-Agent": "AION 2 crafting planner price refresh"}
PAGE = 200
# Face Kina for the three crafted Kina Chests the Quna Tracker tracks.
QUNA_FACES = (10000000, 2000000, 400000)
# aion2.exchange collects Kina Chests on very few Servers; questlog.gg lists every Server, keyed by game server key.
QUESTLOG_RATES = "https://questlog.gg/aion-2/api/trpc/auctionHouse.getExchangeRates"
QUESTLOG_CHEST_FACE = {516130001: 10000000, 516140001: 2000000, 516150001: 400000}
QUNA_HISTORY_DAYS = 8


# The Actions runner clock can be an hour off (it was, on the US daylight-saving fallback).
# The daily close trusts fetchedAt, so the time comes from the exchange's Date header instead of
# the runner. The age chip counts from the Price Snapshot instead.
OBSERVED_AT = None

STARTED = time.monotonic()


def log(msg):
    """One timed progress line on stderr, so a run killed by its job limit still shows which phase was slow."""
    print(f"[{time.monotonic() - STARTED:6.0f}s] {msg}", file=sys.stderr, flush=True)


# Thousands of requests per run: one response cut off mid-body (IncompleteRead) killed the 2026-11-01 20:41 refresh.
GET_TRIES = 4


def transient(err):
    if isinstance(err, urllib.error.HTTPError):
        return err.code == 429 or err.code >= 500
    return isinstance(err, (http.client.HTTPException, urllib.error.URLError, OSError, json.JSONDecodeError))


# A failing get sleeps 5 + 10 + 20 = 35 seconds before it gives up. This many in a row (every retry spent, no success
# between) means the API is down, not one flaky page: every later get then fails at once instead of burning 35 seconds
# each, so an outage ends the run in about two minutes. Servers that finished before it keep their fresh prices.
OUTAGE_AFTER = 12
# No new Server's prices start after this many seconds into the run. A healthy run finishes them by about 1,600.
PRICES_START_DEADLINE_S = 28 * 60
FAILS_IN_A_ROW = 0


class ApiDown(Exception):
    pass


def get(path):
    global OBSERVED_AT, FAILS_IN_A_ROW
    if FAILS_IN_A_ROW >= OUTAGE_AFTER:
        raise ApiDown(f"aion2.exchange failed {OUTAGE_AFTER} requests in a row")
    req = urllib.request.Request(f"{API}{path}", headers=UA)
    for attempt in range(GET_TRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                observed = r.headers.get("Date")
                body = json.load(r)
            FAILS_IN_A_ROW = 0
            break
        except Exception as err:
            if not transient(err):
                raise
            if attempt == GET_TRIES - 1:
                FAILS_IN_A_ROW += 1
                raise
            time.sleep(2 ** attempt * 5)
    if observed:
        parsed = email.utils.parsedate_to_datetime(observed)
        if parsed is not None:
            OBSERVED_AT = parsed.astimezone(datetime.timezone.utc)
    return body


def fetched_now():
    when = OBSERVED_AT or datetime.datetime.now(datetime.timezone.utc)
    return when.isoformat(timespec="minutes")


def snapshot_at():
    """The Price Snapshot these prices come from. It is hourly, so this is the age that matters to a player, and it
    is read before the downloads so it never claims a newer Price Snapshot than the data.
    None when /health is unreachable; the page then falls back to fetchedAt."""
    try:
        at = datetime.datetime.fromisoformat(get("/health")["latest_snapshot_at"].replace("Z", "+00:00"))
    except Exception:
        print("/health unreachable, prices carry no Price Snapshot time", file=sys.stderr)
        return None
    return at.astimezone(datetime.timezone.utc).isoformat(timespec="minutes")


def all_items(sid, window):
    items, offset = [], 0
    while True:
        page = get(f"/items?server={sid}&limit={PAGE}&offset={offset}&window={window}")
        items += page["items"]
        offset += PAGE
        if offset >= page["total"]:
            return items


def server_prices(server, wanted_items, wanted_names, artisan_keys, unique_gear):
    sid = server["server_id"]
    crafts = {}
    for key in artisan_keys:
        try:
            tree = get(f"/crafts/{key}?server={sid}")
        except urllib.error.HTTPError as err:
            if transient(err):
                raise  # an outage, not the other faction's version of this item
            continue
        # A server with no listing for some material leaves its price and the total empty.
        whole = lambda v: None if v is None else round(v)
        crafts[key] = {
            "cost": whole(tree["latest"]["cost"]),
            "steps": [{
                "out": str(s["result_key"]),
                "name": s["result_name"],
                "mastery": s["mastery_level"],
                "attempts": round(s["attempts"], 2),
                "mats": [[str(m["material_key"]), m["name"], round(m["expected"], 2), whole(m["unit_price"])] for m in s["materials"]],
            } for s in tree["steps"]],
        }
    # The Artisan's chains use materials the Dragon Lord recipes don't, so price those too.
    wanted_items = wanted_items | {m[0] for c in crafts.values() for s in c["steps"] for m in s["mats"]}

    items = all_items(sid, "24h")
    week = {it["item_key"]: it.get("change_pct") for it in all_items(sid, "7d")}
    prices, targets, market, goods = {}, {}, {}, {}
    # Pass ROI values bound Unique gear at the cheapest Unique gear on the Market. Every Unique rerolls gear lines,
    # so none sells for much less than the rest; a listing under a quarter of the median is a stray dump.
    unique_asks = sorted(it["price"] for it in items
                         if str(it["item_key"]) in unique_gear and it["price"] is not None and it["listings"])
    unique_floor = next((p for p in unique_asks if p >= unique_asks[len(unique_asks) // 2] / 4), None) if unique_asks else None
    for it in items:
        price = it["price"] if it["price"] is not None else it["last_price"]
        if price is None:
            continue
        change = it.get("change_pct")
        entry = [round(price), it["listings"], None if change is None else round(change)]
        if str(it["item_key"]) in wanted_items:
            prices[str(it["item_key"])] = entry
        if it["name"] in wanted_names:
            targets[it["name"]] = entry
        if it["category"]["section_id"] == "materials":
            w = week.get(it["item_key"])
            market[str(it["item_key"])] = {"name": it["name"], "cat": it["category"]["name"],
                                           "row": [round(price), it["listings"], None if change is None else round(change, 1), None if w is None else round(w, 1)]}
        else:
            goods[str(it["item_key"])] = [it["name"], it["category"]["name"], round(price), it["listings"] or 0]
    return str(sid), {"items": prices, "targets": targets, "crafts": crafts, "market": market, "goods": goods,
                      "uniqueFloor": None if unique_floor is None else round(unique_floor)}


def try_server(server, *args):
    """server_prices, or (serverId, None) when this Server's pages kept failing or the run is out of time. Its
    previous prices are kept instead (keep_previous)."""
    try:
        if time.monotonic() - STARTED > PRICES_START_DEADLINE_S:
            raise TimeoutError("past the time budget")
        return server_prices(server, *args)
    except Exception as err:
        print(f"Server {server['server_id']} skipped, its previous prices stay: {err!r}", file=sys.stderr)
        return str(server["server_id"]), None


def keep_previous(failed, prev_prices, prev_market, prev_goods, days):
    """What to carry for each Server in `failed`, from the live files the run pulled:
    {serverId: {"fetchedAt": when those prices were fetched, "prices", "market", "names", "goods"}}.
    The Market closes are re-laid on today's `days`; today gets no close, because nothing was seen today.
    A Server with no previous prices is left out, never written empty."""
    kept = {}
    for sid in failed:
        prices = (prev_prices.get("prices") or {}).get(sid)
        if not prices:
            continue
        srv = (prev_market.get("servers") or {}).get(sid)
        market = None
        if srv:
            was = prev_market.get("days", [])
            market = {"items": srv["items"],
                      "closes": {k: [dict(zip(was, c)).get(d) for d in days] for k, c in srv["closes"].items()}}
        kept[sid] = {
            "fetchedAt": (prev_prices.get("staleServers") or {}).get(sid) or prev_prices["fetchedAt"],
            "prices": prices,
            "market": market,
            "names": {k: prev_market["names"][k] for k in (market or {"items": {}})["items"] if k in prev_market.get("names", {})},
            "goods": (prev_goods.get("servers") or {}).get(sid),
        }
    return kept


HISTORY_DAYS = 30
# The first day of Price History: the site's full range starts here (ADR 0003).
HISTORY_FIRST_DAY = "2026-10-06"
# A material with fewer than NEW_MATERIAL_KNOWN closes in the last NEW_MATERIAL_DAYS days is new to us.
NEW_MATERIAL_DAYS = 7
NEW_MATERIAL_KNOWN = 5


def load_previous_market():
    try:
        return json.loads((ROOT / "data" / "market.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def load_previous_prices():
    """The live prices.json, pulled by tools/pull_inputs.sh. Optional: without it no Server can keep previous prices."""
    try:
        return json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def load_previous_goods():
    """Yesterday's goods.json, the carry-file for each Market Good's last listed day. Optional: without it every
    Market Good starts with no listed day, and the planner reads it as Sellable from its next listing on."""
    try:
        return json.loads((ROOT / "data" / "goods.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def goods_file(results, fetched_at, prev):
    """Every Market Good per Server as [name, category, Ask, listings today, last day listed or null]: everything the
    price site sells beyond materials, with no Price History. A Good listed today takes today's lowest Ask and today as
    its day. One unlisted today keeps the Ask and day it was last seen listed, carried from the previous goods.json.
    The site only gives a last price without its day, so a Good never seen listed here has no day and no Sellable
    copies until it lists (the planner's ADR 0005)."""
    day = fetched_at[:10]
    servers = {}
    for sid, res in results.items():
        old = prev.get("servers", {}).get(sid, {})
        rows = {}
        for key, (name, cat, price, listings) in res["goods"].items():
            was = old.get(key)
            if listings > 0:
                rows[key] = [name, cat, price, listings, day]
            elif was and was[4]:
                rows[key] = [name, cat, was[2], 0, was[4]]
            else:
                rows[key] = [name, cat, price, 0, None]
        servers[sid] = rows
    return {"fetchedAt": fetched_at, "day": day, "servers": servers}


def load_previous_quna():
    try:
        return json.loads((ROOT / "data" / "quna.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


EMPTY_QUNA_HISTORY = {"days": [], "bestKinaPerQuna": []}


def as_quna_history(hist):
    """Accept a reduced history object, or None when the shape is unusable."""
    if isinstance(hist, dict) and isinstance(hist.get("days"), list) and isinstance(hist.get("bestKinaPerQuna"), list):
        return {"days": hist["days"], "bestKinaPerQuna": hist["bestKinaPerQuna"]}
    return None


def previous_quna_history(prev_servers, sid):
    return as_quna_history((prev_servers.get(sid) or {}).get("history")) or dict(EMPTY_QUNA_HISTORY)


def pull_history_series(sid):
    """Daily history preferred; hourly when daily is empty. None means the pull failed."""
    got_empty_daily = False
    try:
        series = get(f"/quna/history?server={sid}&bucket=1d").get("series") or []
        if series:
            return series
        got_empty_daily = True
    except Exception:
        pass
    try:
        return get(f"/quna/history?server={sid}").get("series") or []
    except Exception:
        return [] if got_empty_daily else None


def reduce_histories(series_by_sid):
    """Run Quna.bestKinaPerQunaHistory once per Server series so the calculator stays the source of truth."""
    if not series_by_sid:
        return {}
    script = (
        "const Q=require('./quna.js');let b='';"
        "process.stdin.on('data',d=>b+=d);"
        "process.stdin.on('end',()=>{"
        "const inn=JSON.parse(b),out={};"
        "for(const[k,s] of Object.entries(inn)) out[k]=Q.bestKinaPerQunaHistory(s);"
        "process.stdout.write(JSON.stringify(out));"
        "});"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(series_by_sid, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Quna.bestKinaPerQunaHistory failed")
    return json.loads(proc.stdout)


def history_window(today):
    return [str(today - datetime.timedelta(days=i)) for i in range(HISTORY_DAYS - 1, -1, -1)]


def seed_plan(results, prev, today):
    """The (serverId, itemKey) pairs whose full daily range this run asks for, one request each. Every material
    when the carried market.json starts later than the HISTORY_DAYS window (a fresh start, or the first run after the
    window grew), otherwise only materials new to us. A full seed is about 9,000 requests and 20 extra minutes of
    Fetch, so tools/check_seed_cost.js keeps the refresh job's timeout-minutes above it."""
    days = prev.get("days", [])
    short = not days or days[0] > history_window(today)[0]
    recent = {str(today - datetime.timedelta(days=i)) for i in range(1, NEW_MATERIAL_DAYS + 1)}
    wanted = []
    for sid, res in results.items():
        closes = prev.get("servers", {}).get(sid, {}).get("closes", {})
        for key in res["market"]:
            known = sum(1 for d, v in zip(days, closes.get(key, [])) if d in recent and v is not None)
            if short or known < NEW_MATERIAL_KNOWN:
                wanted.append((sid, key))
    return wanted


def seed_closes(results, prev, today):
    """Daily closes from aion2.exchange's full range for every pair in seed_plan, so a graph fills straight away.
    The window is then recorded, so a material is never asked again."""
    wanted = seed_plan(results, prev, today)
    log(f"seed: {len(wanted)} requests planned")

    def candles(job):
        sid, key = job
        try:
            pts = daily_candles(sid, key)
        except Exception:
            return job, {}
        return job, {day: close for day, (close, _listings) in pts.items()}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(candles, wanted))


def daily_candles(sid, key):
    """aion2.exchange's whole daily series for one material on one Server: {YYYY-MM-DD: [close, listings]}."""
    pts = get(f"/items/{key}/prices?server={sid}&bucket=1d&range=all")["points"]
    return {p["ts"][:10]: [round(p["close"]), p.get("listings")] for p in pts if p.get("close") is not None}


def market_file(results, fetched_at, prev, seeds):
    """Today's market rows plus daily closes on the HISTORY_DAYS calendar days ending today. Each day's close is
    the last price we saw that UTC day; days we never saw come from the API's daily candles when we have them."""
    days = history_window(datetime.date.fromisoformat(fetched_at[:10]))
    names, servers = {}, {}
    for sid, res in results.items():
        old_closes = prev.get("servers", {}).get(sid, {}).get("closes", {})
        closes = {}
        for key, m in res["market"].items():
            names[key] = [m["name"], m["cat"]]
            old = dict(zip(prev.get("days", []), old_closes.get(key, [])))
            seed = seeds.get((sid, key), {})
            c = [old.get(d) if old.get(d) is not None else seed.get(d) for d in days]
            c[-1] = m["row"][0]
            closes[key] = c
        servers[sid] = {"items": {k: m["row"] for k, m in res["market"].items()}, "closes": closes}
    return {"fetchedAt": fetched_at, "days": days, "names": names, "servers": servers}


def history_day(market):
    """Today's Price History day from the market rows: {serverId: {itemKey: [close, listings]}}."""
    return {sid: {key: row[:2] for key, row in s["items"].items()} for sid, s in market["servers"].items()}


def write_history(directory, name, obj):
    """One Price History object, a day or a Server's whole history, as directory/<name>.json. Sorted keys and no
    spaces, so the same object always serialises to the same bytes and a rerun uploads nothing new."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return path


def add_history_day(server_history, day, items):
    """One Server's Price History with `day` set to items ({itemKey: [close, listings]}), as
    {"days": [...sorted], "items": {itemKey: [[close, listings] or None per day]}}. The day's entries are replaced
    whole, so running the same day again converges, and folding every day object in rebuilds the file."""
    old_days = (server_history or {}).get("days", [])
    old_items = (server_history or {}).get("items", {})
    at = {d: i for i, d in enumerate(old_days)}
    days = sorted(set(old_days) | {day})
    out = {}
    for key in sorted(set(old_items) | set(items)):
        old = old_items.get(key, [])
        row = [items.get(key) if d == day else (old[at[d]] if at.get(d, len(old)) < len(old) else None) for d in days]
        if any(v is not None for v in row):
            out[key] = row
    return {"days": days, "items": out}


def server_history_from_days(day_objects, sid, start=None):
    """One Server's Price History with every day of {day: {serverId: {itemKey: [close, listings]}}} folded into
    `start` (a stored history), or rebuilt from the days alone when there is none."""
    hist = start or {"days": [], "items": {}}
    for day in sorted(day_objects):
        if day_objects[day].get(sid):
            hist = add_history_day(hist, day, day_objects[day][sid])
    return hist


def remote_json(base, key):
    """A stored object from the Worker, or None when it holds none (404)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/{key}", headers=UA), timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def local_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def previous_server_histories(sids, today):
    """Each Server's stored Price History, from the Worker when UPLOAD_BASE_URL is set, else from data/history/.
    A Server with none yet is rebuilt from the stored day objects before today. A Server whose file could not be
    read is left out, so this run uploads nothing for it rather than replacing its stored file with today alone."""
    base = os.environ.get("UPLOAD_BASE_URL", "").rstrip("/")
    directory = ROOT / "data" / "history"

    def one(sid):
        try:
            return sid, (remote_json(base, f"data/history/server/{sid}.json") if base
                         else local_json(directory / "server" / f"{sid}.json"))
        except Exception as err:
            print(f"Price History for Server {sid} unreadable, not updated this run: {err}", file=sys.stderr)
            return sid, False

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        found = dict(pool.map(one, sids))
    missing = [sid for sid, hist in found.items() if hist is None]
    if missing:
        day, day_objects = datetime.date.fromisoformat(HISTORY_FIRST_DAY), {}
        while str(day) < today:
            try:
                obj = remote_json(base, f"data/history/{day}.json") if base else local_json(directory / f"{day}.json")
            except Exception as err:
                print(f"Price History day {day} unreadable, rebuilt without it: {err}", file=sys.stderr)
                obj = None
            if obj:
                day_objects[str(day)] = obj
            day += datetime.timedelta(days=1)
        for sid in missing:
            found[sid] = server_history_from_days(day_objects, sid)
    return {sid: hist for sid, hist in found.items() if hist is not False}


def write_server_histories(directory, day, today_servers, previous):
    """data/history/server/<serverId>.json for every Server with a readable stored history, today folded in."""
    for sid, prev in previous.items():
        write_history(directory, sid, add_history_day(prev, day, today_servers[sid]) if today_servers.get(sid) else prev)


def server_quna(server):
    """One Server's Kina Chest snapshot from /quna?server= plus raw history series for later reduction."""
    sid = str(server["server_id"])
    hist_series = pull_history_series(sid)
    try:
        raw = get(f"/quna?server={sid}")
    except Exception:
        return sid, {
            "serverId": sid,
            "capturedAt": None,
            "collected": False,
            "chests": [],
            "_hist_series": hist_series,
        }
    by_face = {}
    for chest in raw.get("chests") or []:
        face = chest.get("kina_amount")
        if face not in QUNA_FACES:
            continue
        price = chest.get("price")
        if price is None:
            price = chest.get("last_price")
        if price is None:
            continue
        by_face[face] = {
            "face": face,
            "ask": round(price) if float(price) == int(float(price)) else float(price),
            "listings": int(chest["listings"]) if chest.get("listings") is not None else 0,
        }
    chests = [by_face[face] for face in QUNA_FACES if face in by_face]
    return sid, {
        "serverId": sid,
        "capturedAt": raw.get("captured_at"),
        "collected": bool(raw.get("collected")) and bool(chests),
        "chests": chests,
        "source": "aion2.exchange",
        "_hist_series": hist_series,
    }


def attach_quna_histories(servers_quna, prev_quna):
    """Reduce pulled series with Quna.bestKinaPerQunaHistory; carry forward prior history when a pull failed."""
    prev_servers = prev_quna.get("servers") or {}
    pending = {}
    for sid, snap in servers_quna.items():
        series = snap.pop("_hist_series", None)
        if series is None:
            snap["history"] = previous_quna_history(prev_servers, sid)
        else:
            pending[sid] = series
    try:
        reduced = reduce_histories(pending)
    except Exception:
        for sid in pending:
            servers_quna[sid]["history"] = previous_quna_history(prev_servers, sid)
        return
    for sid in pending:
        servers_quna[sid]["history"] = as_quna_history(reduced.get(sid)) or previous_quna_history(prev_servers, sid)


def questlog_rates():
    """questlog.gg's Exchange board for every Server, keyed by game server key. {} when it can't be read.

    questlog.gg's bot check turns away GitHub's runners, so with the upload secrets set this goes through
    the Worker's /__questlog/rates instead."""
    base, token = os.environ.get("UPLOAD_BASE_URL", "").rstrip("/"), os.environ.get("UPLOAD_TOKEN", "")
    if base and token:
        req = urllib.request.Request(f"{base}/__questlog/rates", headers={**UA, "Authorization": f"Bearer {token}"})
    else:
        req = urllib.request.Request(QUESTLOG_RATES, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            rows = json.load(r)["result"]["data"]
    except Exception as err:
        print(f"questlog.gg Exchange board unreadable, so only aion2.exchange Quna Asks this run: {err}", file=sys.stderr)
        return {}
    return {str(row["serverId"]): row for row in rows if isinstance(row, dict) and row.get("serverId") is not None}


def questlog_chests(row):
    by_face = {}
    for chest in row.get("chests") or []:
        face = QUESTLOG_CHEST_FACE.get(chest.get("itemId"))
        price = chest.get("minPrice")
        if face and isinstance(price, (int, float)) and price > 0:
            by_face[face] = {"face": face, "ask": round(price) if float(price).is_integer() else float(price)}
    return [by_face[face] for face in QUNA_FACES if face in by_face]


def questlog_history(row, today):
    """questlog.gg's trend is one best Kina per Quna per day, oldest first, ending today."""
    trend = [v if isinstance(v, (int, float)) and v > 0 else None for v in (row.get("trend") or [])][-QUNA_HISTORY_DAYS:]
    days = [str(today - datetime.timedelta(days=len(trend) - 1 - i)) for i in range(len(trend))]
    return {"days": days, "bestKinaPerQuna": trend}


def fill_from_questlog(servers_quna, servers, rates, fetched_at):
    """Servers aion2.exchange did not collect take their Asks, and history when they have none, from questlog.gg."""
    game_key = {str(s["server_id"]): str(s["game_server_key"]) for s in servers}
    today = datetime.date.fromisoformat(fetched_at[:10])
    for sid, snap in servers_quna.items():
        row = rates.get(game_key.get(sid))
        if not row:
            continue
        if not snap.get("collected"):
            chests = questlog_chests(row)
            if chests:
                snap.update({"capturedAt": fetched_at, "collected": True, "chests": chests, "source": "questlog.gg"})
        if sum(1 for v in snap["history"]["bestKinaPerQuna"] if v is not None) < 2:
            hist = questlog_history(row, today)
            if sum(1 for v in hist["bestKinaPerQuna"] if v is not None) >= 2:
                snap["history"] = hist


def quna_file(servers_quna, fetched_at):
    return {"fetchedAt": fetched_at, "source": "https://aion2.exchange", "servers": servers_quna}


def main():
    recipes = json.loads((ROOT / "data" / "recipes.json").read_text(encoding="utf-8"))
    shop = json.loads((ROOT / "data" / "shop.json").read_text(encoding="utf-8"))
    daeva_pass = json.loads((ROOT / "data" / "pass.json").read_text(encoding="utf-8"))
    unique_gear = set(daeva_pass["tradeableUniqueGear"])
    wanted_items = (set(recipes["items"]) | {s["for"] for s in shop["seasonShop"]}
                    | {d["item"] for t in daeva_pass["tiers"] for d in t["drops"]})
    wanted_names = {recipes["items"][t["key"]]["name"] for t in recipes["targets"]}
    artisan_keys = sorted({k for r in recipes["recipes"].values() for k, _ in r["in"]
                           if recipes["items"].get(k, {}).get("name", "").startswith("Artisan's Splendent")})
    servers = [s for s in get("/servers")["servers"] if s["has_data"]]
    price_snapshot_at = snapshot_at()
    log(f"prices: {len(servers)} servers")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(pool.map(lambda s: try_server(s, wanted_items, wanted_names, artisan_keys, unique_gear), servers))
        quna = dict(pool.map(server_quna, servers))
    results = {sid: res for sid, res in fetched.items() if res}
    if not results:
        sys.exit("Every Server's prices failed. Nothing is written, so the live prices stay as they are.")
    failed = [sid for sid, res in fetched.items() if not res]

    log("prices: done")
    fetched_at = fetched_now()
    prev = load_previous_market()
    goods = goods_file(results, fetched_at, load_previous_goods())
    prev_quna = load_previous_quna()
    seeds = seed_closes(results, prev, datetime.date.fromisoformat(fetched_at[:10]))
    log("seed: done")
    market = market_file(results, fetched_at, prev, seeds)
    # Before the carried Servers join market, so Price History never records a price nobody saw today.
    today_history = history_day(market)
    write_history(ROOT / "data" / "history", fetched_at[:10], today_history)
    write_server_histories(ROOT / "data" / "history" / "server", fetched_at[:10], today_history,
                           previous_server_histories(sorted(today_history), fetched_at[:10]))
    log("history: done")
    for res in results.values():
        del res["market"]
        del res["goods"]
    prev_prices = load_previous_prices()
    kept = keep_previous(failed, prev_prices, prev, load_previous_goods(), market["days"])
    stale = {sid: k["fetchedAt"] for sid, k in kept.items()}
    for sid, k in kept.items():
        results[sid] = k["prices"]
        if k["market"]:
            market["servers"][sid] = k["market"]
            for key, name in k["names"].items():
                market["names"].setdefault(key, name)
        if k["goods"]:
            goods["servers"][sid] = k["goods"]
    if stale:
        market["staleServers"] = stale
    listed = [s for s in servers if str(s["server_id"]) in results]
    if failed:
        print(f"{len(failed)} Servers skipped, {len(stale)} kept their previous prices: {', '.join(failed)}", file=sys.stderr)
    out = {
        "fetchedAt": fetched_at,
        **({"snapshotAt": price_snapshot_at} if price_snapshot_at else {}),
        "source": "https://aion2.exchange",
        **({"staleServers": stale} if stale else {}),
        "servers": [{"id": str(s["server_id"]), "gameKey": str(s["game_server_key"]), "name": s["name"], "region": s["region_name"],
                     "faction": s["faction"], "race": "light" if s["faction"] == "Elyos" else "dark"} for s in listed],
        "prices": results,
    }
    attach_quna_histories(quna, prev_quna)
    fill_from_questlog(quna, servers, questlog_rates(), fetched_at)
    quna_out = quna_file(quna, fetched_at)
    log("quna: done")
    (ROOT / "data" / "prices.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (ROOT / "data" / "market.json").write_text(json.dumps(market, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (ROOT / "data" / "goods.json").write_text(json.dumps(goods, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (ROOT / "data" / "quna.json").write_text(json.dumps(quna_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    collected = sum(1 for snap in quna.values() if snap.get("collected"))
    with_hist = sum(1 for snap in quna.values() if sum(1 for v in (snap.get("history") or {}).get("bestKinaPerQuna") or [] if v is not None) >= 2)
    print(f"{len(listed)} servers, {sum(len(v['items']) for v in results.values())} item prices, "
          f"{len(market['names'])} market materials, {len(market['days'])} days of history, {len(seeds)} graphs seeded, "
          f"{collected}/{len(quna)} Quna snapshots collected, {with_hist}/{len(quna)} with history sparks")


def print_seed_plan(market_path, today):
    """Dry run, no network: how many seed requests a run on `today` would make, carrying market_path forward.
    The material set comes from market_path itself."""
    prev = json.loads(pathlib.Path(market_path).read_text(encoding="utf-8"))
    results = {sid: {"market": s["items"]} for sid, s in prev.get("servers", {}).items()}
    wanted = seed_plan(results, prev, datetime.date.fromisoformat(today))
    print(json.dumps({"requests": len(wanted), "materials": sum(len(r["market"]) for r in results.values())}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--seed-plan"]:
        print_seed_plan(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else str(datetime.datetime.now(datetime.timezone.utc).date()))
    else:
        main()
