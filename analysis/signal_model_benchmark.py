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


def _time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


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
    required = ("market", "source", "observed_at_utc", "best_price", "line")
    if any(side.get(field) is None or opposite.get(field) is None for field in required):
        return None
    if side["market"] != opposite["market"] or side["source"] != opposite["source"]:
        return None
    if not _paired_lines_compatible(side["market"], side["line"], opposite["line"]):
        return None
    try:
        synchronized = abs((_time(side["observed_at_utc"]) - _time(opposite["observed_at_utc"])).total_seconds()) <= max_seconds
    except (TypeError, ValueError):
        return None
    return no_vig_probability(side["best_price"], opposite["best_price"], same_source=True, same_line=True, synchronized=synchronized)


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


def run_walk_forward_benchmark(rows: Iterable[dict[str, Any]], folds: int = 3) -> dict[str, Any]:
    """Run fixed, chronological OOS models.  No test data informs fitting."""
    all_rows = list(rows); fold_pairs = chronological_game_folds(all_rows, folds)
    predictions: dict[str, list[dict[str, Any]]] = defaultdict(list); fold_metrics = []
    for fold_id, (train, test) in enumerate(fold_pairs):
        predicted = _raw_market_predictions(test)
        for row in predicted: row["fold"] = fold_id; row["model"] = "Model 0A"
        predictions["Model 0A"].extend(predicted)
        fold_metrics.append({"model": "Model 0A", "fold": fold_id, **binary_metrics(predicted, "prediction")})
        for model in ("Model 0B", "Model 1", "Model 2", "Model 3"):
            predicted = _fit_predict(train, test, model)
            for row in predicted: row["fold"] = fold_id; row["model"] = model
            predictions[model].extend(predicted); metrics = binary_metrics(predicted, "prediction")
            fold_metrics.append({"model": model, "fold": fold_id, **metrics})
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
