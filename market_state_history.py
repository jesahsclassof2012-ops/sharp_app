"""Read-only, UI-safe summaries of paired market-state research history."""
from __future__ import annotations
import os
from typing import Any
from analysis.research_readiness import sufficiency_gate
from analysis.signal_model_benchmark import build_market_states, landmark_coverage_audit, landmark_decision_rows

SNAPSHOTS_SQL = "SELECT game_key, signal_key, observed_at_utc, sport, matchup, event_start_utc, market, selection, selection_side, split_line, bets_pct, money_pct, money_minus_bets_gap, best_line, best_price, break_even_pct, data_quality, line_vs_split FROM public.snapshots"
RESULTS_SQL = "SELECT game_key, sport, matchup, event_start_utc, away_score, home_score, settled_at_utc FROM public.results"
CONNECTION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=60000"

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
    return {"snapshots": snapshots, "results": results, "states": states, "entries": entries, "pair_audit": pair_audit, "coverage": landmark_coverage_audit(snapshots, states), "by_horizon": by_horizon, "readiness": {name: {"main": sufficiency_gate(rows), "movement": sufficiency_gate(rows, True)} for name, rows in by_horizon.items()}, "summary": {"stored_observations": len(snapshots), "unique_games": len({row.get("game_key") for row in snapshots}), "valid_states": len(states), "recorded_results": len({row.get("game_key") for row in results}), "non_ok_retained": pair_audit["non_ok_data_quality_pairs"]}}
