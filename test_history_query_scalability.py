"""Regression coverage for bounded persistent History analytics queries."""

import streamlit_app as app
from history_store import HistoryStore


def snapshot(index: int, observed_at_utc: str, **changes):
    row = {
        "observed_at_utc": observed_at_utc,
        "sport": "NFL",
        "matchup": f"Away {index} vs Home {index}",
        "event_start_utc": "2026-09-14T20:00:00Z",
        "market": "Spread",
        "selection": f"Away {index}",
        "selection_side": "away",
        "split_line": "+3 / -3",
        "bets_pct": 40,
        "money_pct": 55,
        "money_minus_bets_gap": 15,
        "best_line": "+3",
        "best_price": -110,
        "break_even_pct": 52.38,
        "data_quality": "OK",
        "line_vs_split": "Same (+0)",
    }
    row.update(changes)
    return row


def analytics_selects(signal_count: int) -> tuple[int, list[dict], list[str]]:
    store = HistoryStore("sqlite:///:memory:")
    for index in range(signal_count):
        entry = snapshot(index, "2026-09-13T00:00:00Z")
        close = snapshot(index, "2026-09-13T01:00:00Z", best_line="+2.5")
        store.insert_snapshots([entry, close])
        store.record_game_result("NFL", entry["matchup"], entry["event_start_utc"], 24, 21)

    statements: list[str] = []
    store.connection.set_trace_callback(statements.append)
    store.snapshots = lambda: (_ for _ in ()).throw(AssertionError("analytics must not call snapshots()"))
    rows = store.analytics_rows()
    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    return len(selects), rows, selects


def test_analytics_queries_are_bounded_and_do_not_rescan_snapshots_per_signal():
    one_count, one_rows, one_sql = analytics_selects(1)
    many_count, many_rows, many_sql = analytics_selects(25)

    assert len(one_rows) == 1
    assert len(many_rows) == 25
    assert one_count == many_count == 3
    assert all("SELECT * FROM snapshots ORDER BY observed_at_utc" not in statement for statement in [*one_sql, *many_sql])


def test_batched_analytics_preserves_entry_settlement_and_latest_valid_close():
    store = HistoryStore("sqlite:///:memory:")
    invalid_early = snapshot(1, "2026-09-13T00:00:00Z", money_minus_bets_gap=0)
    entry = snapshot(1, "2026-09-13T01:00:00Z", best_line="+3")
    valid_close = snapshot(1, "2026-09-13T02:00:00Z", best_line="+2.5")
    invalid_late = snapshot(1, "2026-09-13T03:00:00Z", best_line="broken")
    store.insert_snapshots([invalid_early, entry, valid_close, invalid_late])
    store.record_game_result("NFL", entry["matchup"], entry["event_start_utc"], 20, 23, result_source="espn", external_event_id="event-1", source_status="final")

    row = store.analytics_rows()[0]
    assert row["observed_at_utc"] == entry["observed_at_utc"]
    assert row["bet_result"] == "push"  # The +3 entry must not become the +2.5 close.
    assert row["closing_line"] == "+2.5"
    assert row["clv"] == 0.5
    assert row["result_source"] == "espn"
    assert row["external_event_id"] == "event-1"


def test_history_metadata_helpers_do_not_materialize_snapshot_exports():
    store = HistoryStore("sqlite:///:memory:")
    store.insert_snapshots([
        snapshot(1, "2026-09-13T00:00:00Z", sport="NFL"),
        snapshot(2, "2026-09-13T00:00:00Z", sport="NBA"),
    ])
    assert store.history_sports() == ["NBA", "NFL"]
    assert store.snapshot_count() == 2
    assert store.snapshot_count("NFL") == 1


def test_snapshot_csv_query_is_deferred_until_the_existing_download_is_requested():
    class ExportStore:
        def __init__(self):
            self.calls = []

        def snapshots(self, sport=None):
            self.calls.append(sport)
            return [{"sport": sport or "NFL", "selection": "Away"}]

    store = ExportStore()
    supplier = app.snapshot_export_data(store, "NFL")
    assert store.calls == []
    assert "selection" in supplier()
    assert store.calls == ["NFL"]
