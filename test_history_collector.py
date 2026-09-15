from datetime import datetime, timedelta, timezone
import math

import pandas as pd
import pytest

import history_collector
from history_store import HistoryStore


def collector_row(**changes):
    data = {"Start time": datetime.now(timezone.utc) + timedelta(hours=2), "Selection": "DEN", "Selection side": "away", "Matchup": "DEN vs KC", "Market": "Spread", "Split line": "+3 / -3", "Bets %": 40.0, "Money %": 60.0, "Money minus Bets gap": 20.0, "Best line": "+3.5", "Best price": -110, "Break-even %": 52.38, "Data quality": "OK", "Line vs split": "Better (+0.5)"}
    data.update(changes)
    return data


def test_collector_persists_line_vs_split(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    data = pd.DataFrame([collector_row(**{"Start time": future})])
    class Response:
        text = "fixture"
        def raise_for_status(self): pass
    class Store:
        captured = []
        def insert_snapshots(self, values):
            self.captured.extend(values); return len(values)
    monkeypatch.setattr(history_collector.requests,"get",lambda *args,**kwargs: Response())
    monkeypatch.setattr(history_collector,"parse_scoresandodds_html",lambda *args: data)
    store=Store(); assert history_collector.collect_sport(store,"NFL")==1
    assert store.captured[0]["line_vs_split"]=="Better (+0.5)"


def test_dataframe_iterrows_missing_price_normalizes_to_none_and_persists():
    data = pd.DataFrame([collector_row(**{"Best price": None}), collector_row(**{"Best price": -110, "Selection": "KC"})])
    row = next(data.iterrows())[1]
    assert isinstance(row["Best price"], float) and math.isnan(row["Best price"])
    snapshot = history_collector.snapshot_from_row(row, "NFL", datetime.now(timezone.utc))
    assert snapshot["best_price"] is None
    store = HistoryStore("sqlite:///:memory:")
    assert store.insert_snapshot(snapshot)
    assert store.snapshots()[0]["best_price"] is None
    assert not store.insert_snapshot(snapshot)


def test_optional_numeric_normalization_handles_pandas_nulls_valid_prices_and_nonfinite_values():
    observed_at = datetime.now(timezone.utc)
    for missing in (None, float("nan"), pd.NA, float("inf"), float("-inf")):
        row = pd.Series(collector_row(**{"Best price": missing}))
        assert history_collector.snapshot_from_row(row, "NFL", observed_at)["best_price"] is None
    assert history_collector.snapshot_from_row(pd.Series(collector_row(**{"Best price": -110})), "NFL", observed_at)["best_price"] == -110
    assert type(history_collector.snapshot_from_row(pd.Series(collector_row(**{"Best price": 150})), "NFL", observed_at)["best_price"]) is int
    with pytest.raises(ValueError, match="outside PostgreSQL INTEGER range"):
        history_collector.snapshot_from_row(pd.Series(collector_row(**{"Best price": 2 ** 31})), "NFL", observed_at)
    row = pd.Series(collector_row(**{"Bets %": float("nan"), "Money %": pd.NA, "Money minus Bets gap": float("inf"), "Break-even %": float("-inf")}))
    snapshot = history_collector.snapshot_from_row(row, "NFL", observed_at)
    assert all(snapshot[field] is None for field in ("bets_pct", "money_pct", "money_minus_bets_gap", "break_even_pct"))


def test_snapshot_insert_error_includes_safe_observation_context(monkeypatch):
    store = HistoryStore("sqlite:///:memory:")
    snapshot = history_collector.snapshot_from_row(pd.Series(collector_row()), "NFL", datetime.now(timezone.utc))
    monkeypatch.setattr(store, "insert_snapshot", lambda item: (_ for _ in ()).throw(OverflowError("integer out of range")))
    with pytest.raises(RuntimeError, match=r"sport=NFL matchup=DEN vs KC.*best_price.*int"):
        store.insert_snapshots([snapshot])
