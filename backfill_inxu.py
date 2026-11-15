"""Backfill the Kalshi S&P 500 above/below ladder (series KXINXU).

Two stages, both from free public endpoints with no API key:

  python backfill_inxu.py markets
      Lists every market in the series (live and archived) and writes
      data/kalshi_inxu/markets.csv.gz: strike, result, settlement value,
      volume, open and close time.

  python backfill_inxu.py candles [--sample-days N] [--seed S]
      Pulls hourly candles for ladders that close at 16:00 Eastern, for
      strikes that traded at least once. One file per event under
      data/kalshi_inxu/candles/. Events that already have a file are
      skipped, so the stage can be stopped and resumed. With --sample-days
      it pulls a random sample of event days instead of all of them.

The full candle stage is several tens of thousands of requests. Run it
from the backfill workflow or leave it running locally.
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXINXU"
OUT = Path("data/kalshi_inxu")
ET = ZoneInfo("America/New_York")
HOUR = 60
WORKERS = 4
UA = {"User-Agent": "Mozilla/5.0 (research data collector)"}

MARKET_FIELDS = ["ticker", "event_ticker", "strike", "strike_type", "result",
                 "expiration_value", "volume_fp", "open_time", "close_time", "source"]
CANDLE_FIELDS = ["ticker", "end_period_utc", "yes_bid_close", "yes_ask_close",
                 "price_close", "volume", "open_interest"]

_local = threading.local()


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(UA)
    return _local.s


def get(path, params=None, tries=10):
    for attempt in range(tries):
        try:
            r = session().get(KALSHI + path, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(30, 1.0 * (attempt + 1)))
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}
    raise RuntimeError(f"gave up on {path} after {tries} tries")


def parse_ts(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def pick(d, name):
    if not isinstance(d, dict):
        return None
    for k in (name, name + "_dollars", name + "_fp"):
        if d.get(k) is not None:
            return d[k]
    return None


# ------------------------------------------------------------- markets

def stage_markets():
    """Resumable: each page is appended to a part file and the cursor saved,
    so a rerun continues where the last one stopped."""
    OUT.mkdir(parents=True, exist_ok=True)
    part, state = OUT / "markets.part.csv", OUT / "markets.cursor"
    done = dict(line.split("\t") for line in state.read_text().splitlines()) if state.exists() else {}
    new_file = not part.exists()
    with part.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MARKET_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for source, path in (("historical", "/historical/markets"), ("live", "/markets")):
            cursor, pages = done.get(source), 0
            if cursor == "END":
                continue
            while True:
                q = {"series_ticker": SERIES, "limit": 1000}
                if cursor:
                    q["cursor"] = cursor
                status, page = get(path, q, tries=30)
                rows = page.get("markets", []) if status == 200 else []
                for m in rows:
                    m["source"] = source
                    m["strike"] = m.get("floor_strike")
                w.writerows(rows)
                f.flush()
                pages += 1
                cursor = page.get("cursor") if status == 200 and rows else None
                done[source] = cursor or "END"
                state.write_text("\n".join(f"{k}\t{v}" for k, v in done.items()))
                if pages % 20 == 0:
                    print(f"{source}: {pages} pages this run", flush=True)
                if not cursor:
                    break
                time.sleep(0.1)
    with part.open(newline="") as f:
        seen = {m["ticker"]: m for m in csv.DictReader(f)}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MARKET_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(sorted(seen.values(), key=lambda m: m["ticker"]))
    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as f:
        f.write(buf.getvalue())
    part.unlink()
    state.unlink()
    print(f"wrote {len(seen)} markets")


# ------------------------------------------------------------- candles

def load_markets():
    with gzip.open(OUT / "markets.csv.gz", "rt", newline="") as f:
        return list(csv.DictReader(f))


def closing_events(markets):
    """Settled ladders that close at 16:00 Eastern, keyed by event."""
    events = {}
    for m in markets:
        if m["result"] not in ("yes", "no"):
            continue
        close = parse_ts(m["close_time"]).astimezone(ET)
        if (close.hour, close.minute) != (16, 0):
            continue
        events.setdefault(m["event_ticker"], []).append(m)
    return events


def market_candles(m):
    ticker = m["ticker"]
    q = {"start_ts": int(parse_ts(m["open_time"]).timestamp()),
         "end_ts": int(parse_ts(m["close_time"]).timestamp()),
         "period_interval": HOUR}
    paths = [f"/historical/markets/{ticker}/candlesticks",
             f"/series/{SERIES}/markets/{ticker}/candlesticks"]
    if m["source"] == "live":
        paths.reverse()
    for path in paths:
        status, page = get(path, q)
        if status == 200:
            return [{
                "ticker": ticker,
                "end_period_utc": dt.datetime.fromtimestamp(
                    c["end_period_ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "yes_bid_close": pick(c.get("yes_bid"), "close"),
                "yes_ask_close": pick(c.get("yes_ask"), "close"),
                "price_close": pick(c.get("price"), "close"),
                "volume": pick(c, "volume"),
                "open_interest": pick(c, "open_interest"),
            } for c in page.get("candlesticks", [])]
    return []


def stage_candles(sample_days, seed):
    out = OUT / "candles"
    out.mkdir(parents=True, exist_ok=True)
    events = closing_events(load_markets())
    names = sorted(events)
    if sample_days:
        random.Random(seed).shuffle(names)
        names = names[:sample_days]
    todo = [e for e in names if not (out / f"{e}.csv.gz").exists()]
    print(f"{len(events)} closing ladders, {len(names)} selected, {len(todo)} to pull", flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        for i, event in enumerate(todo, 1):
            traded = [m for m in events[event] if float(m["volume_fp"] or 0) > 0]
            rows = [r for chunk in pool.map(market_candles, traded) for r in chunk]
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=CANDLE_FIELDS)
            w.writeheader()
            w.writerows(rows)
            with gzip.open(out / f"{event}.csv.gz", "wt", newline="") as f:
                f.write(buf.getvalue())
            if i % 10 == 0 or i == len(todo):
                print(f"{i}/{len(todo)} {event}: {len(traded)} strikes, {len(rows)} candles",
                      flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["markets", "candles"])
    ap.add_argument("--sample-days", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if args.stage == "markets":
        stage_markets()
    else:
        stage_candles(args.sample_days, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
