"""Match each closing SPX option chain with the next session's Kalshi ladder.

For every closing snapshot in data/manifest.csv (session D):

1. Options. Take the SPXW chain expiring on the next session N, which settles
   on the same 16:00 index close as the Kalshi ladder. Find the forward from
   put call parity near the money, convert out of the money midpoints to
   implied volatilities, and fit a smoothing spline to the smile, weighting
   each strike by how precisely its quote pins down the volatility. The
   probability that the index closes at or above K is minus the slope of the
   fitted call price curve at K.

2. Kalshi. For every strike of the ladder that settles at 16:00 on N, pull one
   minute candles and keep the bid and ask from the last candle ending at or
   before 16:15 Eastern on D, when the option chain stops updating. The
   evening quotes in the snapshot itself are too wide to use.

3. Settlement. Where N has passed, record whether each Kalshi strike settled
   yes.

Writes data/derived/matched_close.csv, one row per session and strike, and
prints a summary per session for strikes near the money (option probability
from 0.05 to 0.95) with a Kalshi spread of at most 10 cents: the mean and
largest gap between the Kalshi midpoint and the option probability, and the
share of strikes where the option probability lies inside the Kalshi quote. With only a few sessions the comparison is a
plumbing check and not a result.

Usage:
  python match_close.py
"""
import csv
import datetime as dt
import glob
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from scipy.interpolate import UnivariateSpline
from scipy.optimize import brentq
from scipy.stats import norm

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXINXU"
ET = ZoneInfo("America/New_York")
OUT = Path("data/derived/matched_close.csv")
MATCH_TIME = dt.time(16, 15)
MIN_OTM_MID = 0.10     # drop option quotes cheaper than this
TIGHT = 0.10           # Kalshi spread at or below this counts as tight
NEAR = (0.05, 0.95)    # option probabilities counted as near the money
UA = {"User-Agent": "Mozilla/5.0 (research data collector)"}


def get(path, params=None, tries=8):
    for attempt in range(tries):
        r = requests.get(KALSHI + path, params=params, headers=UA, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}
    raise RuntimeError(f"gave up on {path}")


def pick(d, name):
    if not isinstance(d, dict):
        return None
    for k in (name, name + "_dollars", name + "_fp"):
        if d.get(k) is not None:
            return float(d[k])
    return None


# ------------------------------------------------------------------ options

def black_call(f, k, sigma, t):
    if sigma <= 0:
        return max(f - k, 0.0)
    s = sigma * np.sqrt(t)
    d1 = (np.log(f / k) + s * s / 2) / s
    return f * norm.cdf(d1) - k * norm.cdf(d1 - s)


def implied_vol(price, f, k, t):
    intrinsic = max(f - k, 0.0)
    if price <= intrinsic + 1e-6:
        return np.nan
    try:
        return brentq(lambda v: black_call(f, k, v, t) - price, 1e-4, 5.0)
    except ValueError:
        return np.nan


def option_probabilities(chain, expiry, t, strikes):
    """P(index at expiry >= K) for each K in strikes, with fit diagnostics."""
    x = chain[(chain["root"] == "SPXW") & (chain["expiry"] == expiry)].copy()
    x = x[(x["bid"] > 0) & (x["ask"] > 0)]
    x["mid"] = (x["bid"] + x["ask"]) / 2
    calls = x[x["cp"] == "C"].set_index("strike")
    puts = x[x["cp"] == "P"].set_index("strike")
    both = calls.index.intersection(puts.index)
    gap = (calls.loc[both, "mid"] - puts.loc[both, "mid"])
    near = gap.abs().nsmallest(5).index
    f = float(np.median(near + gap[near]))

    rows = []
    for k, r in puts[puts.index < f].iterrows():
        if r["mid"] >= MIN_OTM_MID:
            rows.append((k, r["mid"] + f - k, r["ask"] - r["bid"]))
    for k, r in calls[calls.index >= f].iterrows():
        if r["mid"] >= MIN_OTM_MID:
            rows.append((k, r["mid"], r["ask"] - r["bid"]))
    smile = []
    for k, c, spread in sorted(rows):
        iv = implied_vol(c, f, k, t)
        if np.isnan(iv):
            continue
        s = iv * np.sqrt(t)
        vega = f * norm.pdf((np.log(f / k) + s * s / 2) / s) * np.sqrt(t)
        sd = max(spread / 2, 0.025) / max(vega, 1e-6)   # vol error implied by the spread
        smile.append((np.log(k / f), iv, sd, k))
    smile = np.array(smile)
    lk, iv, sd = smile[:, 0], smile[:, 1], smile[:, 2]
    spline = UnivariateSpline(lk, iv, w=1 / sd, k=3, s=len(lk))
    lo, hi = smile[0, 3], smile[-1, 3]

    def call(k):
        return black_call(f, k, float(spline(np.log(k / f))), t)

    probs = {}
    for k in strikes:
        if not lo <= k <= hi:
            probs[k] = np.nan
            continue
        h = 0.5
        probs[k] = min(1.0, max(0.0, -(call(k + h) - call(k - h)) / (2 * h)))
    atm = float(spline(0.0))
    return probs, {"forward": f, "atm_iv": atm, "smile_points": len(lk),
                   "fit_lo": lo, "fit_hi": hi}


