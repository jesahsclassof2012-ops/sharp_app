"""Read-only, UI-safe summaries of paired market-state research history."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any
from analysis.research_readiness import MAIN_GATES, MOVEMENT_GATES, sufficiency_gate
from analysis.signal_model_benchmark import CANONICAL_SIDES, LANDMARK_HORIZONS_MINUTES, LANDMARK_MAX_STALENESS_MINUTES, _quote_usable, build_market_states, landmark_coverage_audit, landmark_decision_rows

SNAPSHOTS_SQL = "SELECT game_key, signal_key, observed_at_utc, sport, matchup, event_start_utc, market, selection, selection_side, split_line, bets_pct, money_pct, money_minus_bets_gap, best_line, best_price, break_even_pct, data_quality, line_vs_split FROM public.snapshots"
RESULTS_SQL = "SELECT game_key, sport, matchup, event_start_utc, away_score, home_score, settled_at_utc FROM public.results"
CONNECTION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=60000"


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _horizon_name(minutes: int) -> str:
    return f"T-{minutes // 60}h"


def _supported_groups(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in snapshots:
        if row.get("game_key") and row.get("market") in CANONICAL_SIDES:
            groups.setdefault((str(row["game_key"]), str(row["market"])), []).append(row)
    return groups


def _quantiles(values: list[float]) -> dict[str, float | None]:
    """Deterministic linear-interpolated p50/p75/p90 for descriptive timing."""
    if not values:
        return {"p50": None, "p75": None, "p90": None}
    ordered = sorted(values)
    def value_at(percentile: float) -> float:
        position = (len(ordered) - 1) * percentile
        lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"p50": value_at(.50), "p75": value_at(.75), "p90": value_at(.90)}


def operational_landmark_coverage(snapshots: list[dict[str, Any]], states: list[dict[str, Any]], as_of: Any = None) -> dict[str, Any]:
    """As-of-aware operational capture coverage; separate from benchmark audit.

    Future decision times are pending, never capture failures.  The supplied
    states are reused from the cached paired-state build rather than rebuilt.
    """
    observed_times = [moment for row in snapshots if (moment := _timestamp(row.get("observed_at_utc")))]
    as_of_time = _timestamp(as_of) if as_of is not None else (max(observed_times) if observed_times else None)
    raw_groups = _supported_groups(snapshots)
    valid_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for state in states:
        valid_groups.setdefault((str(state["game_key"]), str(state["market"])), []).append(state)
    output: dict[str, Any] = {"as_of": as_of_time.isoformat().replace("+00:00", "Z") if as_of_time else None, "as_of_available": as_of_time is not None, "by_horizon": {}}
    for horizon in LANDMARK_HORIZONS_MINUTES:
        counts = {"observed_game_markets": len(raw_groups), "matured": 0, "pending": 0, "usable_landmark": 0, "paired_state_in_window_but_no_usable_quote": 0, "too_stale": 0, "appeared_after_decision_time": 0, "no_valid_paired_state": 0, "valid_paired_state_in_window": 0}
        stale_minutes: list[float] = []; late_minutes: list[float] = []
        for key, raw_rows in raw_groups.items():
            starts = [_timestamp(row.get("event_start_utc")) for row in raw_rows]
            event_start = next((value for value in starts if value is not None), None)
            if as_of_time is None or event_start is None:
                counts["pending"] += 1
                continue
            target = event_start.timestamp() - horizon * 60
            if target > as_of_time.timestamp():
                counts["pending"] += 1
                continue
            counts["matured"] += 1
            valid = valid_groups.get(key, [])
            if not valid:
                counts["no_valid_paired_state"] += 1
                continue
            prior = [row for row in valid if (observed := _timestamp(row.get("observed_at_utc"))) and observed.timestamp() <= target]
            if not prior:
                counts["appeared_after_decision_time"] += 1
                after = [row for row in valid if (observed := _timestamp(row.get("observed_at_utc"))) and observed.timestamp() > target]
                if after:
                    late_minutes.append((min(_timestamp(row["observed_at_utc"]) for row in after).timestamp() - target) / 60)
                continue
            window = [row for row in prior if _timestamp(row["observed_at_utc"]).timestamp() >= target - LANDMARK_MAX_STALENESS_MINUTES * 60]
            if not window:
                counts["too_stale"] += 1
                stale_minutes.append((target - max(_timestamp(row["observed_at_utc"]) for row in prior).timestamp()) / 60)
                continue
            counts["valid_paired_state_in_window"] += 1
            if any(_quote_usable(row, "canonical") for row in window):
                counts["usable_landmark"] += 1
            else:
                counts["paired_state_in_window_but_no_usable_quote"] += 1
        counts["capture_rate"] = counts["usable_landmark"] / counts["matured"] if counts["matured"] else None
        counts["stale_timing"] = {"count": len(stale_minutes), **_quantiles(stale_minutes)}
        counts["late_timing"] = {"count": len(late_minutes), **_quantiles(late_minutes)}
        output["by_horizon"][_horizon_name(horizon)] = counts
    return output


def gate_progress(gate: dict[str, Any], movement: bool = False) -> dict[str, int | bool]:
    """Display exact readiness-cohort progress without duplicating gate rules."""
    cohort = gate.get("cohort", [])
    binary = [row for row in cohort if row.get("binary_target") is not None]
    dates = {_timestamp(row.get("event_start_utc")).date().isoformat() for row in cohort if _timestamp(row.get("event_start_utc"))}
    gates = MOVEMENT_GATES if movement else MAIN_GATES
    return {"games": len({row.get("game_key") for row in cohort if row.get("game_key")}), "binary_rows": len(binary), "event_dates": len(dates), "folds": len(gate.get("folds", [])), "game_goal": gates["unique_settled_games"], "binary_goal": gates["binary_rows"], "fold_goal": gates["folds"], "event_date_goal": gates.get("distinct_event_dates"), "passed": bool(gate.get("passed"))}


def operational_capture_breakdowns(snapshots: list[dict[str, Any]], states: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate event-date, market, and per-sport operational coverage."""
    def compact(rows: list[dict[str, Any]], paired: list[dict[str, Any]]) -> dict[str, Any]:
        return operational_landmark_coverage(rows, paired)
    markets = {market: compact([row for row in snapshots if row.get("market") == market], [row for row in states if row.get("market") == market]) for market in CANONICAL_SIDES}
    sports = {}
    for sport in sorted({str(row.get("sport")) for row in snapshots if row.get("sport")}):
        scoped_rows = [row for row in snapshots if row.get("sport") == sport]
        sports[sport] = {"coverage": compact(scoped_rows, [row for row in states if row.get("sport") == sport]), "observed_game_markets": len(_supported_groups(scoped_rows)), "recorded_results": len({row.get("game_key") for row in results if row.get("sport") == sport})}
    dates = sorted({moment.date().isoformat() for row in snapshots if (moment := _timestamp(row.get("event_start_utc")))}, reverse=True)
    trend = {}
    for date in dates:
        scoped_rows = [row for row in snapshots if (moment := _timestamp(row.get("event_start_utc"))) and moment.date().isoformat() == date]
        coverage = compact(scoped_rows, [row for row in states if (moment := _timestamp(row.get("event_start_utc"))) and moment.date().isoformat() == date])
        if any(values["matured"] for values in coverage["by_horizon"].values()):
            trend[date] = coverage
        if len(trend) == 7:
            break
    return {"by_market": markets, "by_sport": sports, "by_event_date": trend}

