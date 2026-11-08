"""Split data/market.json and data/quna.json into the per-Server files the page reads one at a time.

Run: python -I tools/split_market.py
Writes data/market/<serverId>.json, data/goods/<serverId>.json and data/quna/<serverId>.json, which the refresh
workflow uploads to the Worker.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def write_splits(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.json"):
        if old.stem not in files:
            old.unlink()
    for sid, body in files.items():
        (directory / f"{sid}.json").write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    market = json.loads((ROOT / "data" / "market.json").read_text(encoding="utf-8"))
    write_splits(ROOT / "data" / "market", {
        sid: {
            "fetchedAt": market["fetchedAt"],
            "days": market["days"],
            "items": {k: market["names"][k] + row + [srv["closes"].get(k, [])] for k, row in srv["items"].items()},
        }
        for sid, srv in market["servers"].items()
    })
    goods_path = ROOT / "data" / "goods.json"
    if goods_path.exists():
        goods = json.loads(goods_path.read_text(encoding="utf-8"))
        write_splits(ROOT / "data" / "goods", {
            sid: {"fetchedAt": goods["fetchedAt"], "day": goods["day"], "items": items}
            for sid, items in goods.get("servers", {}).items()
        })
    quna_path = ROOT / "data" / "quna.json"
    if quna_path.exists():
        quna = json.loads(quna_path.read_text(encoding="utf-8"))
        write_splits(ROOT / "data" / "quna", quna.get("servers", {}))
    print(f"split {len(market['servers'])} Market Servers")


if __name__ == "__main__":
    main()
