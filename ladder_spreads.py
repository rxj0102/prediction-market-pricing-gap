"""Near the money spreads of the S&P 500 16:00 ladder by hours before settlement.

Uses the hourly candles from backfill_inxu.py. For each ladder and hour, takes
strikes with a midpoint from 0.20 to 0.80 and records the median spread and
how many of those strikes are quoted at 10 cents or tighter. Prints the median
across ladders by year for selected hours. Hours before settlement are counted
from the candle's end, so 23 is the hour ending 17:00 Eastern on the day
before a ladder settles.

Usage:
  python ladder_spreads.py
"""
import glob
import sys

import pandas as pd

HOURS = [23, 22, 20, 18, 16, 6, 4, 2, 1]


def main():
    m = pd.read_csv("data/kalshi_inxu/markets.csv.gz", low_memory=False,
                    usecols=["ticker", "event_ticker", "close_time"])
    c = pd.concat((pd.read_csv(f) for f in glob.glob("data/kalshi_inxu/candles/*.csv.gz")),
                  ignore_index=True).merge(m, on="ticker")
    end = pd.to_datetime(c["end_period_utc"], utc=True)
    close = pd.to_datetime(c["close_time"], utc=True, format="ISO8601")
    c["hours_before"] = ((close - end).dt.total_seconds() / 3600).round().astype(int)
    c["year"] = close.dt.tz_convert("America/New_York").dt.year
    c["mid"] = (c["yes_bid_close"] + c["yes_ask_close"]) / 2
    c["spread"] = c["yes_ask_close"] - c["yes_bid_close"]
    near = c[c["mid"].between(0.20, 0.80) & c["hours_before"].isin(HOURS)]
    per = near.groupby(["event_ticker", "year", "hours_before"]).agg(
        median_spread=("spread", "median"),
        tight_strikes=("spread", lambda s: int((s <= 0.10).sum()))).reset_index()
    table = per.groupby(["year", "hours_before"]).agg(
        ladders=("median_spread", "size"), median_spread=("median_spread", "median"),
        tight_strikes=("tight_strikes", "median")).reset_index()
    table = table.sort_values(["year", "hours_before"], ascending=[True, False])
    print(table.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
