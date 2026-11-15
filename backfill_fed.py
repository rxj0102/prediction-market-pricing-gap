"""Backfill Kalshi Fed funds rate markets (series KXFED) from the public API.

Writes two files under data/kalshi_fed/:
  markets.csv       one row per market: strike, open and close time, result
  candles.csv.gz    one row per market per day: bid, ask, trade price, volume

Free public endpoints, no API key. Settled markets older than the archive
cutoff live on the /historical/ routes, so each lookup tries both.

Usage:
  python backfill_fed.py
"""
import csv
import datetime as dt
import gzip
import sys
import time
from pathlib import Path

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXFED"
OUT = Path("data/kalshi_fed")
DAY = 1440  # candle length in minutes
MAX_CANDLES = 4000  # API limit is 5000 per request
UA = {"User-Agent": "Mozilla/5.0 (research data collector)"}

MARKET_FIELDS = ["ticker", "event_ticker", "status", "strike", "strike_type", "floor_strike",
                 "cap_strike", "open_time", "close_time", "result",
                 "expiration_value", "volume_fp", "source"]
CANDLE_FIELDS = ["ticker", "end_period_utc", "yes_bid_close", "yes_ask_close",
                 "price_close", "price_mean", "volume", "open_interest"]


def get(path, params=None, tries=8):
    """Return (status, json). Retries on rate limits and server errors."""
    for attempt in range(tries):
        r = requests.get(KALSHI + path, params=params, headers=UA, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}
    raise RuntimeError(f"gave up on {path} after {tries} tries")


def paged(path, key, params):
    rows, cursor = [], None
    while True:
        q = dict(params, limit=200)
        if cursor:
            q["cursor"] = cursor
        status, page = get(path, q)
        if status != 200:
            return rows
        rows += page.get(key, [])
        cursor = page.get("cursor")
        if not cursor or not page.get(key):
            return rows
        time.sleep(0.2)


def event_markets(event_ticker):
    """Markets for one event, from the live route or the archive."""
    for source, path in (("live", "/markets"), ("historical", "/historical/markets")):
        rows = paged(path, "markets", {"event_ticker": event_ticker})
        if rows:
            return source, rows
    return None, []


def strike_from_ticker(ticker):
    """FED-22DEC-T1.25 -> 1.25. Older markets have no strike fields in the
    API response, so the ticker is the only complete source."""
    tail = ticker.rsplit("-T", 1)
    try:
        return float(tail[1]) if len(tail) == 2 else None
    except ValueError:
        return None


def ts(iso):
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def pick(d, name):
    """Live candles use name_dollars or name_fp, archive candles use name."""
    if not isinstance(d, dict):
        return None
    for k in (name, name + "_dollars", name + "_fp"):
        if d.get(k) is not None:
            return d[k]
    return None


def candles(market, source):
    ticker = market["ticker"]
    start = ts(market["open_time"])
    end = min(ts(market["close_time"]), int(time.time()))
    paths = [f"/historical/markets/{ticker}/candlesticks",
             f"/series/{SERIES}/markets/{ticker}/candlesticks"]
    if source == "live":
        paths.reverse()
    out = []
    step = MAX_CANDLES * DAY * 60
    for lo in range(start, end + 1, step):
        q = {"start_ts": lo, "end_ts": min(lo + step, end), "period_interval": DAY}
        for path in paths:
            status, page = get(path, q)
            if status == 200:
                out += page.get("candlesticks", [])
                break
        time.sleep(0.15)
    rows = []
    for c in out:
        rows.append({
            "ticker": ticker,
            "end_period_utc": dt.datetime.fromtimestamp(
                c["end_period_ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "yes_bid_close": pick(c.get("yes_bid"), "close"),
            "yes_ask_close": pick(c.get("yes_ask"), "close"),
            "price_close": pick(c.get("price"), "close"),
            "price_mean": pick(c.get("price"), "mean"),
            "volume": pick(c, "volume"),
            "open_interest": pick(c, "open_interest"),
        })
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    events = paged("/events", "events", {"series_ticker": SERIES})
    tickers = sorted({e["event_ticker"] for e in events})
    print(f"{len(tickers)} events listed for {SERIES}")

    all_markets, all_candles, empty = [], [], []
    for ev in tickers:
        source, markets = event_markets(ev)
        if not markets:
            empty.append(ev)
            print(f"{ev}: no markets returned")
            continue
        n = 0
        for m in markets:
            m["source"] = source
            m["strike"] = strike_from_ticker(m["ticker"])
            rows = candles(m, source)
            n += len(rows)
            all_candles += rows
        all_markets += markets
        print(f"{ev}: {len(markets)} markets, {n} daily candles ({source})", flush=True)

    with (OUT / "markets.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MARKET_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(all_markets, key=lambda m: m["ticker"]))
    with gzip.open(OUT / "candles.csv.gz", "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CANDLE_FIELDS)
        w.writeheader()
        w.writerows(sorted(all_candles, key=lambda r: (r["ticker"], r["end_period_utc"])))
    print(f"wrote {len(all_markets)} markets and {len(all_candles)} candles; "
          f"{len(empty)} events returned no markets: {empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
