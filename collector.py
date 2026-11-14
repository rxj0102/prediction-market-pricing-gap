"""Snapshot the Kalshi S&P 500 ladders and the matching SPX option chain.

Each run writes two gzip CSV files under data/YYYY-MM-DD/ and appends one row
to data/manifest.csv. Both sources are free and need no API key:
  Kalshi public REST API (markets for the series in KALSHI_SERIES)
  Cboe delayed quotes (full SPX chain, about 15 minutes delayed)

The option chain cannot be backfilled for free, so it is the reason this job
exists. Kalshi prices can be re-pulled later at one minute resolution from the
candlestick endpoints, so the Kalshi snapshot here is a convenience copy.

Usage:
  python collector.py            run only if the cash market traded today
  python collector.py --force    run regardless (for testing)
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json"
KALSHI_SERIES = ["KXINXU", "KXINXY"]  # above/below ladder, yearly range
NEAR_DAYS = 7          # keep option expiries this many calendar days out
ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (research data collector)"}

KALSHI_FIELDS = [
    "ticker", "event_ticker", "status", "strike_type", "floor_strike",
    "cap_strike", "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars",
    "no_ask_dollars", "last_price_dollars", "volume_fp", "volume_24h_fp",
    "open_interest_fp", "open_time", "close_time",
]
OPTION_FIELDS = [
    "option", "root", "expiry", "cp", "strike", "bid", "ask", "bid_size",
    "ask_size", "iv", "delta", "volume", "open_interest", "last_trade_price",
    "last_trade_time",
]


def get_json(url, params=None, tries=6):
    for attempt in range(tries):
        r = requests.get(url, params=params, headers=UA, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"gave up on {url} after {tries} tries")


def kalshi_open_markets(series):
    rows, cursor = [], None
    while True:
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = get_json(f"{KALSHI}/markets", params)
        rows += page.get("markets", [])
        cursor = page.get("cursor")
        if not cursor or not page.get("markets"):
            return rows
        time.sleep(0.3)


def parse_occ(symbol):
    """SPXW261120C07500000 -> (SPXW, 2026-11-20, C, 7500.0)"""
    body, root = symbol[-15:], symbol[:-15]
    expiry = dt.date(2000 + int(body[:2]), int(body[2:4]), int(body[4:6]))
    return root, expiry, body[6], int(body[7:]) / 1000.0


def keep_expiry(expiry, today, year_ends):
    return today <= expiry <= today + dt.timedelta(days=NEAR_DAYS) or expiry in year_ends


def write_gz(path, fields, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    with gzip.open(path, "wt", newline="") as f:
        f.write(buf.getvalue())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    now_utc = dt.datetime.now(dt.timezone.utc)
    today = now_utc.astimezone(ET).date()

    chain = get_json(CBOE)
    data = chain["data"]
    # last_trade_time is the latest index tick in Eastern time. If it is not
    # from today the cash market has not traded (weekend, holiday, pre-open).
    last_tick = str(data.get("last_trade_time") or "")
    if not args.force and last_tick[:10] != today.isoformat():
        print(f"market not open today (last tick {last_tick}); skipping")
        return 0

    # Last listed expiry of each calendar year, for the yearly range contract.
    parsed = [(o, *parse_occ(o["option"])) for o in data["options"]]
    year_ends = {}
    for _, _, expiry, _, _ in parsed:
        if expiry.month == 12:
            year_ends[expiry.year] = max(year_ends.get(expiry.year, expiry), expiry)
    year_ends = {d for y, d in year_ends.items() if y <= today.year + 1}

    options = []
    for o, root, expiry, cp, strike in parsed:
        if keep_expiry(expiry, today, year_ends):
            options.append({**o, "root": root, "expiry": expiry.isoformat(),
                            "cp": cp, "strike": strike})

    markets = []
    for series in KALSHI_SERIES:
        markets += kalshi_open_markets(series)
    kalshi_done_utc = dt.datetime.now(dt.timezone.utc)

    day_dir = Path(args.out) / today.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_utc.strftime("%H%M%SZ")
    write_gz(day_dir / f"{stamp}_spx_options.csv.gz", OPTION_FIELDS, options)
    write_gz(day_dir / f"{stamp}_kalshi.csv.gz", KALSHI_FIELDS, markets)

    manifest = Path(args.out) / "manifest.csv"
    new = not manifest.exists()
    with manifest.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date_et", "stamp_utc", "fetch_start_utc", "kalshi_done_utc",
                        "cboe_payload_timestamp", "spx_last_tick_et", "spx_price",
                        "option_rows", "kalshi_rows", "forced"])
        w.writerow([today.isoformat(), stamp, now_utc.isoformat(timespec="seconds"),
                    kalshi_done_utc.isoformat(timespec="seconds"),
                    chain.get("timestamp"), last_tick, data.get("current_price"),
                    len(options), len(markets), int(args.force)])
    print(f"{today} {stamp}: {len(options)} option rows, {len(markets)} Kalshi rows")
    if not options or not markets:
        print("warning: one side came back empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
