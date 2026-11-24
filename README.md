# prediction-market-pricing-gap

Data collection for a study of the gap between Kalshi prediction market prices and option implied probabilities on the same S&P 500 outcomes.

## Status

Collection, backfill, a first alignment of the rates data, a first calibration table for the S&P 500 ladder, and matching of closing option chains with the next day ladder. The calibration table is a descriptive baseline from one pass over the data, and the matching covers three sessions. Neither is a tested finding.

The tests were written down before the sample was large enough to run them. [docs/hypotheses.md](docs/hypotheses.md), frozen on 2026-11-24, fixes the unit, filters, sample size, hypotheses and multiple testing rule. No test runs before 60 settled sessions, and every specification that is run goes into `results/results_log.csv`.

## What the collector does

`collector.py` takes one snapshot per run and writes it under `data/YYYY-MM-DD/`, named by the trading session it belongs to:

- `*_spx_options.csv.gz`: SPX option quotes from the free Cboe delayed feed (about 15 minutes delayed), limited to expiries in the next 7 days plus the December year end expiries. When Cboe refuses the request, as it has for GitHub runners since 2026-11-19, the same chain comes from Yahoo Finance, without sizes or delta.
- `*_kalshi.csv.gz`: every open Kalshi market in the S&P 500 above/below ladder (KXINXU) and the yearly range (KXINXY), with bid, ask, volume and open interest.
- `data/manifest.csv`: one row per snapshot with fetch times, the SPX level, the kind of snapshot and the source of the option chain.

There are two kinds. An intraday snapshot is taken while the index is still ticking. A closing snapshot is taken after the session ends, when the option chain holds its closing quotes until the next open, so a run any time overnight captures the same closing chain. Only one closing snapshot is kept per session. Yahoo shows the live quotes of Cboe's overnight session, which opens at 20:15 Eastern, so a Yahoo chain taken after that is labelled overnight and is not used as the closing chain.

The closing chain lines up with the next session's 16:00 above/below ladder. In the backfill, 97.3 percent of those ladders were already open by 16:15 Eastern on the previous trading day, when the option chain stops updating.

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

## S&P 500 ladder backfill and calibration

`backfill_inxu.py` has two stages. The `markets` stage lists every market in the Kalshi S&P 500 above/below series (KXINXU). The `candles` stage pulls hourly candles for the ladders that close at 16:00 Eastern, for strikes that traded at least once. Both stages can be stopped and resumed.

From the run that finished on 2026-11-15:

- 852,285 markets across 3,511 events in the series.
- 852 ladders that close at 16:00 Eastern, from August 2022 to November 2026.
- 14,543 traded strikes in those ladders and 297,477 hourly candles.

`calibrate_inxu.py` takes the bid and ask midpoint 6, 4, 2 and 1 hours before the close, keeps quotes with a spread of at most 10 cents, and compares price with how often the contract settled yes. Standard errors are clustered by event day. The full table is in `data/derived/inxu_calibration.csv`.

What the table shows, 6 hours before the close (44,966 usable quotes over 849 event days across all four snapshots):

| Price bucket | Quotes | Mean price | Settled yes | Yes minus price | Std error |
|---|---|---|---|---|---|
| 0.05 to 0.15 | 780 | 0.092 | 0.053 | -0.039 | 0.011 |
| 0.15 to 0.25 | 473 | 0.198 | 0.146 | -0.053 | 0.019 |
| 0.45 to 0.55 | 334 | 0.503 | 0.464 | -0.038 | 0.032 |
| 0.75 to 0.85 | 620 | 0.805 | 0.782 | -0.023 | 0.023 |
| 0.85 to 0.95 | 1441 | 0.914 | 0.926 | 0.013 | 0.012 |

Cheap contracts settled yes less often than their price, which is the favourite longshot pattern. Read it with these limits in mind:

- Only strikes that traded were backfilled, so untraded far strikes are missing.
- The table has 44 buckets across four snapshots and no correction for multiple tests.
- In the cheapest bucket a midpoint cannot fall below half a cent, so part of that gap is mechanical.
- The gap is negative through most of the middle of the range too, not only at the cheap end. The next section tests this.

## Yes tilt check

`test_yes_tilt.py` follows up on the negative gap in the middle of the price range. It writes `data/derived/inxu_yes_tilt.csv`.

- Pooled over prices from 0.15 to 0.85, the gap between settlement and midpoint six hours out is -0.028 with a standard error of 0.018 (757 event days). It is negative in both halves of the range and not clearly different from zero in either.
- Against the bid the same gap is -0.011 (standard error 0.018). Against the ask it is -0.045.
- On 306 days where the index level at the snapshot is known from an hourly ladder, the implied median sits 4.8 basis points above the index six hours out (standard error 0.2), while the implied mean on 280 of those days sits 4.2 basis points below it (standard error 0.5). The median is 9.1 basis points above the mean.
- The realized move from snapshot to close on those days averaged -0.8 basis points with a standard error of 4.4.

A median above the mean is what a left skewed distribution looks like. So the negative gap in the middle of the range is consistent with left skew in the quotes and does not need a general tilt toward yes. The settlement data is too noisy to say whether that skew is larger than the skew in outcomes. The implied mean ignores probability outside the quoted strikes and uses traded strikes with tight spreads only.

## Matching closing chains with the next day ladder

`match_close.py` pairs each closing snapshot (session D) with the Kalshi ladder that settles at 16:00 on the next session.

