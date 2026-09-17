"""Offline, leakage-safe readiness checks for split-feature research.

This module is deliberately separate from the app.  It never writes to a
database and does not decide which live signals to show or rank.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import exp, log
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
    """Expanding folds split whole games, ordered by event start."""
    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        games[row["game_key"]].append(row)
    ordered = sorted(games.values(), key=lambda values: _time(values[0]["event_start_utc"]))
    if len(ordered) < folds + 1:
        return []
    test_size = max(1, len(ordered) // (folds + 1))
    output = []
    for index in range(1, folds + 1):
        split = index * test_size
        train_games, test_games = ordered[:split], ordered[split: min(len(ordered), split + test_size)]
        if not train_games or not test_games:
            continue
        output.append(([row for game in train_games for row in game], [row for game in test_games for row in game]))
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
