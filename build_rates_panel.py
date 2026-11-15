"""Build the rates pilot panel: Kalshi Fed funds ladders against the Atlanta
Fed Market Probability Tracker (MPT), which is implied by CME options on
three month SOFR.

Step 1  Kalshi daily distribution per meeting, from data/kalshi_fed/.
        Each ladder strike k is a market on the event: upper bound of the
        target range after the meeting is above k. Quotes are bid and ask
        midpoints, forced to be non increasing in k. Strikes with no quote
        that day are filled only when their neighbours bound them tightly.
        Written to data/derived/kalshi_fed_daily.csv.gz.

Step 2  For each MPT observation date and three month reference window,
        the Kalshi implied expected average upper bound over that window:
        a day weighted average over the meetings that govern the window,
        using realized outcomes for meetings already decided.

Step 3  Convert to SOFR units with the trailing 20 day spread between the
        upper bound and SOFR, then take MPT mean minus Kalshi implied mean.
        Written to data/derived/rates_panel.csv.

The MPT file and the SOFR series are downloaded to data/external/ and are
not committed. The MPT file is licensed for personal and educational use
only, so neither it nor the panel that contains its values is redistributed.

Usage:
  python build_rates_panel.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

MPT_URL = ("https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/"
           "research-and-data/data/market-probability-tracker/mpt_histdata.xlsx")
SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
EXT = Path("data/external")
DER = Path("data/derived")
KAL = Path("data/kalshi_fed")
UA = {"User-Agent": "Mozilla/5.0 (research data collector)"}
STEP = 0.25           # ladder spacing in percentage points
BASIS_DAYS = 20       # trailing window for the upper bound minus SOFR spread
WIDE = 0.10           # widest bid ask spread allowed in a clean ladder
TRUNC = 0.05          # most probability mass allowed outside the ladder
MIN_MEETING_GAP = 35  # days; shortest spacing assumed between meetings
FILL_TOL = 0.02       # widest bound on an unquoted strike that still gets filled


def download(url, path, params=None):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, params=params, headers=UA, timeout=180)
    r.raise_for_status()
    path.write_bytes(r.content)


def third_wednesday(year, month):
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(2 - first.weekday()) % 7 + 14)


def next_imm(start):
    month = start.month + 3
    year = start.year + (month - 1) // 12
    return third_wednesday(year, (month - 1) % 12 + 1)


# ---------------------------------------------------------------- step 1

def kalshi_daily():
    markets = pd.read_csv(KAL / "markets.csv")
    candles = pd.read_csv(KAL / "candles.csv.gz")
    end = pd.to_datetime(candles["end_period_utc"], utc=True) - pd.Timedelta(seconds=1)
    candles["date"] = end.dt.tz_convert("America/New_York").dt.date
    candles = candles.merge(markets[["ticker", "event_ticker", "strike"]], on="ticker")
    candles["mid"] = (candles["yes_bid_close"] + candles["yes_ask_close"]) / 2
    candles["spread"] = candles["yes_ask_close"] - candles["yes_bid_close"]
    ladders = markets.groupby("event_ticker")["strike"].apply(lambda x: np.sort(x.unique()))
    rows = []
    for (event, date), g in candles.groupby(["event_ticker", "date"]):
        k = ladders[event]
        quoted = g.set_index("strike")["mid"].reindex(k).to_numpy()
        p, fill_error = fill_ladder(quoted)
        row = {
            "event_ticker": event,
            "date": date,
            "strikes_listed": len(k),
            "strikes_quoted": int(np.isfinite(quoted).sum()),
            "strike_min": k[0],
            "strike_max": k[-1],
            "complete": p is not None,
            "max_spread": float(g["spread"].max()),
            "mean_spread": float(g["spread"].mean()),
            "volume": float(g["volume"].sum()),
        }
        if p is not None:
            row.update({
                # Mass at or below the lowest strike is placed at that strike
                # and mass above the highest strike one step above it.
                "mean_upper": k[0] + STEP * p.sum(),
                "mass_below_ladder": 1 - p[0],
                "mass_above_ladder": p[-1],
                "fill_error_bps": fill_error * STEP * 100,
            })
        rows.append(row)
    return markets, pd.DataFrame(rows)


def fill_ladder(quoted):
    """Force the ladder to be non increasing and fill unquoted strikes.

    A strike with no quote on a given day has no candle. Its probability is
    still bounded by its quoted neighbours (1 below the ladder, 0 above it),
    so it is filled with the midpoint of those bounds when they are at most
    FILL_TOL apart. Returns (probabilities, sum of half widths of the filled
    bounds), or (None, nan) when a gap is too wide to fill.
    """
    have = np.isfinite(quoted)
    if not have.any():
        return None, np.nan
    upper = np.minimum.accumulate(np.where(have, quoted, 1.0))
    lower = np.maximum.accumulate(np.where(have, quoted, 0.0)[::-1])[::-1]
    mono = np.minimum.accumulate(np.where(have, quoted, np.inf))
    width = np.where(have, 0.0, upper - np.minimum(lower, upper))
    if (width > FILL_TOL).any():
        return None, np.nan
    p = np.where(have, mono, (upper + np.minimum(lower, upper)) / 2)
    return np.minimum.accumulate(p), float(width.sum() / 2)


def meeting_table(markets):
    """One row per meeting: decision date in Eastern time and realized upper
    bound where the settled ladder pins it down."""
    close = pd.to_datetime(markets["close_time"], utc=True).dt.tz_convert("America/New_York")
    markets = markets.assign(decision=close.dt.date)
    out = []
    for event, g in markets.groupby("event_ticker"):
        yes = g.loc[g["result"] == "yes", "strike"]
        no = g.loc[g["result"] == "no", "strike"]
        realized = np.nan
        if len(yes) and len(no) and yes.max() < no.min():
            realized = yes.max() + STEP
        out.append({"event_ticker": event, "decision": g["decision"].max(),
                    "realized_upper": realized})
    return pd.DataFrame(out).sort_values("decision").reset_index(drop=True)


# ---------------------------------------------------------------- step 2

def window_weights(start, meetings):
    """Share of days in [start, next IMM date) governed by each meeting. A
    decision takes effect the day after the meeting."""
    end = next_imm(start)
    # Meeting dates past the last listed ladder are unknown. Meetings are at
    # least five weeks apart, so the window must end within that margin.
    if end > meetings["decision"].max() + dt.timedelta(days=MIN_MEETING_GAP):
        return None
    eff = [(m.decision + dt.timedelta(days=1), m.event_ticker) for m in meetings.itertuples()]
    counts = {}
    day = start
    while day < end:
        gov = [e for d, e in eff if d <= day]
        if not gov:
            return None
        counts[gov[-1]] = counts.get(gov[-1], 0) + 1
        day += dt.timedelta(days=1)
    total = sum(counts.values())
    return {e: c / total for e, c in counts.items()}


def main():
    DER.mkdir(parents=True, exist_ok=True)
    markets, daily = kalshi_daily()
    daily.sort_values(["event_ticker", "date"]).to_csv(
        DER / "kalshi_fed_daily.csv.gz", index=False)
    meetings = meeting_table(markets)
    decision = dict(zip(meetings["event_ticker"], meetings["decision"]))
    realized = dict(zip(meetings["event_ticker"], meetings["realized_upper"]))
    meeting_days = set(meetings["decision"])
    ok = daily[daily["complete"]].set_index(["event_ticker", "date"])

    download(MPT_URL, EXT / "mpt_histdata.xlsx")
    mpt = pd.read_excel(EXT / "mpt_histdata.xlsx", sheet_name="DATA")
    mpt["date"] = pd.to_datetime(mpt["date"]).dt.date
    mpt["reference_start"] = pd.to_datetime(mpt["reference_start"]).dt.date
    target = (mpt.drop_duplicates("date").set_index("date")["target_range"]
              .str.extract(r"- (\d+)bps")[0].astype(float))
    means = mpt[mpt["field"] == "Rate: mean"][["date", "reference_start", "value"]]

    sofr_path = EXT / "sofr.json"
    download(SOFR_URL, sofr_path, {"startDate": "2023-01-01",
                                   "endDate": dt.date.today().isoformat(), "type": "rate"})
    sofr = pd.DataFrame(json.loads(sofr_path.read_text())["refRates"])
    sofr["date"] = pd.to_datetime(sofr["effectiveDate"]).dt.date
    sofr = sofr.set_index("date")["percentRate"].sort_index() * 100
    basis = (target.reindex(sofr.index).ffill() - sofr).rolling(BASIS_DAYS).mean()

    weights = {s: window_weights(s, meetings) for s in sorted(means["reference_start"].unique())}

    rows = []
    for r in means.itertuples():
        w = weights[r.reference_start]
        if w is None or r.date in meeting_days:
            continue
        mean_upper, undecided, worst_spread, worst_trunc, complete = 0.0, 0, 0.0, 0.0, True
        for event, share in w.items():
            if decision[event] < r.date:
                value = realized[event]
            elif (event, r.date) in ok.index:
                q = ok.loc[(event, r.date)]
                value, undecided = q["mean_upper"], undecided + 1
                worst_spread = max(worst_spread, q["max_spread"])
                worst_trunc = max(worst_trunc, q["mass_below_ladder"] + q["mass_above_ladder"])
            else:
                value = np.nan
            if np.isnan(value):
                complete = False
                break
            mean_upper += share * value
        if not complete or undecided == 0:
            continue
        b = basis.get(r.date, np.nan)
        rows.append({
            "date": r.date,
            "reference_start": r.reference_start,
            "days_to_start": (r.reference_start - r.date).days,
            "meetings_undecided": undecided,
            "kalshi_mean_upper_bps": mean_upper * 100,
            "basis_bps": b,
            "kalshi_implied_sofr_bps": mean_upper * 100 - b,
            "mpt_mean_bps": r.value,
            "gap_bps": r.value - (mean_upper * 100 - b),
            "worst_spread": worst_spread,
            "mass_outside_ladder": worst_trunc,
            "clean": worst_spread <= WIDE and worst_trunc <= TRUNC,
        })
    panel = pd.DataFrame(rows).dropna(subset=["gap_bps"])
    panel.to_csv(DER / "rates_panel.csv", index=False)

    print(f"kalshi daily rows: {len(daily)} ({int(daily['complete'].sum())} with a full ladder)")
    print(f"MPT date and window pairs: {len(means)}; matched panel rows: {len(panel)}")
    if len(panel):
        print(f"panel dates {panel['date'].min()} to {panel['date'].max()}, "
              f"{panel['reference_start'].nunique()} windows")
        bins = [-10**4, 0, 90, 180, 10**4]
        labels = ["window started", "0 to 90 days", "90 to 180 days", "over 180 days"]
        panel["horizon"] = pd.cut(panel["days_to_start"], bins=bins, labels=labels)
        for name, sub in (("all rows", panel), ("clean rows only", panel[panel["clean"]])):
            t = sub.groupby("horizon", observed=True)["gap_bps"].agg(
                ["count", "mean", "median", "std"]).round(1)
            print(f"\ngap in bps (MPT mean minus Kalshi implied), {name}")
            print(t.to_string())
        print(f"\nbasis (upper bound minus SOFR), bps: mean {panel['basis_bps'].mean():.1f}, "
              f"min {panel['basis_bps'].min():.1f}, max {panel['basis_bps'].max():.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
