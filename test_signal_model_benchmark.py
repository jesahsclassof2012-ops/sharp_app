from analysis.signal_model_benchmark import (
    binary_metrics, chronological_game_folds, decision_rows, derived_split_features,
    edge_bucket, expected_value, leakage_safe_movement, no_vig_probability,
)
from history_store import game_key, signal_key


def snapshot(**changes):
    start = "2026-01-02T20:00:00Z"
    game = game_key("NFL", "A vs B", start)
    row = {
        "game_key": game, "signal_key": signal_key(game, "Spread", "A"),
        "observed_at_utc": "2026-01-02T18:00:00Z", "event_start_utc": start,
        "sport": "NFL", "matchup": "A vs B", "market": "Spread", "selection": "A",
        "selection_side": "away", "split_line": "+3 / -3", "bets_pct": 40,
        "money_pct": 60, "money_minus_bets_gap": 20, "best_line": "+3", "best_price": -110,
        "data_quality": "OK",
    }
    row.update(changes)
    return row


def test_no_vig_requires_same_source_line_and_time():
    assert no_vig_probability(-110, -110, same_source=True, same_line=True, synchronized=True) == 0.5
    assert no_vig_probability(-110, -110, same_source=False, same_line=True, synchronized=True) is None
    assert no_vig_probability(-110, -110, same_source=True, same_line=False, synchronized=True) is None
    assert no_vig_probability(-110, -110, same_source=True, same_line=True, synchronized=False) is None


def test_diagnostics_are_raw_gap_and_safe_at_percentage_boundaries():
    values = derived_split_features(snapshot(bets_pct=40, money_pct=60))
    assert values["raw_gap"] == 20 and values["log_relative_wager_ratio"] is not None
    assert derived_split_features(snapshot(bets_pct=0, money_pct=60))["relative_wager_ratio"] is None


def test_one_baseline_decision_and_push_binary_exclusion():
    first = snapshot(observed_at_utc="2026-01-02T17:00:00Z", money_minus_bets_gap=0)
    valid = snapshot(observed_at_utc="2026-01-02T18:00:00Z")
    result = {"game_key": valid["game_key"], "away_score": 18, "home_score": 21}
    rows = decision_rows([first, valid], [result])
    assert len(rows) == 1 and rows[0]["decision_timestamp"] == valid["observed_at_utc"]
    assert rows[0]["outcome"] == "push" and rows[0]["binary_target"] is None


def test_movement_never_uses_future_and_missing_is_not_zero():
    entry = snapshot(observed_at_utc="2026-01-02T18:00:00Z", money_minus_bets_gap=20)
    future = snapshot(observed_at_utc="2026-01-02T18:01:00Z", money_minus_bets_gap=99)
    prior = snapshot(observed_at_utc="2026-01-02T17:00:00Z", money_minus_bets_gap=12)
    assert leakage_safe_movement(entry, [future, prior]) == 8
    assert leakage_safe_movement(entry, [future]) is None


def test_chronological_folds_keep_games_together():
    rows = []
    for day in range(5):
        start = f"2026-01-0{day + 1}T20:00:00Z"
        game = game_key("NFL", f"A{day} vs B{day}", start)
        for selection in ("A", "B"):
            row = snapshot(game_key=game, signal_key=signal_key(game, "Moneyline", selection), event_start_utc=start, market="Moneyline", selection=selection, selection_side="away")
            rows.append(row)
    folds = chronological_game_folds(rows, folds=2)
    assert folds
    for train, test in folds:
        assert {row["game_key"] for row in train}.isdisjoint({row["game_key"] for row in test})
        assert max(row["event_start_utc"] for row in train) < min(row["event_start_utc"] for row in test)


def test_probability_and_ev_metrics_are_correct():
    assert expected_value(.5, 150) == .25
    assert expected_value(.5, -200) == -.25
    assert edge_bucket(None) == "missing" and edge_bucket(.03) == ">2% to 5%"
    metrics = binary_metrics([{"market_probability": .5, "binary_target": 1}, {"market_probability": .5, "binary_target": 0}])
    assert metrics["rows"] == 2 and metrics["brier"] == .25
