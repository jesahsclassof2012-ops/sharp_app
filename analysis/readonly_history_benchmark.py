"""Manual, aggregate-only benchmark for a restricted PostgreSQL reader.

This module is intentionally separate from the application.  It never creates
``HistoryStore`` because that class initializes schema objects.  The only SQL
below is fixed SELECT/SHOW text and the connection is read-only from startup.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import random
from typing import Any, Iterable

from analysis.signal_model_benchmark import (
    binary_metrics,
    build_market_states,
    chronological_game_folds,
    decision_rows,
    direction_reversal_audit,
    landmark_decision_rows,
    landmark_coverage_audit,
    primary_model_eligible,
    run_walk_forward_benchmark,
    threshold_lock_entries,
)
from history_store import american_profit, calculate_clv, executable_pregame, line_value


READER_ROLE = "sharp_benchmark_reader"
OUTPUT_PATH = Path("analysis/output/readonly_history_benchmark.md")
CONNECTION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=5000"

CURRENT_USER_SQL = "SELECT current_user AS current_user"
READ_ONLY_SQL = "SHOW transaction_read_only"
SNAPSHOTS_SQL = """
SELECT game_key, signal_key, observed_at_utc, sport, matchup, event_start_utc,
       market, selection, selection_side, split_line, bets_pct, money_pct,
       money_minus_bets_gap, best_line, best_price, break_even_pct,
       data_quality, line_vs_split
FROM public.snapshots
ORDER BY observed_at_utc
"""
RESULTS_SQL = """
SELECT game_key, sport, matchup, event_start_utc, away_score, home_score,
       settled_at_utc
FROM public.results
"""

FORBIDDEN_SQL = (
    "insert", "update", "delete", "merge", "copy", "truncate", "alter",
    "create", "drop", "grant", "revoke", "vacuum", "analyze", "call", "do",
    "set role", "security definer", "begin", "commit", "rollback",
)

MAIN_GATES = {
    "unique_settled_games": 100,
    "binary_rows": 200,
    "distinct_event_dates": 5,
    "folds": 3,
    "test_games_per_fold": 20,
}
MOVEMENT_GATES = {
    "unique_settled_games": 60,
    "binary_rows": 100,
    "folds": 3,
    "test_games_per_fold": 10,
}
MIN_DIAGNOSTIC_ROWS = 20
MIN_ROI_BOOTSTRAP_GAMES = 20
REPORT_AUDIT_KEYS = {
    "total_snapshots", "total_results", "earliest_snapshot", "latest_snapshot",
    "earliest_event_start", "latest_event_start", "sports", "markets",
    "result_coverage_by_sport", "result_coverage_by_market", "distinct_games",
    "distinct_signals", "settled_games", "unsettled_games", "baseline_decisions",
    "wins", "losses", "pushes", "binary_rows", "event_dates", "movement_rows",
    "movement_games", "movement_coverage_pct", "valid_pct", "time_to_start",
    "repeated_snapshot_counts", "same_kickoff_groups", "missing_entry_features",
}


class BenchmarkSafetyError(RuntimeError):
    """A safety invariant failed before any analytical read should proceed."""


def _assert_fixed_read_only_sql() -> None:
    for query in (CURRENT_USER_SQL, READ_ONLY_SQL, SNAPSHOTS_SQL, RESULTS_SQL):
        normalized = " ".join(query.lower().split())
        if not (normalized.startswith("select") or normalized.startswith("show")):
            raise BenchmarkSafetyError("Benchmark query is not read-only")
        if any(token in normalized for token in FORBIDDEN_SQL):
            raise BenchmarkSafetyError("Benchmark query contains a forbidden SQL operation")


def connect_readonly():
    """Connect with server-enforced read-only defaults; never reveal a DSN."""
    database_url = os.environ.get("READONLY_DATABASE_URL")
    if not database_url:
        raise BenchmarkSafetyError("READONLY_DATABASE_URL is required")
    try:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(
            database_url,
            autocommit=True,
            options=CONNECTION_OPTIONS,
            sslmode="require",
            row_factory=dict_row,
        )
    except BenchmarkSafetyError:
        raise
    except Exception:
        raise BenchmarkSafetyError("Unable to connect to the configured read-only database") from None


def _one_row(cursor) -> dict[str, Any]:
    row = cursor.fetchone()
    return dict(row) if row is not None else {}


def verify_readonly_connection(connection) -> None:
    """Fail closed before data reads unless role and transaction mode are exact."""
    with connection.cursor() as cursor:
        cursor.execute(CURRENT_USER_SQL)
        user = _one_row(cursor).get("current_user")
        cursor.execute(READ_ONLY_SQL)
        row = cursor.fetchone()
        setting = row.get("transaction_read_only") if isinstance(row, dict) else row[0] if row else None
    if user != READER_ROLE:
        raise BenchmarkSafetyError("Configured database role is not the benchmark reader")
    if str(setting).lower() not in {"on", "true"}:
        raise BenchmarkSafetyError("Database session is not read-only")


def fetch_production_rows(connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use fixed, explicit SELECT lists; raw rows stay in process memory only."""
    _assert_fixed_read_only_sql()
    with connection.cursor() as cursor:
        cursor.execute(SNAPSHOTS_SQL)
        snapshots = [dict(row) for row in cursor.fetchall()]
        cursor.execute(RESULTS_SQL)
        results = [dict(row) for row in cursor.fetchall()]
    return snapshots, results


