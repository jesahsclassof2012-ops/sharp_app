"""Offline, leakage-safe readiness checks for split-feature research.

This module is deliberately separate from the app.  It never writes to a
database and does not decide which live signals to show or rank.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import exp, log, log1p
from typing import Any, Iterable

from history_store import (
    american_profit,
    baseline_eligible,
    executable_pregame,
    implied_probability,
    line_value,
    settle_selection,
)


MOVEMENT_MINUTES = 60
MOVEMENT_TOLERANCE_MINUTES = 20

# Market-state research is deliberately separate from the application's
# per-selection history semantics.  These are predeclared observation rules,
# not live scanner thresholds.
MARKET_GAP_SUM_TOLERANCE = 1e-9
SPLIT_GAP_VALUE_TOLERANCE = 1e-9
SPLIT_SHARE_SUM_TOLERANCE = 1.0
LANDMARK_HORIZONS_MINUTES = (360, 180, 60)
LANDMARK_MAX_STALENESS_MINUTES = 45
MATERIAL_REVERSAL_THRESHOLDS = (5, 10, 15, 20)
THRESHOLD_GAPS = (5, 10, 15, 20)
ENTRY_WINDOW_MINUTES = 24 * 60
ENTRY_COVERAGE_START_GRACE_MINUTES = 30
ENTRY_COVERAGE_END_GRACE_MINUTES = 30
ENTRY_COVERAGE_MAX_GAP_MINUTES = 45

CANONICAL_SIDES = {
    "Moneyline": ("away", "home"),
    "Spread": ("away", "home"),
    "Total": ("over", "under"),
}


def _time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _grouped(rows: Iterable[dict[str, Any]], key) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def no_vig_probability(
    side_odds: int | None,
    opposite_odds: int | None,
    *,
    same_source: bool,
    same_line: bool,
    synchronized: bool,
) -> float | None:
    """Return a paired no-vig probability only for a defensible price pair."""
    if not (same_source and same_line and synchronized):
        return None
    q1, q2 = implied_probability(side_odds), implied_probability(opposite_odds)
    return None if q1 is None or q2 is None or q1 + q2 == 0 else q1 / (q1 + q2)


def validated_no_vig_pair(side: dict[str, Any], opposite: dict[str, Any], *, max_seconds: int = 300) -> float | None:
    """Validate real pair metadata before calculating no-vig probability.

    This intentionally cannot make current production snapshots no-vig: their
    schema has no book/source or paired opposite-price provenance.
    """
    required = ("game_key", "market", "source", "observed_at_utc", "best_price", "line", "selection")
    if any(side.get(field) is None or opposite.get(field) is None for field in required):
        return None
    if side["game_key"] != opposite["game_key"]:
        return None
    if side["market"] != opposite["market"] or side["source"] != opposite["source"]:
        return None
    if not _opposite_selections(side["market"], side["selection"], opposite["selection"]):
        return None
    if not _paired_lines_compatible(side["market"], side["line"], opposite["line"]):
        return None
    try:
        synchronized = abs((_time(side["observed_at_utc"]) - _time(opposite["observed_at_utc"])).total_seconds()) <= max_seconds
    except (TypeError, ValueError):
        return None
    return no_vig_probability(side["best_price"], opposite["best_price"], same_source=True, same_line=True, synchronized=synchronized)


def _opposite_selections(market: str, side_selection: Any, opposite_selection: Any) -> bool:
    """Require explicit opposite event sides; never infer missing provenance."""
    first = str(side_selection).strip().casefold()
    second = str(opposite_selection).strip().casefold()
    if not first or not second:
        return False
    if market in {"Moneyline", "Spread"}:
        return first != second
    if market == "Total":
        return {first, second} == {"over", "under"}
    return False


def _paired_lines_compatible(market: str, side_line: Any, opposite_line: Any) -> bool:
    """Require matched thresholds, accounting for opposite Spread signs."""
    if market == "Moneyline":
        return True
    first, second = line_value(str(side_line)), line_value(str(opposite_line))
    if first is None or second is None:
        return False
    if market == "Spread":
        return abs(first + second) < 1e-9
    if market == "Total":
        return abs(first - second) < 1e-9
    return False


def derived_split_features(row: dict[str, Any]) -> dict[str, float | None]:
    """Research diagnostics; percentages in storage are converted to fractions."""
    bets, money = row.get("bets_pct"), row.get("money_pct")
    if bets is None or money is None:
        return {"raw_gap": None, "money_bets_ratio": None, "relative_wager_ratio": None, "log_relative_wager_ratio": None}
    b, m = float(bets) / 100, float(money) / 100
    raw = float(money) - float(bets)
    if b <= 0 or m >= 1 or m <= 0 or b >= 1:
        return {"raw_gap": raw, "money_bets_ratio": None, "relative_wager_ratio": None, "log_relative_wager_ratio": None}
    ratio = m / b
    relative = m * (1 - b) / (b * (1 - m))
    return {"raw_gap": raw, "money_bets_ratio": ratio, "relative_wager_ratio": relative, "log_relative_wager_ratio": log(relative)}


def leakage_safe_movement(entry: dict[str, Any], snapshots: Iterable[dict[str, Any]]) -> float | None:
    """Closest eligible T-60 snapshot for this exact signal, never after T."""
    decision_at = _time(entry["observed_at_utc"])
    target_seconds = MOVEMENT_MINUTES * 60
    tolerance_seconds = MOVEMENT_TOLERANCE_MINUTES * 60
    candidates = []
    for row in snapshots:
        if row.get("signal_key") != entry.get("signal_key") or row.get("money_minus_bets_gap") is None:
            continue
        try:
            observed, start = _time(row["observed_at_utc"]), _time(row["event_start_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if observed >= decision_at or observed >= start:
            continue
        distance = abs((decision_at - observed).total_seconds() - target_seconds)
        if distance <= tolerance_seconds:
            candidates.append((distance, observed, row))
    if not candidates:
        return None
    prior = min(candidates, key=lambda item: (item[0], item[1]))[2]
    return float(entry["money_minus_bets_gap"]) - float(prior["money_minus_bets_gap"])


def decision_rows(snapshots: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One earliest eligible baseline decision per exact signal, with no price pairing invented."""
    snapshot_list = list(snapshots)
    scores = {row["game_key"]: row for row in results}
    first: dict[str, dict[str, Any]] = {}
    for row in sorted(snapshot_list, key=lambda value: (str(value.get("observed_at_utc")), str(value.get("signal_key")))):
        if baseline_eligible(row):
            first.setdefault(row["signal_key"], row)
    output = []
    for entry in first.values():
        result = scores.get(entry["game_key"])
        if not result:
            continue
        try:
            outcome = settle_selection(entry["market"], entry.get("selection_side") or "", line_value(entry.get("best_line")), result["away_score"], result["home_score"])
            minutes = (_time(entry["event_start_utc"]) - _time(entry["observed_at_utc"])).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            continue
        row = dict(entry)
        row.update(derived_split_features(entry))
        row.update({
            "decision_timestamp": entry["observed_at_utc"], "minutes_to_start": minutes,
            "outcome": outcome, "binary_target": 1 if outcome == "win" else 0 if outcome == "loss" else None,
            "market_probability": implied_probability(entry.get("best_price")),
            "market_probability_type": "one_sided_implied",  # provenance is not stored.
            "movement_60m": leakage_safe_movement(entry, snapshot_list),
        })
        output.append(row)
    return output