- Options: the SPXW chain expiring on that next session, which settles on the same close as the ladder. The forward comes from put call parity near the money. Out of the money midpoints are turned into implied volatilities, a smoothing spline is fitted to the smile with each strike weighted by how precisely its quote pins down the volatility, and the probability of closing at or above a strike is minus the slope of the fitted call price curve.
- Kalshi: one minute candles for each strike, keeping the bid and ask from the last candle ending at or before 16:15 Eastern on D, when the option chain stops updating. The evening quotes in the snapshot itself are not used. `ladder_spreads.py` shows why: in the hourly backfill for 2026, the hour ending 17:00 Eastern on the day before settlement has a median near the money spread of 5 cents, against 31 cents in the hour ending 18:00.
- Settlement results are added where the next session has passed.

Output from the run on 2026-11-19, for strikes with an option probability from 0.05 to 0.95 and a Kalshi spread of at most 10 cents:

| Session | Strikes | Mean spread | Mean gap | Largest gap | Option inside quote |
|---|---|---|---|---|---|
| 2026-11-16 | 30 | 0.041 | -0.0099 | 0.0184 | 0.80 |
| 2026-11-17 | 52 | 0.047 | 0.0022 | 0.0099 | 1.00 |
| 2026-11-18 | 60 | 0.049 | -0.0025 | 0.0121 | 1.00 |

The gap is the Kalshi midpoint minus the option probability. At 16:15 the next day ladder sits within about 2 cents of the option implied probability on every near the money strike, inside a quote about 5 cents wide. That pattern is what quotes set off the option market would look like, but three sessions cannot show that, and the option probabilities carry their own fitting error. The full table is in `data/derived/matched_close.csv`.

## Schedule

`.github/workflows/collect.yml` runs the collector hourly on weekdays from 14:35 to 21:35 UTC for intraday snapshots, and hourly from 22:07 UTC until 13:07 UTC the next morning for the closing snapshot. It commits new files.

GitHub runs scheduled jobs late or skips them. Of the first eleven scheduled slots, from 14:35 UTC on 2026-11-16 to 16:35 UTC on 2026-11-17, one run started, at 23:28 UTC on 2026-11-16, almost two hours after the last slot of that day. The overnight slots exist so that any single run that does start is enough to save the closing chain.

## Mac collector

GitHub has run none of the intraday slots so far, so the `mac/` folder adds a second collector that runs on a Mac during US hours.

- `mac/mac_collect.py` starts every 10 minutes through launchd and usually exits at once. It takes one intraday snapshot per US trading hour from 10:00 to 15:59 Eastern, at the first start from minute 20 of the hour, and the closing snapshot from 16:50 Eastern if GitHub has not saved it yet.
- It pulls before deciding, so it sees snapshots GitHub has already pushed, and it rebases before pushing, so the two collectors do not collide.
- The decision uses US Eastern time, so it keeps working when the Mac changes time zone. A Mac that is asleep or offline misses its slots.
- At six intraday snapshots and one closing snapshot a day, the data grows by roughly 1.2 MB per trading day.

To install, clone the repo on the Mac, stay on `main`, and run:

```
sh mac/install.sh            # installs and loads the launchd job, then prints a dry run
sh mac/install.sh uninstall  # removes it
```

The log is at `~/Library/Logs/pmgap-collect.log`. The Mac needs to be able to push to the repo without a prompt, so check that a plain `git push` works from that clone first.

## Run locally

```
pip install -r requirements.txt
python collector.py           # intraday snapshot, or the closing one if not yet saved
python collector.py --force   # snapshot even if the closing chain is already saved
python backfill_fed.py         # rebuild data/kalshi_fed, takes about 30 minutes
python build_rates_panel.py    # rebuild data/derived from the backfill
python backfill_inxu.py markets    # list the S&P 500 ladder series
python backfill_inxu.py candles    # hourly candles for closing ladders
python calibrate_inxu.py           # calibration table
python test_yes_tilt.py            # yes tilt and skew check
python ladder_spreads.py           # ladder spreads by hours before settlement
python match_close.py              # match closing chains with the next day ladder
```

## Data sources

All are free and need no API key.

- Kalshi public REST API: https://docs.kalshi.com
- Cboe delayed quotes: https://www.cboe.com/delayed_quotes/spx/quote_table
- Yahoo Finance option chains through the yfinance package, as a fallback
- Atlanta Fed Market Probability Tracker: https://www.atlantafed.org/research-and-data/data/market-probability-tracker
- New York Fed SOFR: https://www.newyorkfed.org/markets/reference-rates/sofr

## Known limits

- No closing chain was saved for the sessions of 2026-11-19, 2026-11-20 and 2026-11-23, after Cboe started refusing GitHub runners and before the Yahoo fallback existed. Amendment 1 of [docs/hypotheses.md](docs/hypotheses.md) records the change of source.
- Yahoo chains have no bid or ask sizes and no delta, and their closing chain must be caught between the close and 20:15 Eastern.

- Option quotes are delayed and Kalshi quotes are live, so the two files in a snapshot are not from the same instant. Align them in analysis using Kalshi candlesticks at the option timestamp.
- Scheduled GitHub Actions runs can start late or not at all, so intraday coverage depends on the Mac collector being awake.
- A run in the first half hour after the close is labelled intraday, although its quotes are already final. A later run still saves a separate closing snapshot.
