from datetime import datetime, timezone

import pandas as pd
import pytest

import streamlit_app as app
from history_store import HistoryStore, game_key, signal_key


CURRENT = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
START = datetime(2026, 9, 14, 20, tzinfo=timezone.utc)


def snapshot(**changes):
    row = {
        "observed_at_utc": "2026-09-14T11:00:00Z", "sport": "NFL",
        "matchup": "DEN vs KC", "event_start_utc": "2026-09-14T20:00:00Z",
        "market": "Spread", "selection": "DEN", "selection_side": "away",
        "split_line": "+3 / -3", "bets_pct": 40, "money_pct": 58,
        "money_minus_bets_gap": 18, "best_line": "+3", "best_price": -110,
        "break_even_pct": 52.38, "data_quality": "OK", "line_vs_split": "Same (+0)",
    }
    row.update(changes)
    return row


def live_row(**changes):
    row = {
        "Matchup": "DEN vs KC", "Start time": START, "Market": "Spread",
        "Selection": "DEN", "Selection side": "away", "Bets %": 40,
        "Money %": 71, "Money minus Bets gap": 31, "Best line": "+3",
        "Best price": -110, "Last refresh time": CURRENT,
    }
    row.update(changes)
    return row


class CountingStore:
    def __init__(self, store):
        self.store = store
        self.movement_calls = 0
        self.first_seen_calls = 0

    def movement_snapshots(self, *args):
        self.movement_calls += 1
        return self.store.movement_snapshots(*args)

    def first_seen_for_signals(self, *args):
        self.first_seen_calls += 1
        return self.store.first_seen_for_signals(*args)

    def snapshots(self):
        raise AssertionError("live movement must not load the full snapshots table")


def enriched(monkeypatch, store, rows):
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")
    counting = CountingStore(store)
    data = app.add_persistent_movement(pd.DataFrame(rows), "NFL", store_factory=lambda **_: counting)
    return data, counting


def test_snapshot_signal_time_index_is_idempotent():
    store = HistoryStore("sqlite:///:memory:")
    store.initialize()
    indexes = {row["name"] for row in store.rows(store.execute("PRAGMA index_list(snapshots)"))}
    assert "idx_snapshots_signal_observed" in indexes


def test_batched_movement_query_and_first_seen_are_exact_signal_and_bounded():
    store = HistoryStore("sqlite:///:memory:")
    exact = snapshot()
    other = snapshot(selection="KC", selection_side="home", money_minus_bets_gap=99)
    outside = snapshot(observed_at_utc="2026-09-14T10:00:00Z", money_minus_bets_gap=99)
    store.insert_snapshots([exact, other, outside])
    exact_key = signal_key(game_key("NFL", "DEN vs KC", START), "Spread", "DEN")
    rows = store.movement_snapshots([exact_key], "2026-09-14T10:40:00Z", "2026-09-14T12:00:00Z")
    assert [(row["signal_key"], row["money_minus_bets_gap"]) for row in rows] == [(exact_key, 18.0)]
    assert store.first_seen_for_signals([exact_key]) == {exact_key: "2026-09-14 10:00:00"}


def test_exact_signal_identity_is_deterministic_and_does_not_leak_history(monkeypatch):
    store = HistoryStore("sqlite:///:memory:")
    store.insert_snapshots([
        snapshot(observed_at_utc="2026-09-14T10:30:00Z"),
        snapshot(observed_at_utc="2026-09-14T11:00:00Z"),
        snapshot(selection="KC", selection_side="home", observed_at_utc="2026-09-14T09:00:00Z", money_minus_bets_gap=99),
        snapshot(selection="KC", selection_side="home", observed_at_utc="2026-09-14T11:00:00Z", money_minus_bets_gap=99),
    ])
    data, counting = enriched(monkeypatch, store, [live_row(), live_row(Selection="KC", **{"Selection side": "home"})])
    assert list(data["Gap Δ 60m"]) == [13.0, -68.0]
    assert app.format_first_seen(data.loc[0, "First seen"]) == "First seen 3:30 AM PT"
    assert counting.movement_calls == counting.first_seen_calls == 1


@pytest.mark.parametrize(
    "observed,gap,expected",
    [
        ("2026-09-14T11:00:00Z", 18, 13),
        ("2026-09-14T10:45:00Z", 18, 13),  # normal 15-minute collection drift
        ("2026-09-14T10:50:00Z", 18, 13),  # closer than 10:40 around the target
    ],
)
def test_gap_movement_selects_closest_eligible_snapshot(monkeypatch, observed, gap, expected):
    store = HistoryStore("sqlite:///:memory:")
    store.insert_snapshots([snapshot(observed_at_utc=observed, money_minus_bets_gap=gap), snapshot(observed_at_utc="2026-09-14T10:40:00Z", money_minus_bets_gap=5)])
    data, _ = enriched(monkeypatch, store, [live_row()])
    assert data.loc[0, "Gap Δ 60m"] == expected


