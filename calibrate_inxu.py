"""Calibration of the Kalshi S&P 500 above/below ladder at the 16:00 close.

Reads the backfill under data/kalshi_inxu/ and asks: when a contract is
quoted at a given price some hours before the close, how often does it
settle yes? A gap between price and frequency that grows toward the cheap
end is the favourite longshot pattern.

Method
  Quotes are bid and ask midpoints from the hourly candle that ends at the
  snapshot time on the event day (Eastern). A quote is used only when the
  spread is at most MAX_SPREAD. Strikes on the same day settle together, so
  standard errors are clustered by event day.

Limits
  Only strikes that traded at least once were backfilled, so untraded far
  strikes are missing. Midpoints of wide quotes are excluded, not corrected.

Writes data/derived/inxu_calibration.csv and prints the same table.
"""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("data/kalshi_inxu")
OUT = Path("data/derived/inxu_calibration.csv")
SNAPSHOTS = {"10:00": 6, "12:00": 4, "14:00": 2, "15:00": 1}   # hours to close
MAX_SPREAD = 0.10
EDGES = [0, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0]


def load():
    markets = pd.read_csv(SRC / "markets.csv.gz", low_memory=False,
                          usecols=["ticker", "event_ticker", "strike", "result", "close_time"])
    candles = pd.concat((pd.read_csv(f) for f in sorted(glob.glob(str(SRC / "candles/*.csv.gz")))),
                        ignore_index=True)
    candles = candles.merge(markets, on="ticker")
    end = pd.to_datetime(candles["end_period_utc"], utc=True).dt.tz_convert("America/New_York")
    close = pd.to_datetime(candles["close_time"], utc=True, format="ISO8601").dt.tz_convert(
        "America/New_York")
    candles = candles[end.dt.date == close.dt.date].copy()
    candles["snapshot"] = end[candles.index].dt.strftime("%H:%M")
    candles = candles[candles["snapshot"].isin(SNAPSHOTS)]
    candles["mid"] = (candles["yes_bid_close"] + candles["yes_ask_close"]) / 2
    candles["spread"] = candles["yes_ask_close"] - candles["yes_bid_close"]
    candles["yes"] = (candles["result"] == "yes").astype(float)
    candles["year"] = close[candles.index].dt.year
    return candles


def clustered(sub):
    """Mean of (settled yes minus price) with a standard error clustered by event."""
    d = sub["yes"] - sub["mid"]
    resid = d - d.mean()
    by_event = resid.groupby(sub["event_ticker"]).sum()
    se = np.sqrt((by_event ** 2).sum()) / len(d)
    return d.mean(), se


def table(q, label):
    rows = []
    q = q.assign(bucket=pd.cut(q["mid"], EDGES, include_lowest=True))
    for (snap, bucket), sub in q.groupby(["snapshot", "bucket"], observed=True):
        diff, se = clustered(sub)
        rows.append({
            "sample": label,
            "hours_to_close": SNAPSHOTS[snap],
            "price_bucket": f"{bucket.left:.2f} to {bucket.right:.2f}",
            "quotes": len(sub),
            "days": sub["event_ticker"].nunique(),
            "mean_price": sub["mid"].mean(),
            "settled_yes": sub["yes"].mean(),
            "yes_minus_price": diff,
            "std_error": se,
            "t_stat": diff / se if se > 0 else np.nan,
            # Taker results per contract, before fees.
            "buy_yes_at_ask": (sub["yes"] - sub["yes_ask_close"]).mean(),
            "buy_no_at_bid": (sub["yes_bid_close"] - sub["yes"]).mean(),
        })
    return pd.DataFrame(rows)


def main():
    candles = load()
    tight = candles[candles["spread"] <= MAX_SPREAD]
    print(f"snapshot quotes: {len(candles)}, with spread at most {MAX_SPREAD}: {len(tight)}, "
          f"event days: {tight['event_ticker'].nunique()}, "
          f"{tight['year'].min()} to {tight['year'].max()}")
    out = pd.concat([table(tight, "all years")] +
                    [table(tight[tight["year"] == y], str(y)) for y in sorted(tight["year"].unique())],
                    ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.round(4).to_csv(OUT, index=False)
    show = out[out["sample"] == "all years"].drop(columns="sample")
    pd.set_option("display.width", 200)
    for hours, sub in show.groupby("hours_to_close", sort=False):
        print(f"\n{hours} hours before the close")
        print(sub.drop(columns="hours_to_close").round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