def chronological_game_folds(rows: Iterable[dict[str, Any]], folds: int = 3) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Expanding folds split whole simultaneous-kickoff groups, strictly in time."""
    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        games[row["game_key"]].append(row)
    kickoff_groups: dict[datetime, list[list[dict[str, Any]]]] = defaultdict(list)
    for game in games.values():
        kickoff_groups[_time(game[0]["event_start_utc"])].append(game)
    ordered = [kickoff_groups[start] for start in sorted(kickoff_groups)]
    if len(ordered) < 2:
        return []
    requested = min(folds, len(ordered) - 1)
    boundaries = sorted({max(1, (index * len(ordered)) // (requested + 1)) for index in range(1, requested + 1)})
    output = []
    for position, split in enumerate(boundaries):
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(ordered)
        train_groups, test_groups = ordered[:split], ordered[split:end]
        if not train_groups or not test_groups:
            continue
        train = [row for group in train_groups for game in group for row in game]
        test = [row for group in test_groups for game in group for row in game]
        # A construction invariant, not merely a test expectation.
        if max(_time(row["event_start_utc"]) for row in train) < min(_time(row["event_start_utc"]) for row in test):
            output.append((train, test))
    return output


def predicted_edge(probability: float | None, odds: int | None) -> float | None:
    implied = implied_probability(odds)
    return None if probability is None or implied is None else probability - implied


def expected_value(probability: float | None, odds: int | None) -> float | None:
    if probability is None or odds in (None, 0):
        return None
    return probability * american_profit(int(odds)) - (1 - probability)


def edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "missing"
    if edge <= 0:
        return "<=0%"
    if edge <= .02:
        return ">0% to 2%"
    if edge <= .05:
        return ">2% to 5%"
    return ">5%"


def binary_metrics(rows: Iterable[dict[str, Any]], prediction_field: str = "market_probability") -> dict[str, float | int | None]:
    """Probability metrics; pushes are intentionally absent from the target."""
    valid = [(float(row[prediction_field]), int(row["binary_target"])) for row in rows if row.get("binary_target") is not None and row.get(prediction_field) is not None]
    if not valid:
        return {"rows": 0, "log_loss": None, "brier": None}
    clipped = [(min(max(p, 1e-12), 1 - 1e-12), y) for p, y in valid]
    return {
        "rows": len(clipped),
        "log_loss": -sum(y * log(p) + (1 - y) * log(1 - p) for p, y in clipped) / len(clipped),
        "brier": sum((p - y) ** 2 for p, y in clipped) / len(clipped),
    }


MODEL_FEATURES = {
    "Model 0B": ("market_probability",),
    "Model 1": ("market_probability", "raw_gap"),
    "Model 2": ("market_probability", "bets_pct", "money_pct", "bets_money_interaction"),
    "Model 3": ("market_probability", "bets_pct", "money_pct", "bets_money_interaction", "log_minutes_to_start"),
    "Model 4": ("market_probability", "bets_pct", "money_pct", "bets_money_interaction", "log_minutes_to_start", "movement_60m"),
}


def model_features(row: dict[str, Any], model: str) -> list[float] | None:
    """Fixed, predeclared feature construction; no outcome-dependent choices."""
    values = dict(row)
    bets, money = row.get("bets_pct"), row.get("money_pct")
    if bets is not None and money is not None:
        values["bets_money_interaction"] = (float(bets) / 100) * (float(money) / 100)
    minutes = row.get("minutes_to_start")
    if minutes is not None and float(minutes) >= 0:
        values["log_minutes_to_start"] = log1p(float(minutes))
    features = [values.get(name) for name in MODEL_FEATURES[model]]
    return None if any(value is None for value in features) else [float(value) for value in features]


class RegularizedLogisticRegression:
    """Small deterministic ridge logistic model, isolated from production deps."""
    def __init__(self, ridge: float = 1.0, learning_rate: float = .15, iterations: int = 600):
        self.ridge, self.learning_rate, self.iterations = ridge, learning_rate, iterations

    def fit(self, features: list[list[float]], targets: list[int]) -> "RegularizedLogisticRegression":
        if not features or len(set(targets)) < 2:
            raise ValueError("logistic training requires both binary outcome classes")
        width = len(features[0])
        self.means = [sum(row[column] for row in features) / len(features) for column in range(width)]
        self.scales = [max(1e-9, (sum((row[column] - self.means[column]) ** 2 for row in features) / len(features)) ** .5) for column in range(width)]
        scaled = [[(value - self.means[column]) / self.scales[column] for column, value in enumerate(row)] for row in features]
        self.weights = [0.0] * (width + 1)
        for _ in range(self.iterations):
            gradient = [0.0] * (width + 1)
            for row, target in zip(scaled, targets):
                probability = _sigmoid(self.weights[0] + sum(weight * value for weight, value in zip(self.weights[1:], row)))
                error = probability - target
                gradient[0] += error
                for column, value in enumerate(row): gradient[column + 1] += error * value
            size = len(scaled)
            self.weights[0] -= self.learning_rate * gradient[0] / size
            for column in range(width):
                self.weights[column + 1] -= self.learning_rate * (gradient[column + 1] / size + self.ridge * self.weights[column + 1] / size)
        return self

    def predict_probability(self, row: list[float]) -> float:
        scaled = [(value - self.means[index]) / self.scales[index] for index, value in enumerate(row)]
        return _sigmoid(self.weights[0] + sum(weight * value for weight, value in zip(self.weights[1:], scaled)))


def _sigmoid(value: float) -> float:
    value = min(35, max(-35, value))
    return 1 / (1 + exp(-value))


def calibration_buckets(rows: Iterable[dict[str, Any]], prediction_field: str = "prediction") -> list[dict[str, Any]]:
    """Fixed buckets; pushes are excluded from binary calibration."""
    labels = ("<40%", "40–50%", "50–60%", "60–70%", "70%+")
    groups = {label: [] for label in labels}
    for row in rows:
        if row.get("binary_target") is None or row.get(prediction_field) is None: continue
        value = float(row[prediction_field])
        label = labels[0] if value < .4 else labels[1] if value < .5 else labels[2] if value < .6 else labels[3] if value < .7 else labels[4]
        groups[label].append(row)
    return [{"bucket": label, "count": len(values), "average_prediction": (sum(float(row[prediction_field]) for row in values) / len(values) if values else None), "realized_win_rate": (sum(int(row["binary_target"]) for row in values) / len(values) if values else None)} for label, values in groups.items()]


def edge_bucket_performance(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """OOS-only aggregation. Mixed-market CLV stays unavailable rather than averaged."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: groups[edge_bucket(row.get("predicted_edge"))].append(row)
    output = []
    for label in ("<=0%", ">0% to 2%", ">2% to 5%", ">5%", "missing"):
        values = groups.get(label, [])
        wins = sum(row.get("outcome") == "win" for row in values); losses = sum(row.get("outcome") == "loss" for row in values); pushes = sum(row.get("outcome") == "push" for row in values)
        units = sum(american_profit(int(row["best_price"])) if row.get("outcome") == "win" else -1 if row.get("outcome") == "loss" else 0 for row in values if row.get("best_price") not in (None, 0))
        clv_rows = [row for row in values if row.get("clv") is not None]
        markets = {row.get("market") for row in values}
        output.append({"bucket": label, "bets": len(values), "wins": wins, "losses": losses, "pushes": pushes, "win_rate": wins / (wins + losses) if wins + losses else None, "average_predicted_edge": sum(row.get("predicted_edge") or 0 for row in values) / len(values) if values else None, "units": units, "roi": units / len(values) if values else None, "average_clv": (sum(float(row["clv"]) for row in clv_rows) / len(clv_rows) if len(markets) == 1 and clv_rows else None), "positive_clv_rate": (sum(float(row["clv"]) > 0 for row in clv_rows) / len(clv_rows) if clv_rows else None)})
    return output