def _date(value: Any) -> str | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return None


def _valid_line(row: dict[str, Any]) -> bool:
    return row.get("market") == "Moneyline" or line_value(row.get("best_line")) is not None


def attach_captured_clv(entries: list[dict[str, Any]], snapshots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach post-entry captured-close evidence only for secondary reporting.

    Closing observations are never fed to the models.  They are the app's last
    captured executable pregame quote, not an official sportsbook close.
    """
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in snapshots:
        if executable_pregame(row):
            by_signal[row["signal_key"]].append(row)
    output = []
    for entry in entries:
        item = dict(entry)
        # Landmark research identity is deliberately distinct from raw canonical
        # selection identity used to locate the captured canonical close.
        candidates = by_signal.get(entry.get("raw_canonical_signal_key") or entry.get("signal_key"), [])
        close = max(candidates, key=lambda row: row["observed_at_utc"]) if candidates else None
        if close and close["observed_at_utc"] > entry["decision_timestamp"]:
            item["clv"] = calculate_clv(
                item["market"], item.get("selection_side") or "", line_value(item.get("best_line")),
                line_value(close.get("best_line")), item.get("best_price"), close.get("best_price"),
            )
        else:
            item["clv"] = None
        output.append(item)
    return output


def audit_statistics(snapshots: list[dict[str, Any]], results: list[dict[str, Any]], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate coverage only; no raw matchup, selection, or snapshot output."""
    binary = [row for row in entries if row.get("binary_target") is not None]
    movement = [row for row in entries if row.get("movement_60m") is not None]
    eligible_line = [row for row in snapshots if _valid_line(row)]
    times = [row.get("minutes_to_start") for row in entries if row.get("minutes_to_start") is not None]
    repeated_signals = Counter(row["signal_key"] for row in snapshots)
    repeated_games = Counter(row["game_key"] for row in snapshots)
    result_games = {row["game_key"] for row in results}
    by_sport = {}
    for sport, rows in _groups(snapshots, lambda row: row.get("sport")).items():
        games = {row["game_key"] for row in rows}
        by_sport[sport] = {"snapshots": len(rows), "games": len(games), "settled_games": len(games & result_games)}
    by_market = {}
    for market, rows in _groups(snapshots, lambda row: row.get("market")).items():
        entry_rows = [row for row in entries if row.get("market") == market]
        by_market[market] = {"snapshots": len(rows), "baseline_decisions": len(entry_rows), "settled_games": len({row["game_key"] for row in entry_rows})}
    return {
        "total_snapshots": len(snapshots),
        "total_results": len(results),
        "earliest_snapshot": min((row.get("observed_at_utc") for row in snapshots), default=None),
        "latest_snapshot": max((row.get("observed_at_utc") for row in snapshots), default=None),
        "earliest_event_start": min((row.get("event_start_utc") for row in snapshots), default=None),
        "latest_event_start": max((row.get("event_start_utc") for row in snapshots), default=None),
        "sports": dict(sorted(Counter(row.get("sport") for row in snapshots).items())),
        "markets": dict(sorted(Counter(row.get("market") for row in snapshots).items())),
        "result_coverage_by_sport": dict(sorted(by_sport.items())),
        "result_coverage_by_market": dict(sorted(by_market.items())),
        "distinct_games": len({row["game_key"] for row in snapshots}),
        "distinct_signals": len({row["signal_key"] for row in snapshots}),
        "settled_games": len({row["game_key"] for row in entries}),
        "unsettled_games": len({row["game_key"] for row in snapshots} - {row["game_key"] for row in entries}),
        "baseline_decisions": len(entries),
        "wins": sum(row.get("outcome") == "win" for row in entries),
        "losses": sum(row.get("outcome") == "loss" for row in entries),
        "pushes": sum(row.get("outcome") == "push" for row in entries),
        "binary_rows": len(binary),
        "event_dates": len({_date(row.get("event_start_utc")) for row in entries if _date(row.get("event_start_utc"))}),
        "movement_rows": len(movement),
        "movement_games": len({row["game_key"] for row in movement}),
        "movement_coverage_pct": 100 * len(movement) / len(entries) if entries else 0.0,
        "valid_pct": {
            "money_pct": _percent(snapshots, lambda row: row.get("money_pct") is not None),
            "bets_pct": _percent(snapshots, lambda row: row.get("bets_pct") is not None),
            "best_price": _percent(snapshots, lambda row: row.get("best_price") is not None),
            "line_when_required": _percent(snapshots, _valid_line),
            "event_start_utc": _percent(snapshots, lambda row: _date(row.get("event_start_utc")) is not None),
            "positive_gap": _percent(snapshots, lambda row: row.get("money_minus_bets_gap") is not None and row["money_minus_bets_gap"] > 0),
        },
        "time_to_start": _time_distribution(times),
        "repeated_snapshot_counts": {"signals_with_multiple": sum(count > 1 for count in repeated_signals.values()), "games_with_multiple": sum(count > 1 for count in repeated_games.values())},
        "same_kickoff_groups": sum(count > 1 for count in Counter(row.get("event_start_utc") for row in entries).values()),
        "missing_entry_features": {field: sum(row.get(field) is None for row in entries) for field in ("best_price", "bets_pct", "money_pct", "raw_gap", "minutes_to_start", "movement_60m", "market_probability")},
    }


def _percent(rows: list[dict[str, Any]], predicate) -> float:
    return 100 * sum(bool(predicate(row)) for row in rows) / len(rows) if rows else 0.0


def _time_distribution(values: list[float]) -> dict[str, int]:
    buckets = {"<=60": 0, ">60-180": 0, ">180-720": 0, ">720": 0}
    for value in values:
        if value <= 60: buckets["<=60"] += 1
        elif value <= 180: buckets[">60-180"] += 1
        elif value <= 720: buckets[">180-720"] += 1
        else: buckets[">720"] += 1
    return buckets


def sufficiency_gate(entries: list[dict[str, Any]], movement: bool = False) -> dict[str, Any]:
    """Predeclared gate evaluated before any fit or model metric is produced."""
    # The gate measures the exact common primary population, not a looser set
    # that one challenger might later be unable to score.
    cohort = [row for row in entries if primary_model_eligible(row)]
    if movement:
        cohort = [row for row in cohort if row.get("movement_60m") is not None]
    binary = [row for row in cohort if row.get("binary_target") is not None]
    gates = MOVEMENT_GATES if movement else MAIN_GATES
    folds = chronological_game_folds(cohort, folds=gates["folds"])
    failures = []
    if len({row["game_key"] for row in cohort}) < gates["unique_settled_games"]: failures.append("unique settled games")
    if len(binary) < gates["binary_rows"]: failures.append("binary W/L rows")
    if not movement and len({_date(row.get("event_start_utc")) for row in cohort if _date(row.get("event_start_utc"))}) < gates["distinct_event_dates"]: failures.append("distinct event dates")
    if len(folds) < gates["folds"]: failures.append("valid chronological folds")
    for train, test in folds:
        if len({row["game_key"] for row in test}) < gates["test_games_per_fold"]: failures.append("test games per fold"); break
        if len({row.get("binary_target") for row in train if row.get("binary_target") is not None}) < 2: failures.append("both training outcome classes"); break
    return {"passed": not failures, "failures": failures, "folds": folds, "cohort": cohort}


def _bootstrap_deltas(predictions: dict[str, list[dict[str, Any]]], challenger: str, baseline: str, seed: int = 20260917, rounds: int = 200) -> dict[str, Any] | None:
    """Game-clustered bootstrap for matched OOS model metrics."""
    identity = lambda row: row.get("research_row_key") or row.get("signal_key")
    by_key = {(row["fold"], identity(row)): row for row in predictions.get(baseline, [])}
    pairs = [(base, row) for key, row in {(item["fold"], identity(item)): item for item in predictions.get(challenger, [])}.items() if (base := by_key.get(key))]
    games: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for pair in pairs: games[pair[0]["game_key"]].append(pair)
    if len(games) < 20: return None
    rng = random.Random(seed); keys = list(games); log_deltas = []; brier_deltas = []
    for _ in range(rounds):
        selected = [pair for key in (rng.choice(keys) for _ in keys) for pair in games[key]]
        base = binary_metrics((item[0] for item in selected), "prediction")
        model = binary_metrics((item[1] for item in selected), "prediction")
        if base["log_loss"] is not None and model["log_loss"] is not None:
            log_deltas.append(model["log_loss"] - base["log_loss"]); brier_deltas.append(model["brier"] - base["brier"])
    if not log_deltas: return None
    return {"matched_games": len(games), "log_loss_delta_ci_95": _percentile_interval(log_deltas), "brier_delta_ci_95": _percentile_interval(brier_deltas)}


def _percentile_interval(values: list[float]) -> list[float]:
    ordered = sorted(values)
    return [ordered[int(.025 * (len(ordered) - 1))], ordered[int(.975 * (len(ordered) - 1))]]


def _groups(rows: Iterable[dict[str, Any]], key):
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key(row) if key(row) is not None else "missing")].append(row)
    return grouped


