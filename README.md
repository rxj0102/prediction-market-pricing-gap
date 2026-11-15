# prediction-market-pricing-gap

Data collection for a study of the gap between Kalshi prediction market prices and option implied probabilities on the same S&P 500 outcomes.

## Status

Collection and backfill only. No analysis and no results yet.

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

## Schedule

`.github/workflows/collect.yml` runs the collector hourly on weekdays from 14:35 to 21:35 UTC and commits new files. The script skips any day on which the index has not traded.

## Run locally

```
pip install -r requirements.txt
python collector.py           # skips when the market has not traded today
python collector.py --force   # snapshot regardless, for testing
python backfill_fed.py         # rebuild data/kalshi_fed, takes about 30 minutes
```

## Data sources

Both are free and need no API key.

- Kalshi public REST API: https://docs.kalshi.com
- Cboe delayed quotes: https://www.cboe.com/delayed_quotes/spx/quote_table

## Known limits

- Option quotes are delayed and Kalshi quotes are live, so the two files in a snapshot are not from the same instant. Align them in analysis using Kalshi candlesticks at the option timestamp.
- Scheduled GitHub Actions runs can start late.
