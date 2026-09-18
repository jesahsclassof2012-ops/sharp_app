from analysis.research_readiness import MAIN_GATES, MOVEMENT_GATES, sufficiency_gate
from analysis import readonly_history_benchmark as benchmark
from market_state_history import SNAPSHOTS_SQL, RESULTS_SQL, summarize_market_state_history


def pair(observed="2026-01-02T17:00:00Z", sport="NFL"):
    base = {"game_key": "g", "observed_at_utc": observed, "event_start_utc": "2026-01-02T20:00:00Z", "sport": sport, "matchup": "A vs B", "market": "Spread", "data_quality": "OK"}
    return [dict(base, signal_key="a", selection="A", selection_side="away", bets_pct=40, money_pct=60, money_minus_bets_gap=20, best_line="+3", best_price=-110), dict(base, signal_key="b", selection="B", selection_side="home", bets_pct=60, money_pct=40, money_minus_bets_gap=-20, best_line="-3", best_price=-110)]


def test_summary_scopes_counts_and_keeps_landmarks_separate():
    data = summarize_market_state_history(pair(), [{"game_key": "g", "away_score": 24, "home_score": 20}])
    assert data["summary"]["stored_observations"] == 2
    assert data["summary"]["unique_games"] == data["summary"]["valid_states"] == data["summary"]["recorded_results"] == 1
    assert set(data["by_horizon"]) == {"T-6h", "T-3h", "T-1h"}
    assert "run_walk_forward_benchmark" not in open("market_state_history.py", encoding="utf-8").read()


def test_readonly_adapter_uses_only_select_lists_and_shared_gates():
    assert SNAPSHOTS_SQL.lower().startswith("select") and RESULTS_SQL.lower().startswith("select")
    assert MAIN_GATES == benchmark.MAIN_GATES and MOVEMENT_GATES == benchmark.MOVEMENT_GATES
    assert sufficiency_gate([], False) == benchmark.sufficiency_gate([], False)
