"""Pure, shared readiness gates for manual market-state research."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from analysis.signal_model_benchmark import chronological_game_folds, primary_model_eligible

MAIN_GATES = {"unique_settled_games": 100, "binary_rows": 200, "distinct_event_dates": 5, "folds": 3, "test_games_per_fold": 20}
MOVEMENT_GATES = {"unique_settled_games": 60, "binary_rows": 100, "folds": 3, "test_games_per_fold": 10}

def _date(value: Any) -> str | None:
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError): return None

def sufficiency_gate(entries: list[dict[str, Any]], movement: bool = False) -> dict[str, Any]:
    cohort = [row for row in entries if primary_model_eligible(row)]
    if movement: cohort = [row for row in cohort if row.get("movement_60m") is not None]
    binary = [row for row in cohort if row.get("binary_target") is not None]
    gates = MOVEMENT_GATES if movement else MAIN_GATES; folds = chronological_game_folds(cohort, folds=gates["folds"]); failures = []
    if len({row["game_key"] for row in cohort}) < gates["unique_settled_games"]: failures.append("unique settled games")
    if len(binary) < gates["binary_rows"]: failures.append("binary W/L rows")
    if not movement and len({_date(row.get("event_start_utc")) for row in cohort if _date(row.get("event_start_utc"))}) < gates["distinct_event_dates"]: failures.append("distinct event dates")
    if len(folds) < gates["folds"]: failures.append("valid chronological folds")
    for train, test in folds:
        if len({row["game_key"] for row in test}) < gates["test_games_per_fold"]: failures.append("test games per fold"); break
        if len({row.get("binary_target") for row in train if row.get("binary_target") is not None}) < 2: failures.append("both training outcome classes"); break
    return {"passed": not failures, "failures": failures, "folds": folds, "cohort": cohort}
