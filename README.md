# prediction-market-pricing-gap

Data collection for a study of the gap between Kalshi prediction market prices and option implied probabilities on the same S&P 500 outcomes.

## Status

Collection only. No analysis and no results yet.

## What the collector does

`collector.py` takes one snapshot per run and writes it under `data/YYYY-MM-DD/`:

- `*_spx_options.csv.gz`: SPX option quotes from the free Cboe delayed feed (about 15 minutes delayed), limited to expiries in the next 7 days plus the December year end expiries.
- `*_kalshi.csv.gz`: every open Kalshi market in the S&P 500 above/below ladder (KXINXU) and the yearly range (KXINXY), with bid, ask, volume and open interest.
- `data/manifest.csv`: one row per snapshot with fetch times and the SPX level.

The option chain is the part that cannot be backfilled for free, which is why the job runs on a schedule. Kalshi prices can be pulled again later at one minute resolution from the public candlestick endpoints.

## Schedule

`.github/workflows/collect.yml` runs the collector hourly on weekdays from 14:35 to 21:35 UTC and commits new files. The script skips any day on which the index has not traded.

## Run locally

```
pip install -r requirements.txt
python collector.py           # skips when the market has not traded today
python collector.py --force   # snapshot regardless, for testing
```

## Data sources

Both are free and need no API key.

- Kalshi public REST API: https://docs.kalshi.com
- Cboe delayed quotes: https://www.cboe.com/delayed_quotes/spx/quote_table

## Known limits

- Option quotes are delayed and Kalshi quotes are live, so the two files in a snapshot are not from the same instant. Align them in analysis using Kalshi candlesticks at the option timestamp.
- Scheduled GitHub Actions runs can start late.