def test_gap_movement_rejects_stale_future_current_postgame_and_missing_gaps(monkeypatch):
    signal = signal_key(game_key("NFL", "DEN vs KC", START), "Spread", "DEN")
    fake_rows = [
        {"signal_key": signal, "observed_at_utc": "2026-09-14T10:39:00Z", "event_start_utc": "2026-09-14T20:00:00Z", "money_minus_bets_gap": 10},
        {"signal_key": signal, "observed_at_utc": "2026-09-14T12:00:00Z", "event_start_utc": "2026-09-14T20:00:00Z", "money_minus_bets_gap": 10},
        {"signal_key": signal, "observed_at_utc": "2026-09-14T12:01:00Z", "event_start_utc": "2026-09-14T20:00:00Z", "money_minus_bets_gap": 10},
        {"signal_key": signal, "observed_at_utc": "2026-09-14T11:00:00Z", "event_start_utc": "2026-09-14T10:59:00Z", "money_minus_bets_gap": 10},
        {"signal_key": signal, "observed_at_utc": "2026-09-14T11:05:00Z", "event_start_utc": "2026-09-14T20:00:00Z", "money_minus_bets_gap": None},
    ]
    class FakeStore:
        def movement_snapshots(self, *args): return fake_rows
        def first_seen_for_signals(self, *args): return {signal: "2026-09-14T09:00:00Z"}
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")
    data = app.add_persistent_movement(pd.DataFrame([live_row()]), "NFL", store_factory=lambda **_: FakeStore())
    assert pd.isna(data.loc[0, "Gap Δ 60m"])
    missing_current = app.add_persistent_movement(pd.DataFrame([live_row(**{"Money minus Bets gap": None})]), "NFL", store_factory=lambda **_: FakeStore())
    assert pd.isna(missing_current.loc[0, "Gap Δ 60m"])


def test_first_seen_and_cards_format_pacific_time(monkeypatch):
    store = HistoryStore("sqlite:///:memory:")
    store.insert_snapshots([snapshot(observed_at_utc="2026-09-14T09:15:00Z"), snapshot()])
    data, _ = enriched(monkeypatch, store, [live_row()])
    assert app.format_first_seen(data.loc[0, "First seen"]) == "First seen 2:15 AM PT"
    assert app.movement_card_caption(data.loc[0].to_dict()) == "Gap +18 → +31 (+13 in ~60m)"
    assert app.movement_card_caption({"Historical gap 60m": 31, "Money minus Bets gap": 31, "Gap Δ 60m": 0}) == "Gap +31 → +31 (0 in ~60m)"
    assert app.movement_card_caption({"Historical gap 60m": None, "Money minus Bets gap": 31, "Gap Δ 60m": None}) == "60m movement: N/A"


def test_last_refresh_time_not_wall_clock_controls_comparison(monkeypatch):
    store = HistoryStore("sqlite:///:memory:")
    store.insert_snapshot(snapshot(observed_at_utc="2026-09-14T11:00:00Z"))
    data, _ = enriched(monkeypatch, store, [live_row(**{"Last refresh time": "2026-09-14T12:00:00+00:00"})])
    assert data.loc[0, "Gap Δ 60m"] == 13
    unsafe = app.add_persistent_movement(pd.DataFrame([live_row(**{"Last refresh time": None})]), "NFL", store_factory=lambda **_: store)
    assert pd.isna(unsafe.loc[0, "Gap Δ 60m"])


def test_missing_database_or_query_failure_degrades_to_na_without_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    data = app.add_persistent_movement(pd.DataFrame([live_row()]), "NFL", store_factory=lambda **_: (_ for _ in ()).throw(AssertionError("must not open SQLite")))
    assert pd.isna(data.loc[0, "Gap Δ 60m"]) and pd.isna(data.loc[0, "First seen"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")
    data = app.add_persistent_movement(pd.DataFrame([live_row()]), "NFL", store_factory=lambda **_: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    assert pd.isna(data.loc[0, "Gap Δ 60m"]) and pd.isna(data.loc[0, "First seen"])


def test_table_only_adds_one_persistent_movement_column_and_keeps_session_secondary():
    assert "Gap Δ 60m" in app.RESULT_TABLE_COLUMNS
    assert {"Money Δ 60m", "Bets Δ 60m", "Line movement", "Price movement", "First seen"}.isdisjoint(app.RESULT_TABLE_COLUMNS)
    assert "Session movement" in app.RESULT_TABLE_COLUMNS


@pytest.mark.parametrize(
    ("value", "expected"),
    [(13, "+13"), (-8, "-8"), (0, "0"), (None, "N/A")],
)
def test_movement_gap_formatting_is_signed_without_positive_zero(value, expected):
    assert app.format_movement_gap(value) == expected


def test_results_table_preserves_numeric_movement_values_while_formatting_display():
    records = []
    for movement in (13.0, -8.0, 0.0, None):
        row = {column: None for column in app.RESULT_TABLE_COLUMNS}
        row.update({"Matchup": "DEN vs KC", "Gap Δ 60m": movement})
        records.append(row)
    styled = app.results_table_for_display(pd.DataFrame(records))
    assert pd.api.types.is_numeric_dtype(styled.data["Gap Δ 60m"])
    rendered = styled.to_html()
    assert "+13" in rendered and "-8" in rendered and ">0<" in rendered and "N/A" in rendered
