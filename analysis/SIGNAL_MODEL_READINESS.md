# Signal-model readiness audit

## Executive summary

This is research infrastructure, not a production ranking model. No production
database credential was available in this environment, so no live-history
benchmark was run and no claim is made about predictive value. The deterministic
fixture suite exercises Models 0B–4, strict same-kickoff walk-forward folds,
calibration buckets, fixed edge buckets, expected value, and provenance guards
for a later read-only run.

## Market-state methodology

Raw history remains unchanged. Research now treats complementary provider-card
sides at one `(game_key, market, observed_at_utc)` as a single market state,
not independent selection baselines. Moneyline and Spread use away/home
orientation; Total uses over/under. The canonical away/over gap is the signed
gap. A valid split state requires a complete zero-sum pair but does not require
a sportsbook quote; quote executability is checked only for modeling or entry.

Primary cohorts are fixed T-6h, T-3h, and T-1h landmarks. Each uses only the
latest usable canonical quote at or before the target and within 45 minutes
before it; post-target observations are never used. The canonical away/over
side is always the prediction target. Market-level 60-minute movement uses an
earlier paired state, not selection identity.

Direction flips compare non-zero signed directions. Material reversals use
independent 5/10/15/20pp threshold regimes: zero and sub-threshold observations
do not reset an established regime. The former per-selection baseline remains
an aggregate audit only and is excluded from primary fitting.

The secondary 24-hour threshold-entry study uses the first *observed*
qualifying executable favored-side state under a lock policy. It distinguishes
30-minute start/end coverage grace, 45-minute internal gaps, and left/gap/right
censoring. Censored history cannot support a full first-entry or no-entry
claim; right censoring after a locked entry does not erase that entry. Later
reversals are descriptive only—no cash-out, hedge, or flip economics are
invented.

All landmark cohorts remain separate under strict chronological game-grouped
validation. The price baseline remains one-sided implied probability: current
storage lacks defensible same-book paired-price provenance for no-vig pricing.
These rules establish research readiness only; they make no profitability,
predictive-value, or production-readiness claim.

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
