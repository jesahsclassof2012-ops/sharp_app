"""Phase 2 storage, settlement, CLV, and analytics tests."""

from history_store import (
    HistoryStore, american_profit, bucket_performance, calculate_clv, event_key,
    gap_bucket, performance, settle_selection,
)


def snapshot(**changes):
    base = {"observed_at_utc": "2026-09-13T00:00:00+00:00", "sport": "NFL", "matchup": "DEN vs KC", "event_start_utc": "2026-09-14T20:00:00+00:00", "market": "Spread", "selection": "DEN", "selection_side": "away", "split_line": "-3 / +3", "bets_pct": 40.0, "money_pct": 55.0, "money_minus_bets_gap": 15.0, "best_line": "-2.5", "best_price": -110, "break_even_pct": 52.38, "data_quality": "OK"}
    base.update(changes)
    return base


def test_sqlite_storage_insert_and_duplicate_prevention():
    store = HistoryStore("sqlite:///:memory:")
    item = snapshot()
    assert store.insert_snapshot(item)
    assert not store.insert_snapshot(item)
    assert len(store.snapshots()) == 1
    assert not store.is_postgres


def test_deterministic_market_identity_distinguishes_selection_and_market():
    base = event_key("NFL", "DEN vs KC", "2026-09-14T20:00:00Z", "Spread", "DEN")
    assert base == event_key("nfl", "den vs kc", "2026-09-14T20:00:00Z", "spread", "den")
    assert base != event_key("NFL", "DEN vs KC", "2026-09-14T20:00:00Z", "Spread", "KC")
    assert base != event_key("NFL", "DEN vs KC", "2026-09-14T20:00:00Z", "Moneyline", "DEN")


def test_moneyline_spread_total_settlement_and_pushes():
    assert settle_selection("Moneyline", "away", None, 24, 21) == "win"
    assert settle_selection("Moneyline", "home", None, 24, 21) == "loss"
    assert settle_selection("Spread", "away", -3.0, 24, 21) == "push"
    assert settle_selection("Spread", "home", 3.0, 24, 21) == "push"
    assert settle_selection("Total", "over", 45.0, 24, 21) == "push"
    assert settle_selection("Total", "under", 45.0, 24, 21) == "push"
    assert settle_selection("Total", "over", 44.5, 24, 21) == "win"


def test_record_result_uses_recorded_snapshot_line():
    store = HistoryStore("sqlite:///:memory:")
    item = snapshot(best_line="-3")
    store.insert_snapshot(item)
    assert store.record_result(item, 24, 21, "away") == "push"
    assert store.settled_observations()[0]["bet_result"] == "push"


def test_american_profit_roi_and_performance_math():
    rows = [
        {"bet_result": "win", "best_price": 150, "break_even_pct": 40.0, "clv": 0.02},
        {"bet_result": "loss", "best_price": -110, "break_even_pct": 52.38, "clv": -0.01},
        {"bet_result": "push", "best_price": -110, "break_even_pct": 52.38, "clv": None},
    ]
    assert american_profit(150) == 1.5
    assert round(american_profit(-110), 4) == 0.9091
    values = performance(rows)
    assert values["wins"] == values["losses"] == values["pushes"] == 1
    assert values["units"] == 0.5
    assert round(values["roi"], 4) == round(0.5 / 3, 4)
    assert values["positive_clv_rate"] == 0.5


def test_clv_for_spread_totals_moneyline_and_missing_closing_data():
    assert calculate_clv("Spread", "away", -6, -5.5, -110, -110) == 0.5
    assert calculate_clv("Spread", "home", 6, 6.5, -110, -110) == 0.5
    assert calculate_clv("Total", "over", 47, 46.5, -110, -110) == 0.5
    assert calculate_clv("Total", "under", 47, 47.5, -110, -110) == 0.5
    assert calculate_clv("Moneyline", "away", None, None, 150, 130) > 0
    assert calculate_clv("Spread", "away", -6, None, -110, -110) is None


def test_gap_buckets_and_group_analysis():
    assert [gap_bucket(value) for value in (0, 5, 10, 15, 20, 30)] == ["0-5", "5-10", "10-15", "15-20", "20-30", "30+"]
    rows = [dict(snapshot(), bet_result="win"), dict(snapshot(money_minus_bets_gap=32), bet_result="loss")]
    result = bucket_performance(rows)
    assert set(result) == {"15-20", "30+"}


def test_database_url_sqlite_fallback():
    store = HistoryStore("sqlite:///:memory:")
    assert store.connection is not None
    assert not store.is_postgres