def _legacy_breakdown(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Aggregate legacy positive-selection multiplicity without raw identities."""
    output = {}
    for value, group in _groups(rows, lambda row: row.get(field)).items():
        markets = _groups(group, lambda row: (row.get("game_key"), row.get("market")))
        dual = sum(len(items) >= 2 for items in markets.values())
        output[value] = {"settled_game_markets": len(markets), "one_positive_legacy_side": sum(len(items) == 1 for items in markets.values()), "two_opposite_positive_legacy_sides": dual, "dual_side_pct": 100 * dual / len(markets) if markets else 0.0}
    return output


def _reversal_breakdown(states: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Aggregate flip/reversal rates by sport or market only."""
    output = {}
    for value, group in _groups(states, lambda row: row.get(field)).items():
        series = direction_reversal_audit(group)
        count = len(series); raw = sum(item["raw_flip_count"] for item in series.values())
        output[value] = {"series_count": count, "series_with_raw_flip": sum(item["raw_flip_count"] > 0 for item in series.values()), "raw_flip_count": raw, "raw_flip_rate": sum(item["raw_flip_count"] > 0 for item in series.values()) / count if count else 0.0,
                         "material": {str(threshold): {"series_with_reversal": sum(item["material"][threshold]["count"] > 0 for item in series.values()), "reversal_count": sum(item["material"][threshold]["count"] for item in series.values()), "reversal_rate": sum(item["material"][threshold]["count"] > 0 for item in series.values()) / count if count else 0.0} for threshold in (5, 10, 15, 20)}}
    return output


def _economic_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(row.get("outcome") == "win" for row in rows)
    losses = sum(row.get("outcome") == "loss" for row in rows)
    pushes = sum(row.get("outcome") == "push" for row in rows)
    units = sum(
        american_profit(int(row["best_price"])) if row.get("outcome") == "win" else -1 if row.get("outcome") == "loss" else 0
        for row in rows if row.get("best_price") not in (None, 0)
    )
    clv = [float(row["clv"]) for row in rows if row.get("clv") is not None]
    markets = {row.get("market") for row in rows}
    return {
        "bets": len(rows), "wins": wins, "losses": losses, "pushes": pushes,
        "win_rate_excluding_pushes": wins / (wins + losses) if wins + losses else None,
        "units": units, "roi": units / len(rows) if rows else None,
        "average_predicted_edge": (sum(float(row["predicted_edge"]) for row in rows if row.get("predicted_edge") is not None) / sum(row.get("predicted_edge") is not None for row in rows) if any(row.get("predicted_edge") is not None for row in rows) else None),
        "captured_close_average_clv": sum(clv) / len(clv) if clv and len(markets) == 1 else None,
        "positive_captured_clv_rate": sum(value > 0 for value in clv) / len(clv) if clv else None,
    }


def _threshold_economic_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Threshold policy ROI uses only explicitly settled lock entries."""
    settled = [row for row in rows if row.get("settlement_status") == "settled" and row.get("outcome") in {"win", "loss", "push"}]
    invalid = [row for row in rows if row.get("settlement_status") == "invalid"]
    summary = _economic_summary(settled)
    summary.update({"locked_entries_total": len(rows), "settled_entries": len(settled), "unsettled_entries": sum(row.get("settlement_status") == "unsettled" for row in rows), "invalid_settlement_entries": len(invalid)})
    return summary


def _time_bucket(row: dict[str, Any]) -> str:
    value = row.get("minutes_to_start")
    if value is None: return "missing"
    if value <= 60: return "<=60 minutes"
    if value <= 180: return ">60-180 minutes"
    if value <= 720: return ">180-720 minutes"
    return ">720 minutes"


def _gap_bucket(row: dict[str, Any]) -> str:
    value = row.get("raw_gap")
    if value is None: return "missing"
    if value <= 0: return "<=0"
    if value <= 5: return ">0-5"
    if value <= 10: return ">5-10"
    if value <= 20: return ">10-20"
    return ">20"


def _ticket_bucket(row: dict[str, Any]) -> str:
    value = row.get("bets_pct")
    if value is None: return "missing"
    if value < 25: return "<25%"
    if value < 50: return "25-50%"
    if value < 75: return "50-75%"
    return "75%+"


def _calendar_period(row: dict[str, Any]) -> str:
    date = _date(row.get("event_start_utc"))
    return date[:7] if date else "missing"


def diagnostic_breakdowns(rows: list[dict[str, Any]], minimum_rows: int = MIN_DIAGNOSTIC_ROWS) -> dict[str, Any]:
    """Aggregate only adequately sized OOS buckets; suppress smaller buckets."""
    dimensions = {
        "sport": lambda row: row.get("sport"),
        "market": lambda row: row.get("market"),
        "sport_x_market": lambda row: f"{row.get('sport')} × {row.get('market')}",
        "time_to_start": _time_bucket,
        "raw_gap": _gap_bucket,
        "ticket_share": _ticket_bucket,
        "calendar_period": _calendar_period,
    }
    output = {}
    for name, key in dimensions.items():
        visible, suppressed = {}, {}
        for bucket, values in _groups(rows, key).items():
            if len(values) >= minimum_rows:
                visible[bucket] = _economic_summary(values)
            else:
                suppressed[bucket] = len(values)
        output[name] = {"minimum_rows": minimum_rows, "buckets": visible, "suppressed_bucket_rows": suppressed}
    return output


def _matched_predictions(predictions: dict[str, list[dict[str, Any]]], challenger: str, baseline: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    identity = lambda row: row.get("research_row_key") or row.get("signal_key")
    base_by_key = {(row["fold"], identity(row)): row for row in predictions.get(baseline, [])}
    pairs = [(base_by_key[key], row) for key, row in {(item["fold"], identity(item)): item for item in predictions.get(challenger, [])}.items() if key in base_by_key]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def matched_comparison(predictions: dict[str, list[dict[str, Any]]], challenger: str, baseline: str) -> dict[str, Any]:
    base, model = _matched_predictions(predictions, challenger, baseline)
    base_metrics, model_metrics = binary_metrics(base, "prediction"), binary_metrics(model, "prediction")
    return {
        "matched_rows": len(model), "matched_games": len({row["game_key"] for row in model}),
        "baseline": baseline, "challenger": challenger,
        "delta_log_loss": None if model_metrics["log_loss"] is None or base_metrics["log_loss"] is None else model_metrics["log_loss"] - base_metrics["log_loss"],
        "delta_brier": None if model_metrics["brier"] is None or base_metrics["brier"] is None else model_metrics["brier"] - base_metrics["brier"],
    }


def roi_bootstrap_by_bucket(rows: list[dict[str, Any]], seed: int = 20260917, rounds: int = 200) -> dict[str, Any]:
    """Bootstrap ROI by game for fixed predeclared edge buckets only."""
    output = {}
    for bucket, values in _groups(rows, lambda row: _edge_bucket_name(row.get("predicted_edge"))).items():
        games = _groups(values, lambda row: row["game_key"])
        if len(games) < MIN_ROI_BOOTSTRAP_GAMES:
            output[bucket] = {"available": False, "distinct_games": len(games)}
            continue
        rng = random.Random(seed); keys = list(games); rois = []
        for _ in range(rounds):
            sampled = [row for key in (rng.choice(keys) for _ in keys) for row in games[key]]
            rois.append(_economic_summary(sampled)["roi"])
        output[bucket] = {"available": True, "distinct_games": len(games), "roi_ci_95": _percentile_interval(rois)}
    return output


def _edge_bucket_name(edge: Any) -> str:
    if edge is None or edge <= 0: return "<=0%"
    if edge <= .02: return ">0% to 2%"
    if edge <= .05: return ">2% to 5%"
    return ">5%"


def benchmark_if_sufficient(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Run only predeclared-sufficient models; keep movement comparison matched."""
    main = sufficiency_gate(entries)
    movement = sufficiency_gate(entries, movement=True)
    if not main["passed"]:
        return {"main_gate": main, "movement_gate": movement, "models": {}, "fold_metrics": [], "calibration": {}, "edge_buckets": {}, "uncertainty": {}, "roi_uncertainty": {}, "comparisons": {}, "diagnostics": {}}
    result = run_walk_forward_benchmark(main["cohort"], folds=MAIN_GATES["folds"], include_movement=False)
    models = {name: values for name, values in result["metrics"].items() if name != "Model 4" and name != "Model 3 movement cohort"}
    if movement["passed"]:
        movement_result = run_walk_forward_benchmark(movement["cohort"], folds=MOVEMENT_GATES["folds"], include_movement=True)
        for name in ("Model 3 movement cohort", "Model 4"):
            result["predictions"][name] = movement_result["predictions"].get(name, [])
            result["metrics"][name] = movement_result["metrics"].get(name, {})
        result["fold_metrics"].extend(row for row in movement_result["fold_metrics"] if row["model"] in {"Model 3 movement cohort", "Model 4"})
        models["Model 3 movement cohort"] = result["metrics"].get("Model 3 movement cohort", {})
        models["Model 4"] = result["metrics"].get("Model 4", {})
    uncertainty = {name: _bootstrap_deltas(result["predictions"], name, "Model 0B") for name in ("Model 1", "Model 2", "Model 3")}
    if movement["passed"]:
        uncertainty["Model 4 vs Model 3 movement cohort"] = _bootstrap_deltas(result["predictions"], "Model 4", "Model 3 movement cohort")
    comparisons = {
        "Model 1 vs Model 0B": matched_comparison(result["predictions"], "Model 1", "Model 0B"),
        "Model 2 vs Model 0B": matched_comparison(result["predictions"], "Model 2", "Model 0B"),
        "Model 2 vs Model 1": matched_comparison(result["predictions"], "Model 2", "Model 1"),
        "Model 3 vs Model 2": matched_comparison(result["predictions"], "Model 3", "Model 2"),
        "Model 0A vs Model 0B": matched_comparison(result["predictions"], "Model 0A", "Model 0B"),
    }
    if movement["passed"]:
        comparisons["Model 4 vs Model 3 movement cohort"] = matched_comparison(result["predictions"], "Model 4", "Model 3 movement cohort")
    oos_primary = result["predictions"].get("Model 3", [])
    visible_models = set(models)
    roi_uncertainty = {name: roi_bootstrap_by_bucket(values) for name, values in result["predictions"].items() if name in visible_models}
    return {"main_gate": main, "movement_gate": movement, "models": models, "fold_metrics": [row for row in result["fold_metrics"] if row["model"] in visible_models], "calibration": {name: values for name, values in result["calibration"].items() if name in visible_models}, "edge_buckets": {name: values for name, values in result["edge_buckets"].items() if name in visible_models}, "uncertainty": uncertainty, "roi_uncertainty": roi_uncertainty, "comparisons": comparisons, "diagnostics": diagnostic_breakdowns(oos_primary)}


def render_report(audit: dict[str, Any], benchmark: dict[str, Any]) -> str:
    """Render aggregates only; do not include source rows, matchups, or a DSN."""
    safe_audit = _redact_sensitive({key: value for key, value in audit.items() if key in REPORT_AUDIT_KEYS})
    landmarks = benchmark.get("by_landmark")
    if landmarks:
        # A production report must never create a pooled headline from the
        # three predeclared decision times.
        sufficiency = {name: {key: value.get(key, {}) for key in ("main_gate", "movement_gate")} for name, value in landmarks.items()}
        models = {name: value.get("models", {}) for name, value in landmarks.items()}
        comparisons = {name: value.get("comparisons", {}) for name, value in landmarks.items()}
        fold_metrics = {name: value.get("fold_metrics", []) for name, value in landmarks.items()}
        calibration = {name: value.get("calibration", {}) for name, value in landmarks.items()}
        edges = {name: value.get("edge_buckets", {}) for name, value in landmarks.items()}
        roi = {name: value.get("roi_uncertainty", {}) for name, value in landmarks.items()}
        uncertainty = {name: value.get("uncertainty", {}) for name, value in landmarks.items()}
        diagnostics = {name: value.get("diagnostics", {}) for name, value in landmarks.items()}
        suffix = " by landmark"
    else:
        sufficiency = {key: {"passed": value["passed"], "failures": value["failures"]} for key, value in (("main", benchmark["main_gate"]), ("movement", benchmark["movement_gate"]))}
        models, comparisons = benchmark.get("models", {}), benchmark.get("comparisons", {})
        fold_metrics, calibration, edges = benchmark.get("fold_metrics", []), benchmark.get("calibration", {}), benchmark.get("edge_buckets", {})
        roi, uncertainty, diagnostics, suffix = benchmark.get("roi_uncertainty", {}), benchmark.get("uncertainty", {}), benchmark.get("diagnostics", {}), ""
    return "\n".join((
        "# Read-only production-history benchmark",
        "", "## Safety", "- Reader role and read-only session verified before SELECT queries.",
        "- This report contains aggregate research results only; no credentials or raw rows.",
        "- Market baseline is **one-sided implied market probability**, not no-vig.",
        "- Defensible same-book paired-price provenance: **unavailable** in the current stored schema.",
        "- Captured-close CLV, where available, is not an official sportsbook closing line.",
        "", "## Population limitation", "The stored population is the application's provider-card collection path. Primary research uses paired market states, fixed canonical targets, and fixed backward-only landmarks; legacy positive-selection baselines are audit-only. It does not represent a complete sportsbook board.",
        "", "## Audit", "```json", json.dumps(safe_audit, indent=2, sort_keys=True), "```",
        "", "## Market-state pair audit", "```json", json.dumps(_redact_sensitive(audit.get("market_state_pair_audit", {})), indent=2, sort_keys=True), "```",
        "", "## Legacy per-selection baseline audit", "Legacy rows are audit-only and are excluded from primary market-state fitting.", "```json", json.dumps(_redact_sensitive(audit.get("legacy_per_selection_baseline_audit", {})), indent=2, sort_keys=True), "```",
        "", "## Direction-flip / material-reversal audit", "Direction flips use the prior non-zero sign. Material reversals require the opposite 5/10/15/20pp threshold regime.", "```json", json.dumps(_redact_sensitive(audit.get("reversal_audit", {})), indent=2, sort_keys=True), "```",
        "", "## Landmark coverage", "T-6h, T-3h, and T-1h are separate backward-only cohorts; no post-target state is used.", "```json", json.dumps(_redact_sensitive(audit.get("landmark_coverage", {})), indent=2, sort_keys=True), "```",
        "", f"## Sufficiency{suffix}", "```json", json.dumps(sufficiency, indent=2, sort_keys=True), "```",
        "", f"## Out-of-sample benchmark{suffix}", "```json", json.dumps(models, indent=2, sort_keys=True), "```",
        "", f"## Matched model comparisons{suffix}", "```json", json.dumps(comparisons, indent=2, sort_keys=True), "```",
        "", f"## Fold-by-fold OOS metrics{suffix}", "```json", json.dumps(fold_metrics, indent=2, sort_keys=True), "```",
        "", f"## Calibration buckets{suffix}", "```json", json.dumps(calibration, indent=2, sort_keys=True), "```",
        "", f"## Fixed predicted-edge bucket economics{suffix}", "Includes bets, wins, losses, pushes, units, ROI, win rate excluding pushes, predicted edge, and captured-close CLV where compatible.", "```json", json.dumps(edges, indent=2, sort_keys=True), "```",
        "", f"## Game-clustered ROI uncertainty{suffix}", "```json", json.dumps(roi, indent=2, sort_keys=True), "```",
        "", f"## Uncertainty{suffix}", "```json", json.dumps(uncertainty, indent=2, sort_keys=True), "```",
        "", f"## Diagnostic breakdowns{suffix}", f"Only buckets with at least {MIN_DIAGNOSTIC_ROWS} OOS rows are displayed; smaller buckets are suppressed.", "```json", json.dumps(diagnostics, indent=2, sort_keys=True), "```",
        "", "## Threshold-strategy observation coverage", "First observed qualifying state is not a claim about an unobserved first crossing.", "```json", json.dumps(_redact_sensitive(audit.get("threshold_coverage", {})), indent=2, sort_keys=True), "```",
        "", "## Threshold lock-policy economics", "Only the first qualifying executable favored-side entry is locked; later reversals do not erase or replace it.", "```json", json.dumps(_redact_sensitive(audit.get("threshold_lock_economics", {})), indent=2, sort_keys=True), "```",
        "", "## Threshold reversal diagnostics", "```json", json.dumps(_redact_sensitive(audit.get("threshold_reversal_diagnostics", {})), indent=2, sort_keys=True), "```",
        "", "No production ranking change is proposed by this research report.", "",
    ))


def _redact_sensitive(value: Any) -> Any:
    """Defense in depth: aggregate reports must never carry connection data."""
    if isinstance(value, dict):
        return {key: _redact_sensitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, str) and ("://" in value or "password=" in value.lower()):
        return "[redacted]"
    return value


def run() -> dict[str, Any]:
    """Connect, verify, audit, and conditionally benchmark without a DB write."""
    connection = connect_readonly()
    try:
        verify_readonly_connection(connection)
        snapshots, results = fetch_production_rows(connection)
    finally:
        connection.close()
    states, pair_audit = build_market_states(snapshots)
    entries, landmark_coverage = landmark_decision_rows(states, results)
    entries = attach_captured_clv(entries, snapshots)
    audit = audit_statistics(snapshots, results, entries)
    legacy = decision_rows(snapshots, results)
    legacy_groups = _groups(legacy, lambda row: (row.get("game_key"), row.get("market")))
    audit["market_state_pair_audit"] = pair_audit
    audit["landmark_coverage"] = landmark_coverage_audit(snapshots, states)
    audit["legacy_per_selection_baseline_audit"] = {
        "settled_game_markets": len(legacy_groups),
        "one_legacy_positive_selection": sum(len(rows) == 1 for rows in legacy_groups.values()),
        "two_opposite_legacy_positive_selections": sum(len(rows) >= 2 for rows in legacy_groups.values()),
        "dual_side_pct": 100 * sum(len(rows) >= 2 for rows in legacy_groups.values()) / len(legacy_groups) if legacy_groups else 0.0,
        "by_sport": _legacy_breakdown(legacy, "sport"), "by_market": _legacy_breakdown(legacy, "market"),
    }
    reversal = direction_reversal_audit(states)
    audit["reversal_audit"] = {
        "series_count": len(reversal), "series_with_raw_flip": sum(value["raw_flip_count"] > 0 for value in reversal.values()), "raw_flip_count": sum(value["raw_flip_count"] for value in reversal.values()),
        "raw_flip_rate": sum(value["raw_flip_count"] > 0 for value in reversal.values()) / len(reversal) if reversal else 0.0,
        "material_reversals": {str(threshold): {"series_with_reversal": sum(value["material"][threshold]["count"] > 0 for value in reversal.values()), "reversal_count": sum(value["material"][threshold]["count"] for value in reversal.values()), "reversal_rate": sum(value["material"][threshold]["count"] > 0 for value in reversal.values()) / len(reversal) if reversal else 0.0} for threshold in (5, 10, 15, 20)},
        "by_sport": _reversal_breakdown(states, "sport"), "by_market": _reversal_breakdown(states, "market"),
    }
    locked, threshold_audit = threshold_lock_entries(states, results)
    audit["threshold_coverage"] = threshold_audit
    audit["threshold_lock_economics"] = {str(threshold): _threshold_economic_summary([row for row in locked if row.get("threshold") == threshold]) for threshold in (5, 10, 15, 20)}
    audit["threshold_reversal_diagnostics"] = {str(threshold): {status: sum(row.get("threshold") == threshold and row.get("reversal_followup_status") == status for row in locked) for status in ("reversal_observed", "no_reversal_complete_followup", "reversal_followup_censored")} for threshold in (5, 10, 15, 20)}
    per_landmark = {}
    for horizon in (360, 180, 60):
        result = benchmark_if_sufficient([row for row in entries if row.get("landmark_horizon_minutes") == horizon])
        per_landmark[f"T-{horizon}m"] = {key: value for key, value in result.items() if key not in {"main_gate", "movement_gate"}}
        per_landmark[f"T-{horizon}m"]["main_gate"] = {key: result["main_gate"][key] for key in ("passed", "failures")}
        per_landmark[f"T-{horizon}m"]["movement_gate"] = {key: result["movement_gate"][key] for key in ("passed", "failures")}
    # Never fit or report a pooled landmark model.  The top-level shape is
    # horizon-keyed so a future caller cannot mistake it for one cohort.
    benchmark = {"by_landmark": per_landmark}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_report(audit, benchmark), encoding="utf-8")
    return {"audit": audit, "benchmark": benchmark}


def main() -> None:
    run()
    print("Read-only benchmark completed; aggregate report written.")


if __name__ == "__main__":
    main()
