from datetime import datetime, timezone

import pandas as pd
import pytest

import streamlit_app as app
from history_store import baseline_eligible
from sharp_core import MAX_TOTAL_DEVIATION_FROM_SPLIT, validate_executable_total


def test_total_sanity_guard_rejects_production_like_corrupt_quote():
    valid, flags = validate_executable_total("o6.5", "54.5")
    assert MAX_TOTAL_DEVIATION_FROM_SPLIT == 15.0
    assert not valid and flags.invalid_total


@pytest.mark.parametrize("executable,split", [
    ("o53.5", "54.5"), ("u55", "54.5"), ("o46.5", "47.5"),
    ("u9", "8.5"), ("o7", "6.5"), ("u218.5", "220.5"),
])
def test_total_sanity_guard_keeps_plausible_totals(executable, split):
    valid, flags = validate_executable_total(executable, split)
    assert valid and not flags.invalid_total


def total_html(over_line="o6.5", over_price="-112", under_line="u54.5", under_price="-108"):
    return f'''<div class="trend-card"><div class="event-header"><span class="team-name">DET</span><span class="team-name">BUF</span></div><span class="trend-graph-chart">
      <span class="trend-graph-sides"><strong>Over o54.5</strong><strong>Under u54.5</strong><span>Total</span></span>
      <span class="trend-graph-percentage"><span>45%</span><span>55%</span></span>
      <span class="trend-graph-percentage"><span>60%</span><span>40%</span></span>
    </span><span class="best-odds">
      <div class="best-odds-container"><span>Best over Odds</span><span class="data-moneyline">{over_line}</span><small class="data-odds best">{over_price}</small></div>
      <div class="best-odds-container"><span>Best under Odds</span><span class="data-moneyline">{under_line}</span><small class="data-odds best">{under_price}</small></div>
    </span></div>'''


def test_parser_rejects_malformed_total_quote_without_corrupting_opposite_quote():
    data = app.parse_scoresandodds_html(total_html(), datetime.now(timezone.utc))
    over = data.iloc[0]
    under = data.iloc[1]
    assert over["Split line"] == "54.5"
    assert over["Best line"] == "N/A" and pd.isna(over["Best price"])
    assert pd.isna(over["Break-even %"]) and over["Line vs split"] == "N/A"
    assert "invalid total" in over["Data quality"]
    assert under["Best line"] == "u54.5" and under["Best price"] == -108


def test_malformed_total_card_has_no_executable_quote_or_baseline_eligibility():
    row = {
        "Market": "Total", "Selection side": "over", "Best line": "N/A", "Split line": "54.5",
    }
    assert app.card_market_presentation(row) == [("Current total", "N/A"), ("Split total", "54.5")]
    stored = {
        "observed_at_utc": "2026-09-13T00:00:00Z", "event_start_utc": "2026-09-14T20:00:00Z",
        "market": "Total", "selection_side": "over", "best_line": "N/A", "best_price": None,
        "data_quality": "invalid total", "money_minus_bets_gap": 15,
    }
    assert not baseline_eligible(stored)
    stored.update(best_line="o53.5", best_price=-112, data_quality="OK")
    assert baseline_eligible(stored)


def test_valid_total_parser_behavior_remains_executable():
    data = app.parse_scoresandodds_html(total_html(over_line="o53.5"), datetime.now(timezone.utc))
    over = data.iloc[0]
    assert over["Best line"] == "o53.5" and over["Best price"] == -112
    assert over["Data quality"] == "OK"


def test_legacy_metrics_are_safe_for_history_display_and_breakdowns():
    legacy = {
        "settled": 2, "wins": 1, "losses": 1, "pushes": 0, "win_rate": 0.5,
        "units": -0.1, "roi": -0.05, "average_clv": 0.5, "positive_clv_rate": 1,
        "average_break_even_probability": 52.38,
    }
    items = dict(app.history_metric_items(8, legacy))
    assert items["Average CLV"] == "N/A"
    frame = app.history_breakdown_frame({"legacy": legacy})
    assert frame.loc["legacy", "Average break-even %"] == "52.4%"
    assert frame.loc["legacy", "Average CLV"] == "N/A"
    assert frame.loc["legacy", "Invalid results excluded"] == 0