def select_training_edge_threshold(training_rows: Iterable[dict[str, Any]], candidates: Iterable[float] = (.0, .02, .05)) -> float:
    """Deliberately accepts only training rows; callers cannot pass test outcomes."""
    values = list(training_rows)
    if not values: raise ValueError("training rows are required")
    # A simple fixed policy-selection hook; any later use must remain training-only.
    return max(tuple(candidates), key=lambda threshold: sum((row.get("predicted_edge") or float("-inf")) >= threshold for row in values))


def _fit_predict(train: list[dict[str, Any]], test: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    trainable = [(row, model_features(row, model)) for row in train if row.get("binary_target") is not None]
    trainable = [(row, features) for row, features in trainable if features is not None]
    if len(trainable) < 2:
        return []
    features, targets = [item[1] for item in trainable], [int(item[0]["binary_target"]) for item in trainable]
    try: fitted = RegularizedLogisticRegression().fit(features, targets)
    except ValueError: return []
    output = []
    for row in test:
        values = model_features(row, model)
        if values is None: continue
        prediction = fitted.predict_probability(values)
        item = dict(row); item.update({"prediction": prediction, "predicted_edge": predicted_edge(prediction, row.get("best_price")), "expected_value": expected_value(prediction, row.get("best_price"))})
        output.append(item)
    return output


def _raw_market_predictions(test: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Model 0A: pass through the supplied, explicitly labelled baseline."""
    output = []
    for row in test:
        probability = row.get("market_probability")
        if probability is None:
            continue
        prediction = float(probability)
        item = dict(row)
        item.update({
            "prediction": prediction,
            "predicted_edge": predicted_edge(prediction, row.get("best_price")),
            "expected_value": expected_value(prediction, row.get("best_price")),
        })
        output.append(item)
    return output


def primary_model_eligible(row: dict[str, Any]) -> bool:
    """Predeclared common cohort for Models 0A–3.

    The raw baseline is deliberately restricted to the same feature-complete
    decision rows as every challenger.  That makes every primary OOS comparison
    a matched comparison rather than silently changing its test population.
    """
    if not row.get("game_key") or not row.get("signal_key") or row.get("raw_gap") is None:
        return False
    try:
        _time(row["event_start_utc"])
        _time(row["decision_timestamp"] if row.get("decision_timestamp") else row["observed_at_utc"])
    except (KeyError, TypeError, ValueError):
        return False
    return model_features(row, "Model 3") is not None


def run_walk_forward_benchmark(
    rows: Iterable[dict[str, Any]],
    folds: int = 3,
    include_movement: bool = True,
    include_primary: bool = True,
) -> dict[str, Any]:
    """Run fixed, chronological OOS models.  No test data informs fitting."""
    # Keep Models 0A–3 on exactly the same feature-complete rows in every fold.
    all_rows = [row for row in rows if primary_model_eligible(row)]
    fold_pairs = chronological_game_folds(all_rows, folds)
    predictions: dict[str, list[dict[str, Any]]] = defaultdict(list); fold_metrics = []
    for fold_id, (train, test) in enumerate(fold_pairs):
        if include_primary:
            predicted = _raw_market_predictions(test)
            for row in predicted: row["fold"] = fold_id; row["model"] = "Model 0A"
            predictions["Model 0A"].extend(predicted)
            fold_metrics.append({"model": "Model 0A", "fold": fold_id, **binary_metrics(predicted, "prediction")})
            for model in ("Model 0B", "Model 1", "Model 2", "Model 3"):
                predicted = _fit_predict(train, test, model)
                for row in predicted: row["fold"] = fold_id; row["model"] = model
                predictions[model].extend(predicted); metrics = binary_metrics(predicted, "prediction")
                fold_metrics.append({"model": model, "fold": fold_id, **metrics})
        if include_movement:
            movement_train = [row for row in train if row.get("movement_60m") is not None]
            movement_test = [row for row in test if row.get("movement_60m") is not None]
            for model in ("Model 3 movement cohort", "Model 4"):
                source = "Model 3" if model.startswith("Model 3") else model
                predicted = _fit_predict(movement_train, movement_test, source)
                for row in predicted: row["fold"] = fold_id; row["model"] = model
                predictions[model].extend(predicted); metrics = binary_metrics(predicted, "prediction")
                fold_metrics.append({"model": model, "fold": fold_id, **metrics})
    metrics = {model: binary_metrics(values, "prediction") for model, values in predictions.items()}
    baseline = metrics.get("Model 0B", {})
    for model, values in metrics.items():
        values["delta_log_loss_vs_model_0b"] = None if values["log_loss"] is None or baseline.get("log_loss") is None else values["log_loss"] - baseline["log_loss"]
        values["delta_brier_vs_model_0b"] = None if values["brier"] is None or baseline.get("brier") is None else values["brier"] - baseline["brier"]
    return {"folds": fold_pairs, "predictions": dict(predictions), "fold_metrics": fold_metrics, "metrics": metrics, "calibration": {model: calibration_buckets(values) for model, values in predictions.items()}, "edge_buckets": {model: edge_bucket_performance(values) for model, values in predictions.items()}}


def _gap(row: dict[str, Any]) -> float | None:
    """Use the stored gap where present; otherwise verify it from split shares."""
    try:
        value = row.get("money_minus_bets_gap")
        return float(value) if value is not None else float(row["money_pct"]) - float(row["bets_pct"])
    except (KeyError, TypeError, ValueError):
        return None


def build_market_states(snapshots: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Pair complementary raw sides into valid (game, market, timestamp) states.

    Quote availability is intentionally not a pair-validity requirement.  This
    keeps split coverage distinct from whether a wager could be executed.
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in snapshots:
        if row.get("game_key") and row.get("market") in CANONICAL_SIDES and row.get("observed_at_utc"):
            grouped[(str(row["game_key"]), str(row["market"]), str(row["observed_at_utc"]))].append(row)
    states: list[dict[str, Any]] = []
    audit = {"complete_pairs": 0, "incomplete_pairs": 0, "duplicate_sides": 0, "malformed_pairs": 0, "zero_sum_failures": 0, "share_sum_failures": 0, "non_ok_data_quality_pairs": 0, "canonical_non_ok_data_quality": 0, "opposite_non_ok_data_quality": 0, "valid_split_states": 0,
             "canonical_executable_quote_available": 0, "canonical_executable_quote_missing": 0, "opposite_executable_quote_available": 0, "opposite_executable_quote_missing": 0, "both_executable": 0, "neither_executable": 0}
    for (game, market, observed), rows in grouped.items():
        canonical_side, opposite_side = CANONICAL_SIDES[market]
        sides: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            sides[str(row.get("selection_side") or "").casefold()].append(row)
        if set(sides) != {canonical_side, opposite_side}:
            audit["incomplete_pairs"] += 1
            continue
        if len(sides[canonical_side]) != 1 or len(sides[opposite_side]) != 1:
            audit["duplicate_sides"] += 1
            continue
        canonical, opposite = sides[canonical_side][0], sides[opposite_side][0]
        identity_consistent = canonical.get("event_start_utc") == opposite.get("event_start_utc") and (not canonical.get("sport") or not opposite.get("sport") or canonical.get("sport") == opposite.get("sport")) and (not canonical.get("matchup") or not opposite.get("matchup") or canonical.get("matchup") == opposite.get("matchup"))
        try:
            _time(canonical.get("event_start_utc")); _time(opposite.get("event_start_utc"))
        except (TypeError, ValueError):
            identity_consistent = False
        try:
            shares = [float(value) for value in (canonical.get("bets_pct"), canonical.get("money_pct"), opposite.get("bets_pct"), opposite.get("money_pct"))]
        except (TypeError, ValueError):
            audit["malformed_pairs"] += 1; continue
        if not identity_consistent or not all(0 <= value <= 100 for value in shares):
            audit["malformed_pairs"] += 1; continue
        shares_complementary = abs(shares[0] + shares[2] - 100) <= SPLIT_SHARE_SUM_TOLERANCE and abs(shares[1] + shares[3] - 100) <= SPLIT_SHARE_SUM_TOLERANCE
        if not shares_complementary:
            audit["share_sum_failures"] += 1
            continue
        canonical_gap, opposite_gap = _gap(canonical), _gap(opposite)
        if canonical_gap is None or opposite_gap is None:
            audit["malformed_pairs"] += 1
            continue
        stored_consistent = abs(canonical_gap - (shares[1] - shares[0])) <= SPLIT_GAP_VALUE_TOLERANCE and abs(opposite_gap - (shares[3] - shares[2])) <= SPLIT_GAP_VALUE_TOLERANCE
        if not stored_consistent:
            audit["malformed_pairs"] += 1; continue
        if abs(canonical_gap + opposite_gap) > MARKET_GAP_SUM_TOLERANCE:
            audit["zero_sum_failures"] += 1
            continue
        canonical_quality_ok = canonical.get("data_quality", "OK") == "OK"
        opposite_quality_ok = opposite.get("data_quality", "OK") == "OK"
        if not canonical_quality_ok or not opposite_quality_ok:
            audit["non_ok_data_quality_pairs"] += 1
        if not canonical_quality_ok:
            audit["canonical_non_ok_data_quality"] += 1
        if not opposite_quality_ok:
            audit["opposite_non_ok_data_quality"] += 1
        signed = canonical_gap
        favored = "canonical" if signed > 0 else "opposite" if signed < 0 else "none"
        states.append({
            "game_key": game, "sport": canonical.get("sport"), "matchup": canonical.get("matchup"),
            "event_start_utc": canonical.get("event_start_utc"), "market": market, "observed_at_utc": observed,
            "canonical_selection": canonical.get("selection"), "canonical_selection_side": canonical_side,
            "opposite_selection": opposite.get("selection"), "opposite_selection_side": opposite_side,
            "canonical_bets_pct": canonical.get("bets_pct"), "canonical_money_pct": canonical.get("money_pct"), "canonical_gap": canonical_gap,
            "opposite_bets_pct": opposite.get("bets_pct"), "opposite_money_pct": opposite.get("money_pct"), "opposite_gap": opposite_gap,
            "signed_gap": signed, "gap_magnitude": abs(signed), "favored_side": favored,
            "favored_selection": canonical.get("selection") if favored == "canonical" else opposite.get("selection") if favored == "opposite" else None,
            "canonical_best_line": canonical.get("best_line"), "canonical_best_price": canonical.get("best_price"),
            "opposite_best_line": opposite.get("best_line"), "opposite_best_price": opposite.get("best_price"),
            "canonical_row": canonical, "opposite_row": opposite,
        })
        audit["complete_pairs"] += 1; audit["valid_split_states"] += 1
        canonical_usable, opposite_usable = _quote_usable(states[-1], "canonical"), _quote_usable(states[-1], "opposite")
        audit["canonical_executable_quote_available" if canonical_usable else "canonical_executable_quote_missing"] += 1
        audit["opposite_executable_quote_available" if opposite_usable else "opposite_executable_quote_missing"] += 1
        if canonical_usable and opposite_usable: audit["both_executable"] += 1
        if not canonical_usable and not opposite_usable: audit["neither_executable"] += 1
    return sorted(states, key=lambda row: (str(row["event_start_utc"]), str(row["game_key"]), row["market"], str(row["observed_at_utc"]))), audit


def _quote_usable(state: dict[str, Any], side: str = "canonical") -> bool:
    """Execution validation applies after split pairing, never during it."""
    prefix = "canonical" if side == "canonical" else "opposite"
    row = state.get(f"{prefix}_row") or {}
    try:
        odds = int(row.get("best_price"))
    except (TypeError, ValueError):
        return False
    if odds == 0 or implied_probability(odds) is None:
        return False
    try:
        pregame = _time(state["observed_at_utc"]) < _time(state["event_start_utc"])
    except (KeyError, TypeError, ValueError):
        return False
    return pregame and (state["market"] == "Moneyline" or line_value(row.get("best_line")) is not None)


def select_landmark_states(states: Iterable[dict[str, Any]], horizons: Iterable[int] = LANDMARK_HORIZONS_MINUTES) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Select the latest usable state at or before each fixed landmark only."""
    grouped = _grouped(states, lambda row: (row["game_key"], row["market"]))
    selected: list[dict[str, Any]] = []
    audit: dict[str, dict[str, int]] = {}
    for horizon in horizons:
        counts = {"no_paired_state_before_target": 0, "post_target_only": 0, "paired_state_outside_45m_window": 0, "valid_paired_state_in_window": 0, "paired_no_usable_quote": 0, "usable_landmark_rows": 0}
        for values in grouped.values():
            start = _time(values[0]["event_start_utc"]); target = start.timestamp() - horizon * 60
            prior = [row for row in values if _time(row["observed_at_utc"]).timestamp() <= target]
            window = [row for row in prior if _time(row["observed_at_utc"]).timestamp() >= target - LANDMARK_MAX_STALENESS_MINUTES * 60]
            if not prior:
                counts["post_target_only" if values else "no_paired_state_before_target"] += 1; continue
            if not window:
                counts["paired_state_outside_45m_window"] += 1; continue
            counts["valid_paired_state_in_window"] += 1
            usable = [row for row in window if _quote_usable(row, "canonical")]
            if not usable:
                counts["paired_no_usable_quote"] += 1; continue
            row = dict(max(usable, key=lambda value: _time(value["observed_at_utc"])))
            row["landmark_horizon_minutes"] = horizon
            selected.append(row); counts["usable_landmark_rows"] += 1
        audit[f"T-{horizon}m"] = counts
    return selected, audit


def landmark_coverage_audit(raw_snapshots: Iterable[dict[str, Any]], states: Iterable[dict[str, Any]], horizons: Iterable[int] = LANDMARK_HORIZONS_MINUTES) -> dict[str, dict[str, int]]:
    """Classify every raw supported game-market, even if no valid pair survived."""
    raw_groups = _grouped((row for row in raw_snapshots if row.get("market") in CANONICAL_SIDES and row.get("game_key")), lambda row: (row["game_key"], row["market"]))
    valid_groups = _grouped(states, lambda row: (row["game_key"], row["market"]))
    output = {}
    for horizon in horizons:
        counts = {"raw_game_markets_considered": len(raw_groups), "no_valid_paired_state": 0, "valid_paired_state_only_after_target": 0, "valid_paired_state_before_target_but_stale": 0, "valid_paired_state_in_45m_window": 0, "paired_state_in_window_but_no_usable_canonical_quote": 0, "usable_landmark_row": 0}
        for key, raw in raw_groups.items():
            valid = valid_groups.get(key, [])
            if not valid: counts["no_valid_paired_state"] += 1; continue
            target = _time(raw[0]["event_start_utc"]).timestamp() - horizon * 60
            prior = [row for row in valid if _time(row["observed_at_utc"]).timestamp() <= target]
            if not prior: counts["valid_paired_state_only_after_target"] += 1; continue
            window = [row for row in prior if _time(row["observed_at_utc"]).timestamp() >= target - LANDMARK_MAX_STALENESS_MINUTES * 60]
            if not window: counts["valid_paired_state_before_target_but_stale"] += 1; continue
            counts["valid_paired_state_in_45m_window"] += 1
            if not any(_quote_usable(row, "canonical") for row in window): counts["paired_state_in_window_but_no_usable_canonical_quote"] += 1
            else: counts["usable_landmark_row"] += 1
        output[f"T-{horizon}m"] = counts
    return output


def _market_state_movement_from_series(current: dict[str, Any], series: Iterable[dict[str, Any]]) -> float | None:
    """Calculate movement from one already-matched game-market series."""
    decision = _time(current["observed_at_utc"]); target = decision.timestamp() - MOVEMENT_MINUTES * 60
    candidates = []
    for state in series:
        observed = _time(state["observed_at_utc"])
        if observed >= decision or observed >= _time(state["event_start_utc"]):
            continue
        distance = abs(observed.timestamp() - target)
        if distance <= MOVEMENT_TOLERANCE_MINUTES * 60:
            candidates.append((distance, observed, state))
    if not candidates:
        return None
    prior = min(candidates, key=lambda item: (item[0], item[1]))[2]
    return float(current["signed_gap"]) - float(prior["signed_gap"])


def market_state_movement(current: dict[str, Any], states: Iterable[dict[str, Any]]) -> float | None:
    """Use prior paired market states, never signal-key history or future data."""
    series = [state for state in states if state.get("game_key") == current.get("game_key") and state.get("market") == current.get("market")]
    return _market_state_movement_from_series(current, series)


def landmark_decision_rows(states: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Settle the canonical away/over target at each fixed landmark."""
    state_list = list(states); selected, coverage = select_landmark_states(state_list)
    state_series = _grouped(state_list, lambda value: (value["game_key"], value["market"]))
    scores = {row.get("game_key"): row for row in results}; output = []
    for state in selected:
        result = scores.get(state["game_key"])
        if not result:
            continue
        row = state["canonical_row"]
        try:
            outcome = settle_selection(state["market"], state["canonical_selection_side"], line_value(row.get("best_line")), result["away_score"], result["home_score"])
            minutes = (_time(state["event_start_utc"]) - _time(state["observed_at_utc"])).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            continue
        item = dict(row)
        item.update({
            "decision_timestamp": state["observed_at_utc"], "landmark_horizon_minutes": state["landmark_horizon_minutes"],
            "raw_gap": state["signed_gap"], "signed_gap": state["signed_gap"], "gap_magnitude": state["gap_magnitude"],
            "bets_pct": state["canonical_bets_pct"], "money_pct": state["canonical_money_pct"], "minutes_to_start": minutes,
            "outcome": outcome, "binary_target": 1 if outcome == "win" else 0 if outcome == "loss" else None,
            "market_probability": implied_probability(row.get("best_price")), "market_probability_type": "one_sided_implied",
            "movement_60m": _market_state_movement_from_series(state, state_series[(state["game_key"], state["market"])]),
            "raw_canonical_signal_key": row.get("signal_key"),
            "research_row_key": f"{state['game_key']}|{state['market']}|T-{state['landmark_horizon_minutes']}",
        })
        output.append(item)
    return output, coverage


def direction_reversal_audit(states: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Audit raw flips and threshold regimes without treating them as wagers."""
    output = {}
    for key, values in _grouped(states, lambda row: (row["game_key"], row["market"])).items():
        ordered = sorted(values, key=lambda row: _time(row["observed_at_utc"]))
        last_nonzero = None; flips = []; regimes = {threshold: None for threshold in MATERIAL_REVERSAL_THRESHOLDS}; reversals = {threshold: [] for threshold in MATERIAL_REVERSAL_THRESHOLDS}
        first_nonzero = None
        for row in ordered:
            gap = float(row["signed_gap"]); sign = 1 if gap > 0 else -1 if gap < 0 else 0
            if sign:
                if first_nonzero is None: first_nonzero = sign
                if last_nonzero is not None and sign != last_nonzero: flips.append(row["observed_at_utc"])
                last_nonzero = sign
            for threshold in MATERIAL_REVERSAL_THRESHOLDS:
                if abs(gap) < threshold or not sign: continue
                if regimes[threshold] is not None and sign != regimes[threshold]: reversals[threshold].append(row["observed_at_utc"])
                regimes[threshold] = sign
        output[str(key)] = {"valid_states": len(ordered), "first_nonzero_direction": first_nonzero, "raw_flip_count": len(flips), "first_raw_flip": flips[0] if flips else None, "material": {threshold: {"count": len(values), "first": values[0] if values else None} for threshold, values in reversals.items()}}
    return output


def threshold_lock_entries(states: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]], thresholds: Iterable[int] = THRESHOLD_GAPS) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """First observed executable threshold entry; later reversals remain descriptive."""
    scores = {row.get("game_key"): row for row in results}; output = []
    thresholds = tuple(thresholds)
    audit = {str(threshold): {"series_considered": 0, "left_censored": 0, "gap_censored_before_entry": 0, "right_censored_without_entry": 0, "non_executable_qualifying_states": 0, "locked_entries": 0, "no_entry_complete_coverage": 0, "reversal_observed": 0, "no_reversal_complete_followup": 0, "reversal_followup_censored": 0} for threshold in thresholds}
    for _, values in _grouped(states, lambda row: (row["game_key"], row["market"])).items():
        ordered = sorted(values, key=lambda row: _time(row["observed_at_utc"])); start = _time(ordered[0]["event_start_utc"]); window_start = start.timestamp() - ENTRY_WINDOW_MINUTES * 60
        anchors = [row for row in ordered if abs(_time(row["observed_at_utc"]).timestamp() - window_start) <= ENTRY_COVERAGE_START_GRACE_MINUTES * 60]
        if not anchors:
            for threshold in thresholds: audit[str(threshold)]["series_considered"] += 1; audit[str(threshold)]["left_censored"] += 1
            continue
        coverage_start = min(anchors, key=lambda row: (abs(_time(row["observed_at_utc"]).timestamp() - window_start), _time(row["observed_at_utc"])))
        observed = [row for row in ordered if _time(row["observed_at_utc"]) >= _time(coverage_start["observed_at_utc"]) and _time(row["observed_at_utc"]) < start]
        end_covered = any(0 <= (start - _time(row["observed_at_utc"])).total_seconds() <= ENTRY_COVERAGE_END_GRACE_MINUTES * 60 for row in observed)
        for threshold in thresholds:
            policy = audit[str(threshold)]; policy["series_considered"] += 1
            locked = None; coverage_broken = False; previous = None
            for state in observed:
                timestamp = _time(state["observed_at_utc"])
                if previous is not None and (timestamp - previous).total_seconds() > ENTRY_COVERAGE_MAX_GAP_MINUTES * 60:
                    coverage_broken = True
                previous = timestamp
                # A pre-window grace anchor establishes observation coverage but
                # is never itself an executable policy entry.
                if timestamp.timestamp() < window_start:
                    continue
                if coverage_broken:
                    policy["gap_censored_before_entry"] += 1; break
                if abs(float(state["signed_gap"])) < threshold: continue
                side = "canonical" if state["signed_gap"] > 0 else "opposite"
                if not _quote_usable(state, side): policy["non_executable_qualifying_states"] += 1; continue
                locked = (state, side); break
            if locked is None:
                if coverage_broken: continue
                if not end_covered: policy["right_censored_without_entry"] += 1
                else: policy["no_entry_complete_coverage"] += 1
                continue
            state, side = locked; source = state[f"{side}_row"]; result = scores.get(state["game_key"])
            item = dict(source); item.update({"threshold": threshold, "entry_side": side, "decision_timestamp": state["observed_at_utc"], "signed_gap": state["signed_gap"], "locked": True, "outcome": None, "settlement_status": "unsettled"})
            if result is not None:
                try: item["outcome"] = settle_selection(state["market"], source.get("selection_side") or "", line_value(source.get("best_line")), result["away_score"], result["home_score"])
                except (KeyError, TypeError, ValueError): item["settlement_status"] = "invalid"
                else: item["settlement_status"] = "settled" if item["outcome"] in {"win", "loss", "push"} else "invalid"
            post = [row for row in observed if _time(row["observed_at_utc"]) > _time(state["observed_at_utc"])]
            entry_gap = float(state["signed_gap"])
            later = [row for row in post if (entry_gap > 0 and float(row["signed_gap"]) <= -threshold) or (entry_gap < 0 and float(row["signed_gap"]) >= threshold)]
            post_gapped = any((_time(after["observed_at_utc"]) - _time(before["observed_at_utc"])).total_seconds() > ENTRY_COVERAGE_MAX_GAP_MINUTES * 60 for before, after in zip([state, *post], [*post]))
            item["later_threshold_reversal"] = bool(later)
            item["reversal_followup_status"] = "reversal_observed" if later else "reversal_followup_censored" if post_gapped or not end_covered else "no_reversal_complete_followup"
            policy[item["reversal_followup_status"]] += 1
            output.append(item); policy["locked_entries"] += 1
    return output, audit
