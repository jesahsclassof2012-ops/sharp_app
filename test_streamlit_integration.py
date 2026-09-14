"""Integration coverage for the live Sharp Signal V2 parser."""

from datetime import datetime, timezone
from pathlib import Path

import streamlit_app


def test_live_app_imports_and_uses_sharp_core():
    """The app module exposes a parser whose execution uses V2 output fields."""
    assert streamlit_app.parse_team_code.__module__ == "sharp_core"
    html = '''
    <div class="trend-card">
      <span class="trend-graph-chart">
        <span class="trend-graph-sides"><strong>UTSA</strong><strong>TXST</strong><span>% of Bets</span></span>
        <span class="trend-graph-percentage"><span>42%</span><span>58%</span></span>
        <span class="trend-graph-percentage"><span>61%</span><span>39%</span></span>
      </span>
      <span data-role="localtime" data-value="2026-09-14T18:00:00Z"></span>
      <span class="best-odds">
        <div class="best-odds-container"><span>Best away Odds</span><span class="data-moneyline">+120</span></div>
        <div class="best-odds-container"><span>Best home Odds</span><span class="data-moneyline">-140</span></div>
      </span>
    </div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert list(data["Selection"]) == ["UTSA", "TXST"]
    assert data.loc[0, "Money minus Bets gap"] == 19.0
    assert data.loc[0, "Best line"] == "N/A"
    assert data.loc[0, "Best price"] == 120
    assert data.loc[0, "Break-even %"] == 45.45
    assert "Confidence Score" not in data.columns


def test_spread_and_hyphenated_code_are_current_card_identity():
    html = '''
    <div class="trend-card"><span class="trend-graph-chart">
      <span class="trend-graph-sides"><strong>M-OH +3.5</strong><strong>SJSU -3.5</strong><span>Spread</span></span>
      <span class="trend-graph-percentage"><span>45%</span><span>55%</span></span>
      <span class="trend-graph-percentage"><span>60%</span><span>40%</span></span>
    </span><span class="best-odds">
      <div class="best-odds-container"><span>Best away Odds</span><span class="data-moneyline">+4</span><small class="data-odds best">-110</small></div>
      <div class="best-odds-container"><span>Best home Odds</span><span class="data-moneyline">-4</span><small class="data-odds best">-110</small></div>
    </span></div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert data.loc[0, "Matchup"] == "M-OH vs SJSU"
    assert data.loc[0, "Split line"] == "3.5 / -3.5"
    assert data.loc[0, "Best line"] == "+4"
    assert data.loc[0, "Best price"] == -110
    assert data.loc[0, "Line vs split"] == "Better (+0.5)"


def test_total_uses_best_over_and_under_not_away_home_prices():
    html = '''
    <div class="trend-card"><div class="event-header"><span class="team-name">UTSA</span><span class="team-name">TXST</span></div><span class="trend-graph-chart">
      <span class="trend-graph-sides"><strong>Over o45.5</strong><strong>Under u45.5</strong><span>Total</span></span>
      <span class="trend-graph-percentage"><span>49%</span><span>51%</span></span>
      <span class="trend-graph-percentage"><span>56%</span><span>44%</span></span>
    </span><span class="best-odds">
      <div class="best-odds-container"><span>Best over Odds</span><span class="data-moneyline">o46</span><small class="data-odds best">-105</small></div>
      <div class="best-odds-container"><span>Best under Odds</span><span class="data-moneyline">u46</span><small class="data-odds best">-115</small></div>
      <div class="best-odds-container"><span>Best away Odds</span><span class="data-moneyline">+3</span><small class="data-odds best">+300</small></div>
    </span></div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert list(data["Best price"]) == [-105, -115]
    assert list(data["Best line"]) == ["o46", "u46"]
    assert list(data["Split line"]) == ["45.5", "45.5"]
    assert list(data["Matchup"]) == ["UTSA vs TXST", "UTSA vs TXST"]


def test_captured_scoresandodds_cards_keep_event_identity_and_quotes():
    """Regression fixture mirrors current nested trend-card/event-header markup."""
    html = (Path(__file__).parent / "tests" / "fixtures" / "scoresandodds_trend_cards.html").read_text()
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert set(data.loc[data["Market"] == "Moneyline", "Matchup"]) == {"Southern Miss vs Auburn"}
    assert set(data.loc[data["Market"] == "Spread", "Matchup"]) == {"Southern Miss vs Auburn"}
    totals = data[data["Market"] == "Total"]
    assert set(totals["Matchup"]) == {"Southern Miss vs Auburn", "Cal Poly vs San Jose State"}
    assert "Unidentified matchup" not in set(totals["Matchup"])
    assert totals.groupby("Matchup").size().to_dict() == {
        "Southern Miss vs Auburn": 2, "Cal Poly vs San Jose State": 2,
    }
    assert data.loc[(data["Market"] == "Spread") & (data["Selection"] == "AUB"), "Best price"].item() == 100
    assert set(data.loc[data["Market"] == "Spread", "Line vs split"]) == {"Better (+1)"}
    assert set(totals["Best price"]) >= {100, -109, -115, -108}


def test_missing_event_identity_is_a_data_quality_issue():
    html = '''<div class="trend-card"><span class="trend-graph-chart">
      <span class="trend-graph-sides"><strong>Over (o45.5)</strong><span>% of Bets</span><strong>Under (u45.5)</strong></span>
      <span class="trend-graph-percentage"><span>49%</span><span>51%</span></span>
      <span class="trend-graph-percentage"><span>56%</span><span>44%</span></span>
    </span></div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert set(data["Matchup"]) == {"Unidentified matchup"}
    assert data["Data quality"].str.contains("missing matchup").all()


def test_fetch_data_cache_keeps_actual_fetch_timestamp(monkeypatch):
    calls = []

    class Response:
        text = '''<div class="trend-card"><div class="event-header"><span class="team-name">UTSA</span><span class="team-name">TXST</span></div><span class="trend-graph-chart"><span class="trend-graph-sides"><strong>UTSA</strong><span>% of Bets</span><strong>TXST</strong></span><span class="trend-graph-percentage"><span>40%</span><span>60%</span></span><span class="trend-graph-percentage"><span>60%</span><span>40%</span></span></span></div>'''
        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    streamlit_app.fetch_data.clear()
    monkeypatch.setattr(streamlit_app.requests, "get", fake_get)
    first = streamlit_app.fetch_data("CACHE-TEST")
    second = streamlit_app.fetch_data("CACHE-TEST")
    assert len(calls) == 1
    assert first.loc[0, "Last refresh time"] == second.loc[0, "Last refresh time"]
    streamlit_app.fetch_data.clear()
    streamlit_app.fetch_data("CACHE-TEST")
    assert len(calls) == 2
    streamlit_app.fetch_data.clear()
