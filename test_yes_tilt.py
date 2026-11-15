"""Is the Kalshi S&P 500 ladder tilted toward yes, or is it only longshot bias?

The calibration table shows yes contracts settling below their price through
most of the price range. Three checks, all on quotes with a spread of at most
MAX_SPREAD, with one cluster per event day:

1. Pooled gap (settled yes minus midpoint) for prices from 0.15 to 0.85, and
   separately for the half below 0.50 and the half above it. Pure longshot
   bias predicts a negative gap below 0.50 and a positive gap above it. A
   tilt toward yes predicts a negative gap in both halves.

2. The same gap measured against the bid and the ask, to see whether the
   result depends on using midpoints.

3. One number per day: the ladder's implied median index level (the strike
   where the midpoint crosses 0.50) against the actual close. On days that
   also have an hourly ladder settling at the snapshot time, the index level
   at the snapshot is known, which splits the miss into the drift the ladder
   implied and the drift that happened.

Writes data/derived/inxu_yes_tilt.csv and prints the same numbers.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import calibrate_inxu as cal

OUT = Path("data/derived/inxu_yes_tilt.csv")
LOW, MID, HIGH = 0.15, 0.50, 0.85
MAX_GAP = 0.40   # widest midpoint gap allowed across the 0.50 crossing
TAIL_HIGH, TAIL_LOW = 0.95, 0.05   # quote coverage needed for an implied mean


def cluster_mean(values, clusters):
    """Mean with a standard error clustered on `clusters`."""
    values = np.asarray(values, dtype=float)
    resid = values - values.mean()
    sums = pd.Series(resid).groupby(np.asarray(clusters)).sum()
    se = np.sqrt((sums ** 2).sum()) / len(values)
    return values.mean(), se


def row(test, sample, hours, values, clusters, unit):
    mean, se = cluster_mean(values, clusters)
    return {"test": test, "sample": sample, "hours_to_close": hours, "unit": unit,
            "n": len(values), "days": len(set(clusters)), "mean": mean,
            "std_error": se, "t_stat": mean / se if se > 0 else np.nan}


def pooled(q, sample):
    rows = []
    for snap, hours in cal.SNAPSHOTS.items():
        s = q[(q["snapshot"] == snap) & (q["mid"] > LOW) & (q["mid"] <= HIGH)]
        if s["event_ticker"].nunique() < 30:
            continue
        gap = s["yes"] - s["mid"]
        low, high = s["mid"] <= MID, s["mid"] > MID
        rows.append(row("gap vs midpoint, 0.15 to 0.85", sample, hours, gap, s["event_ticker"], "probability"))
        rows.append(row("gap vs midpoint, 0.15 to 0.50", sample, hours, gap[low], s.loc[low, "event_ticker"], "probability"))
        rows.append(row("gap vs midpoint, 0.50 to 0.85", sample, hours, gap[high], s.loc[high, "event_ticker"], "probability"))
        if sample == "all years":
            rows.append(row("gap vs bid, 0.15 to 0.85", sample, hours, s["yes"] - s["yes_bid_close"], s["event_ticker"], "probability"))
            rows.append(row("gap vs ask, 0.15 to 0.85", sample, hours, s["yes"] - s["yes_ask_close"], s["event_ticker"], "probability"))
    return rows


def implied_median(g):
    """Strike where the midpoint crosses 0.50, by linear interpolation between
    the two quoted strikes on either side. None when the crossing is not
    bracketed tightly enough."""
    g = g.sort_values("strike")
    above = g[g["mid"] >= MID]
    below = g[g["mid"] < MID]
    if above.empty or below.empty:
        return None
    a, b = above.iloc[-1], below.iloc[0]      # highest strike still at or over 0.50, next one under
    if a["strike"] >= b["strike"] or a["mid"] - b["mid"] > MAX_GAP:
        return None
    w = (a["mid"] - MID) / (a["mid"] - b["mid"]) if a["mid"] > b["mid"] else 0.5
    return a["strike"] + w * (b["strike"] - a["strike"])


def implied_mean(g):
    """Mean index level implied by the ladder: lowest quoted strike plus the
    area under the quoted probabilities (trapezoid rule). None unless the
    quotes reach from at least TAIL_HIGH down to at most TAIL_LOW, so that
    little probability sits outside the quoted strikes."""
    g = g.sort_values("strike")
    k, p = g["strike"].to_numpy(), np.minimum.accumulate(g["mid"].to_numpy())
    if len(k) < 4 or p[0] < TAIL_HIGH or p[-1] > TAIL_LOW:
        return None
    return k[0] + float(((p[1:] + p[:-1]) / 2 * np.diff(k)).sum())


def settlement_levels():
    """Index level at each ladder's settlement time, keyed by (date, HH:MM)."""
    m = pd.read_csv(cal.SRC / "markets.csv.gz", low_memory=False,
                    usecols=["event_ticker", "close_time", "expiration_value"])
    m = m.drop_duplicates("event_ticker")
    close = pd.to_datetime(m["close_time"], utc=True, format="ISO8601").dt.tz_convert("America/New_York")
    m["date"], m["hm"] = close.dt.date, close.dt.strftime("%H:%M")
    m["level"] = pd.to_numeric(m["expiration_value"], errors="coerce")
    m = m.dropna(subset=["level"])
    return m.set_index(["date", "hm"])["level"].to_dict(), m


