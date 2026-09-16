from datetime import datetime, timezone

import pytest

import streamlit_app as app
from history_store import HistoryStore, american_profit, performance, settle_selection


def snapshot(**changes):
    row = {
        "observed_at_utc": "2026-09-13T00:00:00Z", "sport": "NFL",
        "matchup": "DEN vs KC", "event_start_utc": "2026-09-14T20:00:00Z",
        "market": "Spread", "selection": "DEN", "selection_side": "away",
        "split_line": "+3 / -3", "bets_pct": 40, "money_pct": 55,
        "money_minus_bets_gap": 15, "best_line": "+3", "best_price": -110,
        "break_even_pct": 52.38, "data_quality": "OK", "line_vs_split": "Better (+0.5)",
    }
    row.update(changes)
    return row


def test_table_is_default_but_cards_remain_selectable():
    assert app.default_results_view() == "Table"
    assert app.RESULT_VIEW_OPTIONS == ["Cards", "Table"]
    assert app.matching_signals_label(1) == "1 matching signal"
    assert app.matching_signals_label(2) == "2 matching signals"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"Market": "Total", "Best line": "o46", "Split line": "45.5", "Selection side": "over"}, [("Current total", "46"), ("Split total", "45.5")]),
        ({"Market": "Total", "Best line": "u46.5", "Split line": "46", "Selection side": "under"}, [("Current total", "46.5"), ("Split total", "46")]),
        ({"Market": "Spread", "Best line": "+4", "Split line": "+3.5 / -3.5", "Selection side": "away"}, [("Current spread", "+4"), ("Split spread", "+3.5")]),
        ({"Market": "Spread", "Best line": "-4", "Split line": "+3.5 / -3.5", "Selection side": "home"}, [("Current spread", "-4"), ("Split spread", "-3.5")]),
        ({"Market": "Spread", "Best line": "PK", "Split line": "0 / 0", "Selection side": "away"}, [("Current spread", "+0"), ("Split spread", "+0")]),
        ({"Market": "Spread", "Best line": "N/A", "Split line": "broken", "Selection side": "away"}, [("Current spread", "N/A"), ("Split spread", "N/A")]),
    ],
)
def test_market_aware_card_presentation(row, expected):
    assert app.card_market_presentation(row) == expected


def test_moneyline_card_does_not_invent_line_and_audit_formats_score_and_clv():
    assert app.card_market_presentation({"Market": "Moneyline"}) == []
    frame = app.history_audit_rows([{
        "event_start_utc": "2026-09-14T20:00:00Z", "sport": "NFL", "matchup": "DEN vs KC",
        "market": "Spread", "selection": "DEN", "best_line": "+3", "best_price": -110,
        "bets_pct": 40, "money_pct": 55, "money_minus_bets_gap": 15, "away_score": 24,
        "home_score": 21, "bet_result": "win", "closing_line": "+2.5", "closing_price": -110,
        "clv": 0.5, "result_source": "espn_scoreboard",
    }])
    assert frame.loc[0, "Final score"] == "24-21"
    assert frame.loc[0, "CLV"] == "+0.5 pts"
    assert app.format_clv(0.023, "Moneyline") == "+2.3 pp"
    assert app.format_percent(52.38, 1) == "52.4%"


def test_history_audit_rows_respect_selected_scope_before_rendering():
    snapshots = [{"sport": "NFL"}, {"sport": "NCAAF"}]
    rows = [
        {"sport": "NFL", "event_start_utc": "2026-09-14T20:00:00Z"},
        {"sport": "NCAAF", "event_start_utc": "2026-09-13T20:00:00Z"},
    ]
    _, scoped = app.filter_history_by_sport(snapshots, rows, "NFL")
    assert list(app.history_audit_rows(scoped)["Sport"]) == ["NFL"]


@pytest.mark.parametrize("market,side", [("Moneyline", "invalid"), ("Spread", "invalid"), ("Total", "invalid")])
def test_invalid_settlement_side_fails_closed(market, side):
    with pytest.raises(ValueError):
        settle_selection(market, side, 3 if market != "Moneyline" else None, 24, 21)


def test_moneyline_away_home_and_tie_settlement():
    assert settle_selection("Moneyline", "away", None, 24, 21) == "win"
    assert settle_selection("Moneyline", "away", None, 21, 24) == "loss"
    assert settle_selection("Moneyline", "home", None, 21, 24) == "win"
    assert settle_selection("Moneyline", "home", None, 24, 21) == "loss"
    assert settle_selection("Moneyline", "away", None, 21, 21) == "push"


@pytest.mark.parametrize(
    ("side", "line", "away", "home", "result"),
    [
        ("away", -3.5, 24, 20, "win"), ("away", -3.5, 23, 20, "loss"),
        ("away", 3.5, 20, 23, "win"), ("away", 3.5, 20, 24, "loss"),
        ("home", -3.5, 20, 24, "win"), ("home", -3.5, 20, 23, "loss"),
        ("home", 3.5, 23, 20, "win"), ("home", 3.5, 24, 20, "loss"),
        ("away", 3.0, 20, 23, "push"), ("away", 3.5, 20, 23, "win"),
    ],
)
def test_spread_settlement_uses_selected_side_entry_line(side, line, away, home, result):
    assert settle_selection("Spread", side, line, away, home) == result


