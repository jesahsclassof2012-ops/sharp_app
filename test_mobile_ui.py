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
