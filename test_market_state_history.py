from analysis.research_readiness import MAIN_GATES, MOVEMENT_GATES, sufficiency_gate
from analysis import readonly_history_benchmark as benchmark
from market_state_history import SNAPSHOTS_SQL, RESULTS_SQL, summarize_market_state_history
import streamlit_app as app
from contextlib import nullcontext


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


class FakeResearchStreamlit:
    def __init__(self, view="Market-state research", sport="All sports", horizon="T-6h"):
        self.session_state = {"history_loaded": True, "history_view": view, "history_research_sport": sport, "recent_landmark_horizon": horizon}; self.text = []; self.metrics = []; self.buttons = []
    def divider(self): pass
    def button(self, label, **kwargs): self.buttons.append(label); return False
    def expander(self, *args, **kwargs): return nullcontext()
    def container(self, **kwargs): return nullcontext()
    def selectbox(self, label, options, key=None, **kwargs): return self.session_state.get(key, options[0])
    def caption(self, value, *args, **kwargs): self.text.append(str(value))
    def markdown(self, value, *args, **kwargs): self.text.append(str(value))
    def metric(self, label, value, **kwargs): self.metrics.append((label, value))
    def subheader(self, value): self.text.append(value)
    def dataframe(self, value, **kwargs): self.frames = getattr(self, "frames", []) + [value]
    def info(self, value): self.text.append(value)
    def warning(self, value): self.text.append(value)
    def download_button(self, *args, **kwargs): pass


def research_data(main_failures=(), movement_failures=()):
    rows = [{"event_start_utc": "2026-01-02T20:00:00Z", "sport": "NFL", "matchup": "A vs B", "market": "Spread", "selection": "A", "best_line": "+3", "best_price": -110, "bets_pct": 40, "money_pct": 60, "signed_gap": 20, "movement_60m": 2, "outcome": "win"}]
    gate = lambda failures: {"passed": not failures, "failures": list(failures)}
    return {"snapshots": [{"sport": "NFL"}], "summary": {"stored_observations": 1, "unique_games": 1, "valid_states": 1, "recorded_results": 1, "non_ok_retained": 0}, "coverage": {"T-360m": {}, "T-180m": {}, "T-60m": {}}, "by_horizon": {"T-6h": rows, "T-3h": [], "T-1h": []}, "readiness": {name: {"main": gate(main_failures), "movement": gate(movement_failures)} for name in ("T-6h", "T-3h", "T-1h")}}


def test_market_state_ui_is_default_separate_and_never_uses_legacy_analytics(monkeypatch):
    fake = FakeResearchStreamlit(); calls = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(app, "st", fake); monkeypatch.setattr(app, "cached_market_state_history", lambda sport: research_data())
    monkeypatch.setattr(app, "HistoryStore", lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy analytics touched")))
    monkeypatch.setattr(benchmark, "run_walk_forward_benchmark", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fit")))
    app.render_history()
    assert "Market-state research" in fake.session_state["history_view"] and "ROI" not in {label for label, _ in fake.metrics}
    assert any("MAIN" in item for item in fake.text)


def test_readiness_failures_stay_separate_and_recent_horizon_is_scoped(monkeypatch):
    fake = FakeResearchStreamlit(horizon="T-3h")
    data = research_data(("unique settled games", "distinct event dates"), ("binary W/L rows", "test games per fold")); data["by_horizon"]["T-3h"] = data["by_horizon"]["T-6h"]
    monkeypatch.setenv("DATABASE_URL", "postgresql://test"); monkeypatch.setattr(app, "st", fake); monkeypatch.setattr(app, "cached_market_state_history", lambda sport: data)
    app.render_history()
    joined = "\n".join(fake.text)
    assert "Not enough settled games; Not enough event dates" in joined
    assert "Not enough movement-qualified win/loss rows; Not enough test games per period" in joined
    recent = next(frame for frame in fake.frames if "Reference selection" in frame.columns)
    assert recent.iloc[0]["Reference selection"] == "A"


def test_research_cache_is_sport_scoped(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "read_research_history", lambda sport: (calls.append(sport) or ([], [])))
    app.cached_market_state_history.clear(); app.cached_market_state_history("NFL"); app.cached_market_state_history("NFL"); app.cached_market_state_history("MLB")
    assert calls == ["NFL", "MLB"]
