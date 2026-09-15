"""Pure, sport-scoped history UI behavior."""

import streamlit_app as app


SNAPSHOTS = [
    {"sport": "NFL", "id": "nfl-snapshot"},
    {"sport": "NCAAF", "id": "ncaaf-snapshot"},
    {"sport": "NBA", "id": "nba-snapshot"},
]
ROWS = [
    {"sport": "NFL", "id": "nfl-row", "bet_result": "win", "money_minus_bets_gap": 10, "best_price": -110},
    {"sport": "NCAAF", "id": "ncaaf-row", "bet_result": "loss", "money_minus_bets_gap": 12, "best_price": -110},
    {"sport": "NBA", "id": "nba-row", "bet_result": "push", "money_minus_bets_gap": 14, "best_price": -110},
]


def test_history_sports_are_dynamic_and_sorted():
    assert app.history_sports(SNAPSHOTS, ROWS) == ["NBA", "NCAAF", "NFL"]


def test_history_sport_filtering_preserves_all_sports_and_scopes_each_sport():
    all_snapshots, all_rows = app.filter_history_by_sport(SNAPSHOTS, ROWS, "All sports")
    assert all_snapshots == SNAPSHOTS
    assert all_rows == ROWS
    nfl_snapshots, nfl_rows = app.filter_history_by_sport(SNAPSHOTS, ROWS, "NFL")
    assert [row["id"] for row in nfl_snapshots] == ["nfl-snapshot"]
    assert [row["id"] for row in nfl_rows] == ["nfl-row"]
    ncaaf_snapshots, ncaaf_rows = app.filter_history_by_sport(SNAPSHOTS, ROWS, "NCAAF")
    assert [row["id"] for row in ncaaf_snapshots] == ["ncaaf-snapshot"]
    assert [row["id"] for row in ncaaf_rows] == ["ncaaf-row"]


def test_history_scope_metrics_calls_performance_with_filtered_rows(monkeypatch):
    received = []

    def fake_performance(rows):
        received.append(rows)
        return {"settled": len(rows)}

    monkeypatch.setattr(app, "performance", fake_performance)
    snapshots, rows, metrics = app.history_scope_metrics(SNAPSHOTS, ROWS, "NFL")
    assert [row["id"] for row in snapshots] == ["nfl-snapshot"]
    assert [row["id"] for row in rows] == ["nfl-row"]
    assert received == [rows]
    assert metrics == {"settled": 1}


def test_history_filenames_and_sample_warning_are_sport_scoped():
    assert app.history_export_filename("snapshots", "All sports") == "snapshots_all_sports.csv"
    assert app.history_export_filename("settled_results", "NFL") == "settled_results_nfl.csv"
    assert app.history_export_filename("snapshots", "NCAAF") == "snapshots_ncaaf.csv"
    assert app.insufficient_sample_message("All sports") == "Insufficient sample size: fewer than 30 settled observations."
    assert app.insufficient_sample_message("NFL") == "Insufficient NFL sample size: fewer than 30 settled observations."
