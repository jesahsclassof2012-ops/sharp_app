import pandas as pd

import streamlit_app as app


def test_mobile_display_formatting_is_concise_and_non_mutating():
    percent = 72.0
    gap = 31.0
    price = -110
    line = "+3.5"
    assert app.display_value(None) == "N/A"
    assert app.format_percent(percent) == "72%"
    assert app.format_percent(52.38, 1) == "52.4%"
    assert app.format_gap(gap) == "+31"
    assert app.format_gap(-31.0) == "-31"
    assert app.format_price(150) == "+150"
    assert app.format_price(price) == "-110"
    assert app.format_line(line) == "+3.5"
    assert percent == 72.0 and gap == 31.0 and price == -110 and line == "+3.5"


def test_mobile_display_missing_values_are_na():
    assert app.format_percent(None) == "N/A"
    assert app.format_gap(None) == "N/A"
    assert app.format_price(None) == "N/A"
    assert app.format_line(None) == "N/A"


def test_card_time_is_converted_to_pacific_before_pt_labeling():
    utc = "2026-09-14T20:00:00Z"
    pacific = "2026-09-14T13:00:00-07:00"
    assert app.format_card_start(utc) == app.format_card_start(pacific)
    assert app.format_card_start(utc).endswith("PT")


def test_filter_signature_and_summary_are_deterministic():
    first = app.card_filter_signature("NFL", "All", 0.0, 100.0, 100.0, 24, True)
    assert first == app.card_filter_signature("NFL", "All", 0.0, 100.0, 100.0, 24, True)
    assert first != app.card_filter_signature("NFL", "All", 0.0, 100.0, 80.0, 24, True)
    assert first != app.card_filter_signature("NCAAF", "All", 0.0, 100.0, 100.0, 24, True)
    assert app.active_filter_summary("NFL", "All", 24) == "NFL · All markets · Next 24h"
    assert app.active_filter_summary("NFL", "All", 24, 80.0) == "NFL · All markets · Next 24h · Money ≤ 80%"
    assert app.active_filter_summary("NFL", "Spread", 48, 85.0, 70.0) == "NFL · Spread markets · Next 48h · Money ≤ 85% · Tickets ≤ 70%"


def test_maximum_money_share_filter_is_inclusive_and_missing_safe():
    data = pd.DataFrame({"Bets %": [50.0, 50.0, 50.0, 50.0], "Money %": [79.0, 80.0, 81.0, None]})
    assert list(app.apply_share_filters(data, 100.0, 80.0).index) == [0, 1]
    assert list(app.apply_share_filters(data, 100.0, 100.0).index) == [0, 1, 2]
    assert list(app.apply_share_filters(data, 49.0, 100.0).index) == []
