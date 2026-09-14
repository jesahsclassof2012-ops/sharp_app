"""Sharp Signal V2 Streamlit application.

The page deliberately reports observed splits and executable prices; it does not
turn them into a prediction, confidence score, or betting recommendation.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd
import pytz
import requests
import streamlit as st
from bs4 import BeautifulSoup, Tag

from sharp_core import (
    DataQualityFlags,
    american_odds_to_break_even_probability,
    calculate_money_minus_bets_screen,
    compare_lines,
    compare_total_lines,
    impute_missing_percentage,
    parse_american_odds,
    parse_spread,
    parse_team_code,
    parse_total,
    validate_percentages,
)


SPORTS = ["NBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB"]
PACIFIC = pytz.timezone("America/Los_Angeles")
DISPLAY_COLUMNS = [
    "Matchup", "Start time", "Market", "Selection", "Bets %", "Money %",
    "Money minus Bets gap", "Signal", "Split line", "Best line", "Best price",
    "Break-even %", "Line vs split", "Data quality", "Last refresh time",
]


def extract_percentage(element: Optional[Tag]) -> Optional[float]:
    """Read a displayed percentage, falling back to the chart-bar width."""
    if not element:
        return None
    text = element.get_text(" ", strip=True).replace("%", "")
    match = re.search(r"\b(\d{1,3}(?:\.\d+)?)\b", text)
    if match:
        return float(match.group(1))
    style = element.get("style", "")
    match = re.search(r"width:\s*(\d+(?:\.\d+)?)%", style, re.I)
    return float(match.group(1)) if match else None


def team_code_from_text(value: str) -> Optional[str]:
    """Find an S&O team code without limiting it to the old 2/3-char format."""
    match = re.match(r"\s*([A-Za-z]{2,4}|[A-Za-z]-[A-Za-z]{2})\b", value or "")
    if not match:
        return None
    if match.group(1).upper() in {"OVER", "UNDER"}:
        return None
    code, flags = parse_team_code(match.group(1))
    return None if flags.malformed_team_code else code


def label_market(label: str, teams: list[str], card_classes: list[str] | None = None) -> str:
    """Classify a trend card, preferring ScoresAndOdds' market-class metadata."""
    class_text = " ".join(card_classes or ()).lower()
    if "consensus-table-spread" in class_text:
        return "Spread"
    if "consensus-table-moneyline" in class_text:
        return "Moneyline"
    if "consensus-table-total" in class_text:
        return "Total"
    text = f"{label} {' '.join(teams)}".lower()
    if "total" in text or re.search(r"\b[ou]\d+(?:\.\d+)?", text):
        return "Total"
    if "spread" in text or re.search(r"\b(?:pk|pick|pick'em)\b", text) or any(re.search(r"[+-]\d", team) for team in teams):
        return "Spread"
    return "Moneyline"


def quote_from_container(container: Tag) -> tuple[Optional[str], Optional[int]]:
    """Return separate executable line and American price from an S&O quote."""
    # In the live page, line and price are distinct siblings: data-moneyline is
    # the executable line and data-odds is the price.  Never infer one from the
    # other just because both contain signed numbers.
    line_node = container.select_one("span.data-moneyline")
    price_node = container.select_one("small.data-odds.best, small.data-odds")
    line = line_node.get_text(" ", strip=True).replace(" ", "") if line_node else None
    price_text = price_node.get_text(" ", strip=True) if price_node else None
    # Moneyline cards have no point/total line.  Their data-moneyline value is
    # itself the executable American price, unlike spread and total cards.
    if price_text is None:
        price, _ = parse_american_odds(line or "N/A")
        return None, price
    price, _ = parse_american_odds(price_text or "N/A")
    return line, price


def best_quotes(card: Tag) -> dict[str, tuple[Optional[str], Optional[int]]]:
    """Read executable quotes by explicit label; totals never borrow away/home odds."""
    quotes: dict[str, tuple[Optional[str], Optional[int]]] = {}
    for container in card.select(".best-odds-container"):
        label = container.get_text(" ", strip=True).lower()
        for key in ("away", "home", "over", "under"):
            if f"best {key}" in label:
                quotes[key] = quote_from_container(container)
    return quotes


