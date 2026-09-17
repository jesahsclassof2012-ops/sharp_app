"""Regression coverage for explicit, session-persistent History loading."""

from contextlib import nullcontext

import streamlit_app as app


class FakeStreamlit:
    def __init__(self, load_history=False):
        self.session_state = {}
        self.load_history = load_history
        self.button_calls = []
        self.selectboxes = []
        self.warnings = []

    def divider(self):
        return None

    def button(self, label, **kwargs):
        self.button_calls.append(label)
        return self.load_history

    def expander(self, *args, **kwargs):
        return nullcontext()

    def container(self, **kwargs):
        return nullcontext()

    def selectbox(self, label, options, key=None, **kwargs):
        self.selectboxes.append(label)
        return self.session_state.get(key, options[0])

    def warning(self, message):
        self.warnings.append(message)

    def caption(self, *args, **kwargs):
        return None

    def markdown(self, *args, **kwargs):
        return None

    def metric(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def subheader(self, *args, **kwargs):
        return None

    def download_button(self, *args, **kwargs):
        return None


class GuardedHistoryStore:
    def __init__(self, *args, **kwargs):
        self.analytics_calls = 0
        self.sports_calls = 0
        self.count_calls = 0

    def analytics_rows(self):
        self.analytics_calls += 1
        return []

    def history_sports(self):
        self.sports_calls += 1
        return ["NFL"]

    def snapshot_count(self, sport=None):
        self.count_calls += 1
        return 0


def test_history_analytics_are_not_touched_before_explicit_load(monkeypatch):
    fake_st = FakeStreamlit(load_history=False)
    store = GuardedHistoryStore()
    constructions = 0

    def guarded_store(*args, **kwargs):
        nonlocal constructions
        constructions += 1
        return store

    monkeypatch.setattr(app, "st", fake_st)
    monkeypatch.setattr(app, "HistoryStore", guarded_store)

    app.render_history()

    assert fake_st.button_calls == ["Load History & Performance"]
    assert app.HISTORY_LOADED_KEY not in fake_st.session_state
    assert constructions == 0
    assert store.analytics_calls == store.sports_calls == store.count_calls == 0


def test_requested_history_loads_and_stays_loaded_across_reruns(monkeypatch):
    fake_st = FakeStreamlit(load_history=True)
    stores = []

    def create_store(*args, **kwargs):
        store = GuardedHistoryStore()
        stores.append(store)
        return store

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(app, "st", fake_st)
    monkeypatch.setattr(app, "HistoryStore", create_store)

    app.render_history()
    fake_st.load_history = False
    app.render_history()

    assert fake_st.session_state[app.HISTORY_LOADED_KEY] is True
    assert fake_st.button_calls == ["Load History & Performance"]
    assert len(stores) == 2
    assert all(store.analytics_calls == store.sports_calls == store.count_calls == 1 for store in stores)
    assert "History sport" in fake_st.selectboxes
    assert "Breakdown" in fake_st.selectboxes


def test_requested_history_failure_is_isolated(monkeypatch):
    fake_st = FakeStreamlit(load_history=True)

    class FailingHistoryStore:
        def __init__(self, *args, **kwargs):
            return None

        def analytics_rows(self):
            raise RuntimeError("history query unavailable")

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(app, "st", fake_st)
    monkeypatch.setattr(app, "HistoryStore", FailingHistoryStore)

    app.render_history()

    assert fake_st.session_state[app.HISTORY_LOADED_KEY] is True
    assert fake_st.warnings == ["History is temporarily unavailable."]
    assert "history query unavailable" not in fake_st.warnings[0]