def medians(q):
    levels, events = settlement_levels()
    event_date = dict(zip(events["event_ticker"], events["date"]))
    out = []
    for (event, snap), g in q.groupby(["event_ticker", "snapshot"]):
        med = implied_median(g)
        date = event_date.get(event)
        close = levels.get((date, "16:00"))
        if med is None or close is None:
            continue
        out.append({"event_ticker": event, "snapshot": snap, "year": g["year"].iloc[0],
                    "median": med, "mean": implied_mean(g), "close": close,
                    "spot": levels.get((date, snap))})
    return pd.DataFrame(out)


def median_rows(med, sample):
    rows = []
    for snap, hours in cal.SNAPSHOTS.items():
        s = med[med["snapshot"] == snap]
        if len(s) < 30:
            continue
        miss = (s["close"] - s["median"]) / s["median"] * 1e4
        rows.append(row("close minus implied median", sample, hours, miss, s["event_ticker"], "bps"))
        rows.append(row("share of days closing above implied median, minus one half", sample, hours,
                        (s["close"] > s["median"]).astype(float) - 0.5, s["event_ticker"], "share"))
        k = s.dropna(subset=["spot"])
        if len(k) >= 30 and sample == "all years":
            rows.append(row("implied drift: median minus spot at snapshot", "days with spot", hours,
                            (k["median"] - k["spot"]) / k["spot"] * 1e4, k["event_ticker"], "bps"))
            km = k.dropna(subset=["mean"])
            if len(km) >= 30:
                rows.append(row("implied drift: mean minus spot at snapshot", "days with spot", hours,
                                (km["mean"] - km["spot"]) / km["spot"] * 1e4, km["event_ticker"], "bps"))
                rows.append(row("implied median minus implied mean", "days with spot", hours,
                                (km["median"] - km["mean"]) / km["spot"] * 1e4, km["event_ticker"], "bps"))
            rows.append(row("realized drift: close minus spot at snapshot", "days with spot", hours,
                            (k["close"] - k["spot"]) / k["spot"] * 1e4, k["event_ticker"], "bps"))
            rows.append(row("close minus implied median", "days with spot", hours,
                            (k["close"] - k["median"]) / k["median"] * 1e4, k["event_ticker"], "bps"))
    return rows


def main():
    q = cal.load()
    q = q[q["spread"] <= cal.MAX_SPREAD]
    med = medians(q)
    rows = pooled(q, "all years") + median_rows(med, "all years")
    for year in sorted(q["year"].unique()):
        rows += pooled(q[q["year"] == year], str(year))
        rows += median_rows(med[med["year"] == year], str(year))
    out = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.round(4).to_csv(OUT, index=False)
    pd.set_option("display.width", 220, "display.max_colwidth", 70)
    print(f"day and snapshot pairs with an implied median: {len(med)} "
          f"({int(med['spot'].notna().sum())} with the index level at the snapshot)")
    for sample in ["all years", "days with spot"]:
        print(f"\n== {sample}")
        print(out[out["sample"] == sample].drop(columns="sample").round(3).to_string(index=False))
    by_year = out[(out["test"].isin(["gap vs midpoint, 0.15 to 0.85", "close minus implied median"]))
                  & (out["hours_to_close"] == 6) & ~out["sample"].isin(["days with spot"])]
    print("\n== by year, 6 hours before the close")
    print(by_year.drop(columns=["hours_to_close"]).round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