def split_line(market: str, team_texts: list[str], label: str) -> Optional[str]:
    """Extract a consensus/split line only from the current market card."""
    if market == "Spread":
        parsed, _ = parse_spread(" / ".join(team_texts))
        return f"{parsed[0]:g} / {parsed[1]:g}" if parsed else None
    if market == "Total":
        total, _ = parse_total(f"{label} {' '.join(team_texts)}")
        return f"{total:g}" if total is not None else None
    return None


def format_start(card: Tag) -> Optional[datetime]:
    node = card.select_one('[data-role="localtime"]')
    raw = node.get("data-value") if node else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PACIFIC)
    except ValueError:
        return None


def quality_text(*flags: Any) -> str:
    issues = []
    for flag_set in flags:
        for name, enabled in vars(flag_set).items():
            if enabled:
                issues.append(name.replace("_", " "))
    return "; ".join(sorted(set(issues))) or "OK"


def line_number(value: Optional[str]) -> Optional[float]:
    """Extract the numeric portion of one spread or total line."""
    if (value or "").strip().upper() in {"PK", "PICK", "PICK'EM"}:
        return 0.0
    match = re.search(r"(?:[ou]\s*)?([+-]?\d+(?:\.\d+)?)", value or "", re.I)
    return float(match.group(1)) if match else None


def line_vs_split_label(market: str, best_line: Optional[str], split: Optional[str], side: str) -> str:
    """Describe the executable line from the selected bettor's perspective."""
    if not best_line or not split or split == "N/A":
        return "N/A"
    if market == "Spread":
        consensus, _ = parse_spread(split)
        best_value = line_number(best_line)
        if not consensus or best_value is None:
            return "N/A"
        index = 0 if side == "away" else 1
        current = (best_value, 0.0) if index == 0 else (0.0, best_value)
        label, movement = compare_lines(current, consensus, side)
    elif market == "Total":
        best_value, _ = parse_total(best_line)
        split_value, _ = parse_total("o" + split)
        label, movement = compare_total_lines(best_value, split_value, side)
    else:
        return "N/A"
    if label == "N/A":
        return label
    direction = "Better" if label.startswith("Better") else "Worse" if label.startswith("Worse") else "Same"
    return f"{direction} ({movement:+g})"


def parse_scoresandodds_html(html: str, refreshed_at: datetime) -> pd.DataFrame:
    """Convert S&O trend cards to one transparent row per selectable side.

    Each card supplies its own teams and matchup identity.  In particular, a
    total card is never attached to whichever prior moneyline/spread card was
    encountered by the scraper.
    """
    rows: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("div.trend-card"):
        chart = card.select_one(".trend-graph-chart")
        if not chart:
            continue
        sides = chart.select_one(".trend-graph-sides")
        team_texts = [item.get_text(" ", strip=True) for item in sides.select("strong")] if sides else []
        label_node = sides.select_one("span") if sides else None
        label = label_node.get_text(" ", strip=True) if label_node else ""
        market = label_market(label, team_texts, card.get("class", []))
        codes = [team_code_from_text(team) for team in team_texts]
        card_flags = [parse_team_code(code)[1] for code in codes if code]
        event_teams = [node.get_text(" ", strip=True) for node in card.select(".event-header .team-name")]
        matchup_node = card.select_one(".event-matchup, .trend-card-title, [data-role='matchup']")
        matchup = matchup_node.get_text(" ", strip=True) if matchup_node else " vs ".join(event_teams or [c for c in codes if c])
        if not matchup:
            matchup = "Unidentified matchup"
            card_flags.append(DataQualityFlags(missing_matchup=True))

        percentage_groups = chart.select(".trend-graph-percentage")
        def pair(index: int) -> tuple[Optional[float], Optional[float]]:
            if len(percentage_groups) <= index:
                return None, None
            values = percentage_groups[index].select("span")
            return (extract_percentage(values[0]) if len(values) > 0 else None,
                    extract_percentage(values[1]) if len(values) > 1 else None)
        bet_one, bet_two = impute_missing_percentage(*pair(0))
        money_one, money_two = impute_missing_percentage(*pair(1))
        percentage_flags = validate_percentages(bet_one, bet_two)
        money_flags = validate_percentages(money_one, money_two)
        quotes = best_quotes(card)
        current_split = split_line(market, team_texts, label)
        selections = [("Over", bet_one, money_one, "over"), ("Under", bet_two, money_two, "under")] if market == "Total" else [
            (codes[0] or team_texts[0] if team_texts else "Away", bet_one, money_one, "away"),
            (codes[1] or team_texts[1] if len(team_texts) > 1 else "Home", bet_two, money_two, "home"),
        ]
        for selection, bets, money, quote_key in selections:
            best_line, best_price = quotes.get(quote_key, (None, None))
            odds_flags = parse_american_odds(str(best_price) if best_price is not None else "N/A")[1]
            gap, _ = calculate_money_minus_bets_screen(money, bets)
            # A readable observation, not an inferred probability or rating.
            signal = "Money exceeds bets" if gap is not None and gap > 0 else "Bets exceed money" if gap is not None and gap < 0 else "Unavailable"
            break_even = american_odds_to_break_even_probability(best_price)
            line_vs_split = line_vs_split_label(market, best_line, current_split, quote_key)
            rows.append({
                "Matchup": matchup, "Start time": format_start(card), "Market": market,
                "Selection": selection, "Bets %": bets, "Money %": money,
                "Money minus Bets gap": gap, "Signal": signal, "Split line": current_split or "N/A",
                "Best line": best_line or "N/A", "Best price": best_price,
                "Break-even %": round(break_even * 100, 2) if break_even is not None else None,
                "Line vs split": line_vs_split,
                "Data quality": quality_text(*card_flags, percentage_flags, money_flags, odds_flags),
                "Last refresh time": refreshed_at.astimezone(PACIFIC),
            })
    return pd.DataFrame(rows, columns=DISPLAY_COLUMNS)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_data(sport: str) -> pd.DataFrame:
    """Fetch source data once per sport/TTL; filters must not bust this cache."""
    response = requests.get(
        f"https://www.scoresandodds.com/{sport.lower()}/consensus-picks",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
    )
    response.raise_for_status()
    return parse_scoresandodds_html(response.text, datetime.now(timezone.utc))


