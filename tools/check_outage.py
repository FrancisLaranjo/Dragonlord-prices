"""Self-check for the API-outage path of tools/fetch_prices.py, with a fake failing get. No network.
Run: python -I tools/check_outage.py   (tools/check_outage.js runs it for tools/check.js)"""
import importlib.util
import pathlib
import urllib.error

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fetch_prices", here / "fetch_prices.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

fp.time.sleep = lambda s: None  # the real backoff is 35 seconds per failing get

# 1. A persistent 503 raises, counts, and after OUTAGE_AFTER in a row every get fails at once without a request.
calls = []
def down(req, timeout):
    calls.append(1)
    raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)
fp.urllib.request.urlopen = down
for _ in range(fp.OUTAGE_AFTER):
    try:
        fp.get("/items?server=1")
        raise AssertionError("a 503 must raise")
    except urllib.error.HTTPError:
        pass
assert len(calls) == fp.OUTAGE_AFTER * fp.GET_TRIES, len(calls)
try:
    fp.get("/items?server=1")
    raise AssertionError("the breaker must trip")
except fp.ApiDown:
    pass
assert len(calls) == fp.OUTAGE_AFTER * fp.GET_TRIES, "a tripped get must not request"
fp.FAILS_IN_A_ROW = 0

# 2. A failing Server is skipped, the other keeps its fresh prices.
def fake_server_prices(server, *_):
    if server["server_id"] == 2:
        raise urllib.error.HTTPError("x", 503, "down", {}, None)
    return str(server["server_id"]), {"items": {"a": [1, 1, 0]}}
fp.server_prices = fake_server_prices
fetched = dict(fp.try_server({"server_id": i}) for i in (1, 2))
assert fetched["1"]["items"] and fetched["2"] is None, fetched

# 3. The skipped Server's previous prices, market rows, closes and goods come from the live files; today gets no close.
prev_prices = {"fetchedAt": "2026-11-13T14:51+00:00", "prices": {"2": {"items": {"a": [9, 3, 0]}}, "3": {"items": {}}},
               "staleServers": {"3": "2026-11-13T10:00+00:00"}}
prev_market = {"days": ["d1", "d2"], "names": {"a": ["Ore", "Mats"]},
               "servers": {"2": {"items": {"a": [9, 3, 0, 0]}, "closes": {"a": [7, 9]}}}}
prev_goods = {"servers": {"2": {"g": ["Gem", "Goods", 5, 0, "d2"]}}}
kept = fp.keep_previous(["2", "3", "4"], prev_prices, prev_market, prev_goods, ["d2", "d3"])
assert set(kept) == {"2", "3"}, "a Server with no previous prices is left out, never written empty"
k = kept["2"]
assert k["fetchedAt"] == "2026-11-13T14:51+00:00" and k["prices"] == {"items": {"a": [9, 3, 0]}}
assert k["market"] == {"items": {"a": [9, 3, 0, 0]}, "closes": {"a": [9, None]}}, k["market"]
assert k["names"] == {"a": ["Ore", "Mats"]} and k["goods"] == {"g": ["Gem", "Goods", 5, 0, "d2"]}
assert kept["3"]["fetchedAt"] == "2026-11-13T10:00+00:00", "a Server stale before keeps its older time"
assert kept["3"]["market"] is None and kept["3"]["goods"] is None
print("ok: outage path")
