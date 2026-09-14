from datetime import datetime, timedelta, timezone

import pandas as pd

import history_collector


def test_collector_persists_line_vs_split(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    data = pd.DataFrame([{"Start time": future, "Selection": "DEN", "Selection side": "away", "Matchup": "DEN vs KC", "Market": "Spread", "Split line": "+3 / -3", "Bets %": 40.0, "Money %": 60.0, "Money minus Bets gap": 20.0, "Best line": "+3.5", "Best price": -110, "Break-even %": 52.38, "Data quality": "OK", "Line vs split": "Better (+0.5)"}])
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