def add_session_movement(data: pd.DataFrame) -> pd.DataFrame:
    """Compare only snapshots made in this browser session; no history is stored."""
    prior = st.session_state.get("sharp_v2_snapshot", {})
    movement = []
    for _, row in data.iterrows():
        key = (row["Matchup"], row["Market"], row["Selection"])
        old = prior.get(key)
        movement.append("New this session" if old is None else f"Prior best line: {old}")
    st.session_state["sharp_v2_snapshot"] = {
        (row["Matchup"], row["Market"], row["Selection"]): row["Best line"] for _, row in data.iterrows()
    }
    data = data.copy()
    data["Session movement"] = movement
    return data


def main() -> None:
    st.set_page_config(page_title="Sharp Signal V2", layout="wide", initial_sidebar_state="expanded")
    st.title("Sharp Signal V2")
    st.caption("Transparent split screening. Signals are observations, not betting advice.")
    sport = st.sidebar.selectbox("Sport", SPORTS)
    min_gap = st.sidebar.slider("Minimum Money minus Bets gap", -50.0, 50.0, 0.0, 0.5)
    max_tickets = st.sidebar.slider("Maximum ticket share", 0.0, 100.0, 100.0, 1.0)
    market = st.sidebar.selectbox("Market", ["All", "Moneyline", "Spread", "Total"])
    hours = st.sidebar.slider("Time window (hours)", 1, 168, 24)
    require_price = st.sidebar.checkbox("Require current best price", value=True)
    if st.sidebar.button("Refresh"):
        fetch_data.clear()
    try:
        data = fetch_data(sport)
    except requests.RequestException as exc:
        st.error(f"Could not load ScoresAndOdds: {exc}")
        return
    if data.empty:
        st.info("No consensus cards were available for this sport.")
        return
    now = datetime.now(PACIFIC)
    data = data[(data["Money minus Bets gap"].fillna(-999) >= min_gap) & (data["Bets %"].fillna(101) <= max_tickets)]
    if market != "All":
        data = data[data["Market"] == market]
    data = data[data["Start time"].isna() | ((data["Start time"] >= now) & (data["Start time"] <= now + timedelta(hours=hours)))]
    if require_price:
        data = data[data["Best price"].notna()]
    data = add_session_movement(data).sort_values(["Start time", "Money minus Bets gap"], ascending=[True, False])
    st.caption("Session movement compares this browser session only; it is not persistent historical backtesting.")
    st.dataframe(data, use_container_width=True, hide_index=True, column_config={
        "Start time": st.column_config.DatetimeColumn(format="MMM D, h:mm a"),
        "Last refresh time": st.column_config.DatetimeColumn(format="MMM D, h:mm:ss a"),
    })


if __name__ == "__main__":
    main()