# ------------------------------------------------------------------ kalshi

def kalshi_quote(ticker, at):
    end = int(at.timestamp())
    q = {"start_ts": end - 20 * 60, "end_ts": end, "period_interval": 1}
    for path in (f"/series/{SERIES}/markets/{ticker}/candlesticks",
                 f"/historical/markets/{ticker}/candlesticks"):
        status, page = get(path, q)
        if status != 200:
            continue
        best = None
        for c in page.get("candlesticks", []):
            if c["end_period_ts"] <= end and pick(c.get("yes_bid"), "close") is not None:
                if best is None or c["end_period_ts"] > best["end_period_ts"]:
                    best = c
        if best is None:
            return None
        return {"bid": pick(best["yes_bid"], "close"), "ask": pick(best["yes_ask"], "close"),
                "stale_min": (end - best["end_period_ts"]) / 60}
    return None


def ladder_results(event):
    out = {}
    for path in ("/markets", "/historical/markets"):
        status, page = get(path, {"event_ticker": event, "limit": 1000})
        for m in page.get("markets", []) if status == 200 else []:
            out[m["ticker"]] = (m.get("result") or "", m.get("expiration_value"))
        if out:
            break
    return out


# ------------------------------------------------------------------ main

def next_ladder(kalshi, session):
    k = kalshi[kalshi["event_ticker"].str.startswith(SERIES + "-")
               & kalshi["event_ticker"].str.endswith("H1600")].copy()
    k["close"] = pd.to_datetime(k["close_time"], utc=True).dt.tz_convert(ET)
    k = k[k["close"].dt.date > session]
    if k.empty:
        return None
    first = k["close"].min()
    return k[k["close"] == first]


def main():
    manifest = pd.read_csv("data/manifest.csv")
    closes = manifest[manifest["kind"] == "close"]
    out, summary = [], []
    for r in closes.itertuples():
        session = dt.date.fromisoformat(r.date_et)
        folder = Path("data") / r.date_et
        chain = pd.read_csv(folder / f"{r.stamp_utc}_spx_options.csv.gz")
        kalshi = pd.read_csv(folder / f"{r.stamp_utc}_kalshi.csv.gz")
        ladder = next_ladder(kalshi, session)
        if ladder is None:
            print(f"{session}: no next session ladder in the snapshot")
            continue
        event = ladder["event_ticker"].iloc[0]
        settle_at = ladder["close"].iloc[0]
        match_at = dt.datetime.combine(session, MATCH_TIME, ET)
        t = (settle_at - match_at).total_seconds() / (365.25 * 86400)
        ladder = ladder[ladder["strike_type"] == "greater_or_equal"]
        strikes = sorted(ladder["floor_strike"].unique())
        probs, fit = option_probabilities(chain, settle_at.date().isoformat(), t, strikes)
        results = ladder_results(event)
        for m in ladder.sort_values("floor_strike").itertuples():
            quote = kalshi_quote(m.ticker, match_at) or {}
            res, value = results.get(m.ticker, ("", None))
            bid, ask = quote.get("bid"), quote.get("ask")
            out.append({
                "session": session.isoformat(), "settles": settle_at.date().isoformat(),
                "ticker": m.ticker, "strike": m.floor_strike,
                "kalshi_bid": bid, "kalshi_ask": ask,
                "kalshi_mid": (bid + ask) / 2 if bid is not None else None,
                "kalshi_spread": ask - bid if bid is not None else None,
                "quote_stale_min": quote.get("stale_min"),
                "option_prob": probs[m.floor_strike],
                "forward": round(fit["forward"], 2), "atm_iv": round(fit["atm_iv"], 4),
                "years_to_settle": round(t, 6),
                "result": res, "settle_value": value,
            })
            time.sleep(0.1)
        d = pd.DataFrame([o for o in out if o["session"] == session.isoformat()])
        near = d[(d["kalshi_spread"] <= TIGHT) & d["option_prob"].between(*NEAR)]
        gap = near["kalshi_mid"] - near["option_prob"]
        summary.append({
            "session": session.isoformat(), "ladder": event, "strikes": len(d),
            "kalshi_quoted": int(d["kalshi_bid"].notna().sum()),
            "near_money_tight": len(near),
            "forward": round(fit["forward"], 1), "atm_iv": round(fit["atm_iv"], 3),
            "mean_spread": round(near["kalshi_spread"].mean(), 3),
            "mean_gap": round(gap.mean(), 4), "max_abs_gap": round(gap.abs().max(), 4),
            "option_inside_quote": round(((near["option_prob"] >= near["kalshi_bid"])
                                          & (near["option_prob"] <= near["kalshi_ask"])).mean(), 3),
        })
        print(f"{session}: {event}, {len(d)} strikes", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out).to_csv(OUT, index=False)
    pd.set_option("display.width", 200)
    print(pd.DataFrame(summary).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