@pytest.mark.parametrize(
    ("side", "line", "away", "home", "result"),
    [
        ("over", 45, 24, 22, "win"), ("over", 47, 24, 22, "loss"),
        ("under", 47, 24, 22, "win"), ("under", 45, 24, 22, "loss"),
        ("over", 46, 24, 22, "push"), ("under", 46, 24, 22, "push"),
        ("over", 45.5, 24, 22, "win"), ("under", 46.5, 24, 22, "win"),
    ],
)
def test_total_settlement_entry_total_and_half_points(side, line, away, home, result):
    assert settle_selection("Total", side, line, away, home) == result


def test_units_roi_pushes_and_invalid_results_are_explicit():
    rows = [
        {"best_price": 150, "bet_result": "win", "break_even_pct": 40, "market": "Moneyline", "clv": 0.02},
        {"best_price": -110, "bet_result": "loss", "break_even_pct": 52.38, "market": "Moneyline", "clv": 0},
        {"best_price": -200, "bet_result": "push", "break_even_pct": 66.67, "market": "Moneyline", "clv": None},
        {"best_price": -110, "bet_result": "unknown", "break_even_pct": 52.38, "market": "Moneyline", "clv": None},
    ]
    metrics = performance(rows)
    assert american_profit(150) == 1.5 and american_profit(-200) == 0.5
    assert metrics["settled"] == 3 and metrics["invalid_results"] == 1
    assert metrics["units"] == pytest.approx(0.5) and metrics["roi"] == pytest.approx(0.5 / 3)
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert metrics["average_break_even_pct"] == pytest.approx((40 + 52.38 + 66.67) / 3)
    assert "average_american_odds" not in metrics


def test_roi_examples_keep_pushes_in_risked_count_and_out_of_win_rate():
    first = performance([
        {"best_price": 150, "bet_result": "win", "market": "Moneyline"},
        {"best_price": -110, "bet_result": "loss", "market": "Moneyline"},
    ])
    second = performance([
        {"best_price": -200, "bet_result": "win", "market": "Moneyline"},
        {"best_price": -110, "bet_result": "push", "market": "Moneyline"},
    ])
    third = performance([
        {"best_price": -110, "bet_result": "win", "market": "Spread"},
        {"best_price": -110, "bet_result": "loss", "market": "Spread"},
        {"best_price": -110, "bet_result": "push", "market": "Spread"},
    ])
    assert first["units"] == pytest.approx(0.5) and first["roi"] == pytest.approx(0.25)
    assert second["units"] == pytest.approx(0.5) and second["roi"] == pytest.approx(0.25)
    assert third["units"] == pytest.approx(100 / 110 - 1) and third["roi"] == pytest.approx((100 / 110 - 1) / 3)
    assert second["win_rate"] == 1


def test_mixed_market_clv_is_not_averaged_but_positive_rate_remains():
    mixed = performance([
        {"best_price": -110, "bet_result": "win", "market": "Moneyline", "clv": 0.02},
        {"best_price": -110, "bet_result": "loss", "market": "Spread", "clv": 0.5},
    ])
    assert mixed["average_clv"] is None and mixed["positive_clv_rate"] == 1
    spread = performance([{"best_price": -110, "bet_result": "win", "market": "Spread", "clv": 0.5}])
    assert spread["average_clv"] == 0.5 and spread["clv_market"] == "Spread"
    frame = app.history_breakdown_frame({"mixed": mixed, "spread": spread})
    assert frame.loc["mixed", "Average CLV"] == "N/A"
    assert frame.loc["spread", "Average CLV"] == "+0.5 pts"


def test_analytics_rows_include_result_and_closing_evidence():
    store = HistoryStore("sqlite:///:memory:")
    entry = snapshot(best_line="+3")
    close = snapshot(observed_at_utc="2026-09-13T01:00:00Z", best_line="+2.5")
    store.insert_snapshots([entry, close])
    store.record_game_result("NFL", "DEN vs KC", entry["event_start_utc"], 24, 21, result_source="espn_scoreboard", external_event_id="123", source_status="Final")
    row = store.analytics_rows()[0]
    assert row["bet_result"] == "win" and row["clv"] == 0.5
    assert {"away_score", "home_score", "result_source", "external_event_id", "source_status", "settled_at_utc", "result_fetched_at_utc", "closing_line", "closing_price", "closing_observed_at_utc"} <= set(row)
    assert row["away_score"] == 24 and row["closing_line"] == "+2.5"


def test_closing_quote_does_not_change_baseline_settlement():
    store = HistoryStore("sqlite:///:memory:")
    entry = snapshot(best_line="+3")
    close = snapshot(observed_at_utc="2026-09-13T01:00:00Z", best_line="+2.5")
    store.insert_snapshots([entry, close])
    store.record_game_result("NFL", "DEN vs KC", entry["event_start_utc"], 20, 23)
    row = store.analytics_rows()[0]
    assert row["bet_result"] == "push"  # +3 entry; +2.5 close would lose.
    assert row["clv"] == 0.5


def test_baseline_is_earliest_simultaneously_qualifying_snapshot():
    store = HistoryStore("sqlite:///:memory:")
    negative = snapshot(money_minus_bets_gap=-1)
    bad_quality = snapshot(observed_at_utc="2026-09-13T00:30:00Z", data_quality="missing matchup")
    valid = snapshot(observed_at_utc="2026-09-13T01:00:00Z")
    store.insert_snapshots([negative, bad_quality, valid])
    assert store.baseline_entries()[0]["observed_at_utc"] == valid["observed_at_utc"]
