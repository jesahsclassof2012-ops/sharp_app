# Signal-model readiness audit

## Executive summary

This is research infrastructure, not a production ranking model. No production
database credential was available in this environment, so no live-history
benchmark was run and no claim is made about predictive value. The deterministic
fixture suite exercises Models 0B–4, strict same-kickoff walk-forward folds,
calibration buckets, fixed edge buckets, expected value, and provenance guards
for a later read-only run.

## Data lineage and population

Source-code inspection shows that `ScoresAndOdds consensus-picks HTML` is parsed
by `parse_scoresandodds_html`. For every parsed card it is designed to emit both
provider sides (away/home, or Over/Under), including non-positive gaps.
`history_collector.collect_sport` is designed to iterate those rows and store
every valid pregame observation; it does not apply the live screen filters or
require `Money % > Bets %`. `HistoryStore.insert_snapshot` stores identity,
timestamps, market/selection, split values, price, quality, and split
percentages. Later `baseline_entries`/`analytics_rows` choose the earliest
executable, quality-OK, pregame row with `money_minus_bets_gap > 0`. Results are
captured through `result_collector` using deterministic identity and ESPN
provenance.

Code-path classification: **full parsed-provider-card population**, subject to
whatever markets/cards the upstream consensus page exposes. It is not a complete
all-sportsbook board. Actual production-history completeness was not verified
because read-only production database access was unavailable. A benchmark on
baseline decisions is conditional on the current positive-gap/executable
selection universe and cannot establish effects for filtered-out sides,
zero/negative gaps, or the complete market.

## Stored fields and provenance

Snapshots contain `game_key`, `signal_key`, sport, matchup, event start,
observation time, market, selection/side, split line, Bets %, Money %, raw gap,
best line, best price, break-even percentage, data-quality label, and line-vs-
split label.  Results contain final scores, settlement time, source, external
event id, source status, and fetch time.  Repeated observations are orderable
by UTC `observed_at_utc` and exact signal identity.  The app also stores the
last valid executable pregame quote used for CLV through the existing analytics
path.

Price provenance limitation: snapshots store one `best_price` but no sportsbook
name, paired opposite-side price, or paired same-book timestamp.  Therefore
same-book no-vig probability coverage is currently **unknown/unavailable** and
cross-book best-price pairs must not be called no-vig.  The available fallback
is a clearly labelled one-sided, vig-contaminated implied break-even probability.

## Decision and leakage rules

One row is retained per exact `signal_key`: the earliest simultaneously
eligible pregame baseline.  It includes `minutes_to_start`, raw and diagnostic
split features, entry odds/line, result, and only a prior approximate T-60 gap
within the existing ±20-minute window.  No future snapshot may provide that
movement feature and missing movement remains missing.  Train/test folds group
all rows by `game_key` and are chronologically expanding, preventing a game
from crossing folds.

## Implemented offline benchmark framework

Model 0A remains raw supplied market probability, only called no-vig when a
validated same-event, same-source, same-market, synchronized, opposite-side,
compatible-line pair is available. Model 0B fits a
regularized logistic recalibration on market probability. Models 1–4 add raw
gap; Bets/Money plus interaction; `log1p(minutes_to_start)`; then leakage-safe
movement. Model 4 is compared to a Model-3 movement cohort on exactly the same
eligible rows. Feature scaling is fit from training rows only. Folds group both
exact games and entire equal-kickoff groups, with strict `max(train start) <
min(test start)`. Primary metrics are OOS log loss and Brier score; calibration,
expected value, fixed edge buckets, and market-specific CLV/ROI are secondary.
Pushes are excluded from binary probability targets but retain their 0-unit ROI
treatment. The threshold hook accepts only training rows; no test fold may pick
a threshold, model, transformation, or regularization strength.

## Current conclusion and next evidence needed

There is insufficient live evidence in this environment for a production
ranking change. A future read-only run needs enough settled games over a
meaningful date span, recorded same-book or defensibly synchronized opposite
prices/lines, and adequate T-60 movement coverage. It should report fold-level
uncertainty clustered by `game_key`, market/sport stability, calibration, and
predeclared edge-bucket CLV/ROI before any production proposal. No real
predictive, CLV, ROI, or calibration conclusion is stated here.
