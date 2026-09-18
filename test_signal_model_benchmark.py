from analysis.signal_model_benchmark import (
    binary_metrics, chronological_game_folds, decision_rows, derived_split_features,
    edge_bucket, expected_value, leakage_safe_movement, no_vig_probability,
    calibration_buckets, edge_bucket_performance, run_walk_forward_benchmark,
    select_training_edge_threshold, validated_no_vig_pair,
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


def quote(**changes):
    row = {"game_key": "nfl:a-vs-b:2026-01-02", "market": "Spread", "source": "Book A", "selection": "Team A", "line": "+3", "observed_at_utc": "2026-01-02T18:00:00Z", "best_price": -110}
    row.update(changes)
    return row


def test_moneyline_pairs_require_same_game_and_opposite_selections():
    side = quote(market="Moneyline", selection="Team A", line="ML")
    assert validated_no_vig_pair(side, quote(market="Moneyline", selection="Team B", line="ML")) == .5
    assert validated_no_vig_pair(side, quote(market="Moneyline", selection="Team A", line="ML")) is None
    assert validated_no_vig_pair(side, quote(market="Moneyline", selection="Team B", line="ML", game_key="nfl:c-vs-d:2026-01-02")) is None


def test_spread_pairs_require_opposite_teams_and_complementary_lines():
    side = quote(selection="Team A", line="+3")
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3")) == .5
    assert validated_no_vig_pair(side, quote(selection="Team A", line="-3")) is None
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3.5")) is None
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3", game_key="nfl:c-vs-d:2026-01-02")) is None


def test_total_pairs_require_over_under_and_identical_thresholds():
    over = quote(market="Total", selection="Over", line="o45.5")
    assert validated_no_vig_pair(over, quote(market="Total", selection="Under", line="u45.5")) == .5
    assert validated_no_vig_pair(over, quote(market="Total", selection="Over", line="o45.5")) is None
    under = quote(market="Total", selection="Under", line="u45.5")
    assert validated_no_vig_pair(under, quote(market="Total", selection="Under", line="u45.5")) is None
    assert validated_no_vig_pair(over, quote(market="Total", selection="Under", line="u46")) is None
    assert validated_no_vig_pair(over, quote(market="Total", selection="Under", line="u45.5", game_key="nfl:c-vs-d:2026-01-02")) is None


def test_pair_validation_still_rejects_book_market_and_time_mismatches():
    side = quote(selection="Team A", line="+3")
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3", source="Book B")) is None
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3", market="Moneyline")) is None
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3", observed_at_utc="2026-01-02T18:10:01Z")) is None
    assert validated_no_vig_pair(side, quote(selection="Team B", line="-3", game_key=None)) is None
    assert validated_no_vig_pair(side, quote(selection=None, line="-3")) is None


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


def test_same_kickoff_group_is_never_split_across_train_and_test():
    rows = []
    for day, teams in enumerate((("A", "B"), ("C", "D"), ("E", "F"), ("G", "H"))):
        start = "2026-01-02T20:00:00Z" if day < 2 else f"2026-01-0{day + 1}T20:00:00Z"
        game = game_key("NFL", f"{teams[0]} vs {teams[1]}", start)
        rows.append(snapshot(game_key=game, signal_key=signal_key(game, "Spread", teams[0]), matchup=f"{teams[0]} vs {teams[1]}", event_start_utc=start))
    folds = chronological_game_folds(rows, folds=3)
    assert folds
    for train, test in folds:
        train_starts, test_starts = {row["event_start_utc"] for row in train}, {row["event_start_utc"] for row in test}
        assert train_starts.isdisjoint(test_starts)
        assert max(train_starts) < min(test_starts)
        assert {row["game_key"] for row in train}.isdisjoint({row["game_key"] for row in test})


def test_probability_and_ev_metrics_are_correct():
    assert expected_value(.5, 150) == .25
    assert expected_value(.5, -200) == -.25
    assert edge_bucket(None) == "missing" and edge_bucket(.03) == ">2% to 5%"
    metrics = binary_metrics([{"market_probability": .5, "binary_target": 1}, {"market_probability": .5, "binary_target": 0}])
    assert metrics["rows"] == 2 and metrics["brier"] == .25


def synthetic_decisions():
    rows = []
    # Two games at each kickoff retain simultaneous games as an atomic fold group.
    for group in range(6):
        start = f"2026-02-{group + 1:02d}T20:00:00Z"
        for game_number in range(2):
            game = game_key("NFL", f"A{group}{game_number} vs B{group}{game_number}", start)
            for side in range(2):
                probability = .42 + .04 * ((group + game_number + side) % 4)
                outcome = "win" if (group + game_number + side) % 3 else "loss"
                rows.append({
                    "game_key": game, "signal_key": signal_key(game, "Moneyline", f"S{side}"),
                    "event_start_utc": start, "observed_at_utc": f"2026-02-{group + 1:02d}T18:00:00Z",
                    "sport": "NFL", "market": "Moneyline", "selection": f"S{side}", "best_price": -110,
                    "market_probability": probability, "bets_pct": 25 + 5 * side, "money_pct": 45 + 5 * group,
                    "raw_gap": 20 + 5 * group - 5 * side, "minutes_to_start": 120 + 10 * group,
                    "movement_60m": None if group == 0 else float(group - side), "outcome": outcome,
                    "binary_target": 1 if outcome == "win" else 0,
                })
    return rows


def test_end_to_end_benchmark_is_oos_deterministic_and_movement_matched():
    first = run_walk_forward_benchmark(synthetic_decisions(), folds=3)
    second = run_walk_forward_benchmark(synthetic_decisions(), folds=3)
    assert first["metrics"] == second["metrics"]
    assert {"Model 0A", "Model 0B", "Model 1", "Model 2", "Model 3", "Model 4", "Model 3 movement cohort"} <= set(first["metrics"])
    for model, rows in first["predictions"].items():
        for row in rows:
            train, test = first["folds"][row["fold"]]
            assert row["game_key"] not in {value["game_key"] for value in train}
            assert row["game_key"] in {value["game_key"] for value in test}
    assert {row["signal_key"] for row in first["predictions"]["Model 4"]} == {row["signal_key"] for row in first["predictions"]["Model 3 movement cohort"]}
    assert all(row["movement_60m"] is not None for row in first["predictions"]["Model 4"])
    assert first["edge_buckets"]["Model 1"]


def test_primary_models_use_one_matched_feature_complete_oos_population():
    rows = synthetic_decisions()
    rows[0]["raw_gap"] = None
    rows[1]["bets_pct"] = None
    rows[2]["money_pct"] = None
    rows[3]["minutes_to_start"] = None
    benchmark = run_walk_forward_benchmark(rows, folds=3)
    primary = ("Model 0A", "Model 0B", "Model 1", "Model 2", "Model 3")
    populations = [
        {(row["fold"], row["game_key"], row["signal_key"]) for row in benchmark["predictions"][model]}
        for model in primary
    ]
    assert populations and all(population == populations[0] for population in populations)
    assert all(rows[index]["signal_key"] not in {key[2] for key in populations[0]} for index in range(4))


def test_calibration_edge_buckets_and_training_only_threshold_are_fixed():
    calibration = calibration_buckets([{"prediction": .45, "binary_target": 1}, {"prediction": .45, "binary_target": 0}, {"prediction": .8, "binary_target": 1}])
    middle = next(row for row in calibration if row["bucket"] == "40–50%")
    assert middle == {"bucket": "40–50%", "count": 2, "average_prediction": .45, "realized_win_rate": .5}
    rows = [{"outcome": "win", "best_price": 150, "predicted_edge": .03, "clv": .1, "market": "Moneyline"}, {"outcome": "push", "best_price": -110, "predicted_edge": -.01, "clv": None, "market": "Moneyline"}]
    buckets = edge_bucket_performance(rows)
    assert next(row for row in buckets if row["bucket"] == ">2% to 5%")["units"] == 1.5
    training = [{"predicted_edge": .01, "outcome": "loss"}, {"predicted_edge": .06, "outcome": "win"}]
    assert select_training_edge_threshold(training) == 0
    # The API accepts no test rows/outcomes, preventing test-set threshold selection.
    assert "test" not in select_training_edge_threshold.__code__.co_varnames