def read_research_history(sport: str = "All sports") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url: raise RuntimeError("DATABASE_URL is required")
    import psycopg
    from psycopg.rows import dict_row
    where, params = ("", ()) if sport == "All sports" else (" WHERE sport = %s", (sport,))
    with psycopg.connect(database_url, autocommit=True, options=CONNECTION_OPTIONS, sslmode="require", row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SNAPSHOTS_SQL + where, params); snapshots = [dict(row) for row in cursor.fetchall()]
            cursor.execute(RESULTS_SQL + where, params); results = [dict(row) for row in cursor.fetchall()]
    return snapshots, results

def summarize_market_state_history(snapshots: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    states, pair_audit = build_market_states(snapshots); entries, _ = landmark_decision_rows(states, results)
    by_horizon = {f"T-{hour}h": [row for row in entries if row.get("landmark_horizon_minutes") == hour * 60] for hour in (6, 3, 1)}
    readiness = {name: {"main": sufficiency_gate(rows), "movement": sufficiency_gate(rows, True)} for name, rows in by_horizon.items()}
    return {"snapshots": snapshots, "results": results, "states": states, "entries": entries, "pair_audit": pair_audit, "coverage": landmark_coverage_audit(snapshots, states), "operational_coverage": operational_landmark_coverage(snapshots, states), "operational_breakdowns": operational_capture_breakdowns(snapshots, states, results), "by_horizon": by_horizon, "readiness": readiness, "progress": {name: {"main": gate_progress(value["main"]), "movement": gate_progress(value["movement"], True)} for name, value in readiness.items()}, "summary": {"stored_observations": len(snapshots), "unique_games": len({row.get("game_key") for row in snapshots}), "unique_game_markets": len(_supported_groups(snapshots)), "valid_states": len(states), "recorded_results": len({row.get("game_key") for row in results}), "non_ok_retained": pair_audit["non_ok_data_quality_pairs"]}}
