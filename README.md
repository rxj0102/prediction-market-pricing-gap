# prediction-market-pricing-gap

Data collection for a study of the gap between Kalshi prediction market prices and option implied probabilities on the same S&P 500 outcomes.

## Status

Collection, backfill and a first alignment of the rates data. No findings yet. The numbers below describe data coverage, not a result.

## What the collector does

`collector.py` takes one snapshot per run and writes it under `data/YYYY-MM-DD/`:

- `*_spx_options.csv.gz`: SPX option quotes from the free Cboe delayed feed (about 15 minutes delayed), limited to expiries in the next 7 days plus the December year end expiries.
- `*_kalshi.csv.gz`: every open Kalshi market in the S&P 500 above/below ladder (KXINXU) and the yearly range (KXINXY), with bid, ask, volume and open interest.
- `data/manifest.csv`: one row per snapshot with fetch times and the SPX level.

The option chain is the part that cannot be backfilled for free, which is why the job runs on a schedule. Kalshi prices can be pulled again later at one minute resolution from the public candlestick endpoints.

## Fed funds backfill

`backfill_fed.py` pulls every market in the Kalshi Fed funds rate series (KXFED) with daily candles, and writes:

- `data/kalshi_fed/markets.csv`: one row per market with strike, open and close time, and result.
- `data/kalshi_fed/candles.csv.gz`: one row per market per day with closing bid and ask, trade price, volume and open interest.

The run on 2026-11-14 returned 660 markets across 52 events (42 settled) and 102,756 daily candles, from July 2021 onward. About 76 percent of those daily candles have quotes but no trade, so later analysis should use bid and ask midpoints with a spread filter and not last trade price.

## Rates pilot panel

`build_rates_panel.py` lines up the Kalshi Fed funds ladders with the Atlanta Fed Market Probability Tracker, which is implied by CME options on three month SOFR.

1. For each meeting and day it turns the ladder into a distribution using bid and ask midpoints, and writes `data/derived/kalshi_fed_daily.csv.gz`.
2. For each tracker date and three month reference window it computes the Kalshi implied expected average upper bound of the target range, weighting each meeting by the days it governs in the window.
3. It converts that to SOFR units with the trailing 20 day spread between the upper bound and SOFR, and takes the tracker mean minus the Kalshi implied mean.

Coverage from the 2026-11-14 run:

- 4,648 of 12,690 meeting days have a usable ladder. A strike with no quote on a day has no candle, and it is filled only when its quoted neighbours bound it within 2 percentage points.
- 807 date and window pairs match across the two sources, from May 2023 to November 2026.
- 117 of those pass the clean filter (every spread at most 10 cents and at most 5 percent of probability outside the ladder), across 98 dates and 11 windows.

On the clean rows the gap in means is a few basis points in either direction. The spread between the upper bound and SOFR moved between about minus 1 and 20 basis points over the sample, so a gap of this size cannot be separated from that spread with this method. Treat the panel as plumbing for the S&P 500 study and not as evidence of a premium.

The tracker file is licensed for personal and educational use only. The script downloads it to `data/external/`, which is not committed, and the panel that contains tracker values is not committed either. Run the script to rebuild both.

## Schedule

`.github/workflows/collect.yml` runs the collector hourly on weekdays from 14:35 to 21:35 UTC and commits new files. The script skips any day on which the index has not traded.

## Run locally

```
pip install -r requirements.txt
python collector.py           # skips when the market has not traded today
python collector.py --force   # snapshot regardless, for testing
python backfill_fed.py         # rebuild data/kalshi_fed, takes about 30 minutes
python build_rates_panel.py    # rebuild data/derived from the backfill
```

## Data sources

All are free and need no API key.

- Kalshi public REST API: https://docs.kalshi.com
- Cboe delayed quotes: https://www.cboe.com/delayed_quotes/spx/quote_table
- Atlanta Fed Market Probability Tracker: https://www.atlantafed.org/research-and-data/data/market-probability-tracker
- New York Fed SOFR: https://www.newyorkfed.org/markets/reference-rates/sofr

## Known limits

- Option quotes are delayed and Kalshi quotes are live, so the two files in a snapshot are not from the same instant. Align them in analysis using Kalshi candlesticks at the option timestamp.
- Scheduled GitHub Actions runs can start late.
