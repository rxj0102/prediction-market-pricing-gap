# Pre-registration: Kalshi next day ladder against SPXW option probabilities

Status: frozen on 2026-11-24, before any test was run and with three matched sessions reported in the README. Change it only by dated amendments added at the end, without editing what came before.

## Why write this now

The matching in `match_close.py` already produces gaps for three sessions. Every new session makes it easier to pick a test, a filter or a sample after seeing which one looks good. Fixing them now, while the sample is too small to test anything, keeps the later result honest.

## Unit and sample

- One session D pairs the closing SPXW chain expiring on the next session with the Kalshi KXINXU ladder that settles at 16:00 on that next session, as built by `match_close.py`.
- Strikes enter when the option probability is between 0.05 and 0.95 (`NEAR`) and the Kalshi spread is at most 10 cents (`TIGHT`). Option quotes below 0.10 are dropped before fitting (`MIN_OTM_MID`). These constants are frozen at their current values.
- Strike level results are averaged within each session first. Tests run across sessions, so each session counts once.
- Testing starts when 60 sessions with settled results are collected, about three months of trading days. No test is run before that point. Descriptive tables may be updated, and are labelled as not tested.

## Hypotheses

**H1, agreement.** The mean gap, Kalshi midpoint minus option probability, is zero.
Test: two sided t test on the session means. Supported if p is at least 0.05 after the correction below, and the mean gap lies inside half the average Kalshi spread.

**H2, accuracy.** Kalshi midpoints and option probabilities forecast settlement equally well.
Test: Brier score of each against the settled result, the paired difference averaged within each session, then a two sided t test across sessions. A significant difference names the better forecaster.

**Secondary, not tested for significance.** The gap by option probability bucket (0.05 to 0.25, 0.25 to 0.75, 0.75 to 0.95), to see whether any disagreement sits in the tails, as the favourite longshot pattern in the calibration table would suggest.

## Corrections and reporting

- Holm correction across H1 and H2.
- Every specification that is run, including ones dropped later, is a row in `results/results_log.csv` with its date and reason. The count of rows is reported next to any result.
- Results that fail are reported in the README with the same prominence as results that pass.

## What would change the plan

- If Kalshi or Cboe change the contract design, the settlement source or the quote feed, the sample stops at that date and an amendment says what follows.
- If the collector misses sessions, the missing dates are listed. They are not filled from other sources.

## Amendments

**Amendment 1, 2026-11-24. Option quote source.** From the evening of 2026-11-19, Cboe's delayed quote feed answers requests from GitHub runners with 403 Forbidden, so no closing chain was saved for the sessions of 2026-11-19, 2026-11-20 and 2026-11-23. The collector now falls back to Yahoo Finance when Cboe refuses, and records the source of every chain in the manifest. Yahoo shows the live quotes of Cboe's overnight session, which opens at 20:15 Eastern, so a Yahoo chain counts as a closing chain only if it was taken after the close and before 20:15 on the session day. Later Yahoo chains are kept and labelled overnight, and are not matched.

Consequences for the plan, fixed before any test:
- Sessions from both sources count toward the 60 session threshold.
- H1 and H2 are reported on all sessions, and again separately for each source.
- If the two sources give significantly different mean gaps on H1, that is reported as a difference between the data sources and not as evidence about the markets.
- Missed sessions are listed and not filled, as before.
