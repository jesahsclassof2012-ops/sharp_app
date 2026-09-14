"""Integration coverage for the live Sharp Signal V2 parser."""

from datetime import datetime, timezone

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
        <div class="best-odds-container"><span>Best away Odds</span><small class="data-odds best">+120</small></div>
        <div class="best-odds-container"><span>Best home Odds</span><small class="data-odds best">-140</small></div>
      </span>
    </div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert list(data["Selection"]) == ["UTSA", "TXST"]
    assert data.loc[0, "Money minus Bets gap"] == 19.0
    assert data.loc[0, "Break-even %"] == 45.45
    assert "Confidence Score" not in data.columns


def test_spread_and_hyphenated_code_are_current_card_identity():
    html = '''
    <div class="trend-card"><span class="trend-graph-chart">
      <span class="trend-graph-sides"><strong>M-OH +3.5</strong><strong>SJSU -3.5</strong><span>Spread</span></span>
      <span class="trend-graph-percentage"><span>45%</span><span>55%</span></span>
      <span class="trend-graph-percentage"><span>60%</span><span>40%</span></span>
    </span><span class="best-odds">
      <div class="best-odds-container"><span>Best away Odds</span><small class="data-odds best">+4 -110</small></div>
      <div class="best-odds-container"><span>Best home Odds</span><small class="data-odds best">-4 -110</small></div>
    </span></div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert data.loc[0, "Matchup"] == "M-OH vs SJSU"
    assert data.loc[0, "Split line"] == "3.5 / -3.5"
    assert data.loc[0, "Best line"] == "+4"


def test_total_uses_best_over_and_under_not_away_home_prices():
    html = '''
    <div class="trend-card"><span class="trend-graph-chart">
      <span class="trend-graph-sides"><strong>Over o45.5</strong><strong>Under u45.5</strong><span>Total</span></span>
      <span class="trend-graph-percentage"><span>49%</span><span>51%</span></span>
      <span class="trend-graph-percentage"><span>56%</span><span>44%</span></span>
    </span><span class="best-odds">
      <div class="best-odds-container"><span>Best over Odds</span><small class="data-odds best">o46 -105</small></div>
      <div class="best-odds-container"><span>Best under Odds</span><small class="data-odds best">u46 -115</small></div>
      <div class="best-odds-container"><span>Best away Odds</span><small class="data-odds best">+300</small></div>
    </span></div>'''
    data = streamlit_app.parse_scoresandodds_html(html, datetime.now(timezone.utc))
    assert list(data["Best price"]) == [-105, -115]
    assert list(data["Best line"]) == ["o46", "u46"]
    assert list(data["Split line"]) == ["45.5", "45.5"]
