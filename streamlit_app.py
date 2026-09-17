"""Sharp Signal V2 Streamlit application.

The page deliberately reports observed splits and executable prices; it does not
turn them into a prediction, confidence score, or betting recommendation.
"""

from __future__ import annotations

import re
import csv
import io
import os
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
    validate_executable_total,
    validate_percentages,
)
from history_store import HistoryStore, bucket_performance, game_key, performance, signal_key


SPORTS = ["NBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB"]
PACIFIC = pytz.timezone("America/Los_Angeles")
DISPLAY_COLUMNS = [
    "Matchup", "Start time", "Market", "Selection", "Bets %", "Money %",
    "Money minus Bets gap", "Signal", "Split line", "Best line", "Best price",
    "Break-even %", "Line vs split", "Data quality", "Last refresh time",
]
RESULT_TABLE_COLUMNS = [
    "Matchup", "Start time", "Market", "Selection", "Bets %", "Money %",
    "Money minus Bets gap", "Gap Δ 60m", "Split line", "Best line", "Best price",
    "Break-even %", "Line vs split", "Data quality", "Session movement",
]
RESULT_VIEW_OPTIONS = ["Cards", "Table"]
DEFAULT_RESULTS_VIEW = "Table"
MIXED_MARKET_CLV_MESSAGE = "Average CLV is not combined across different market types because CLV definitions are market-specific."
MOVEMENT_WINDOW = timedelta(minutes=60)
# Snapshots run approximately every 15 minutes; accept normal scheduler drift,
# but never silently widen this 60-minute comparison beyond ±20 minutes.
MOVEMENT_TOLERANCE = timedelta(minutes=20)


def display_value(value: Any) -> str:
    """Keep missing values concise without mutating the source data."""
    return "N/A" if value is None or pd.isna(value) else str(value)


def format_percent(value: Any, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.{decimals}f}%"


def format_gap(value: Any) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):+g}"


def utc_timestamp(value: Any, assume_utc: bool = False) -> Optional[datetime]:
    """Return an aware UTC timestamp, never comparing naive and aware values."""
    if value is None or pd.isna(value):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        if not assume_utc:
            return None
        stamp = stamp.tz_localize(timezone.utc)
    return stamp.tz_convert(timezone.utc).to_pydatetime()


def format_first_seen(value: Any) -> str:
    stamp = utc_timestamp(value, assume_utc=True)
    if stamp is None:
        return "First seen N/A"
    local = stamp.astimezone(PACIFIC)
    return f"First seen {local.strftime('%I').lstrip('0')}:{local.strftime('%M %p')} PT"


def movement_card_caption(row: dict[str, Any]) -> str:
    """Compact persistent split movement; session movement remains secondary."""
    prior, current, delta = (row.get("Historical gap 60m"), row.get("Money minus Bets gap"), row.get("Gap Δ 60m"))
    if any(value is None or pd.isna(value) for value in (prior, current, delta)):
        return "60m movement: N/A"
    return f"Gap {format_gap(prior)} → {format_gap(current)} ({format_gap(delta)} in ~60m)"


def format_price(value: Any) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    price = int(value)
    return f"+{price}" if price > 0 else str(price)


def format_line(value: Any) -> str:
    return display_value(value)


def format_clv(value: Any, market: Any) -> str:
    """Keep CLV units explicit rather than combining points and probability."""
    if value is None or pd.isna(value):
        return "N/A"
    if market == "Moneyline":
        return f"{float(value) * 100:+.1f} pp"
    if market in {"Spread", "Total"}:
        return f"{float(value):+g} pts"
    return "N/A"


def format_final_score(away_score: Any, home_score: Any) -> str:
    if away_score is None or home_score is None or pd.isna(away_score) or pd.isna(home_score):
        return "N/A"
    return f"{int(away_score)}-{int(home_score)}"


def format_card_start(value: Any) -> str:
    if value is None or pd.isna(value):
        return "Start time N/A"
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(PACIFIC)
    else:
        stamp = stamp.tz_convert(PACIFIC)
    return f"{stamp.strftime('%b')} {stamp.day}, {stamp.strftime('%I').lstrip('0')}:{stamp.strftime('%M %p')} PT"


def active_filter_summary(sport: str, market: str, hours: int, max_money: float = 100.0, max_tickets: float = 100.0) -> str:
    """Summarize only live-board filters that actually narrow the board."""
    parts = [f"{sport} · {market} markets · Next {hours}h"]
    if max_money < 100:
        parts.append(f"Money ≤ {max_money:g}%")
    if max_tickets < 100:
        parts.append(f"Tickets ≤ {max_tickets:g}%")
    return " · ".join(parts)


def card_filter_signature(sport: str, market: str, min_gap: float, max_tickets: float, max_money: float, hours: int, require_price: bool) -> tuple[Any, ...]:
    return sport, market, min_gap, max_tickets, max_money, hours, require_price


def default_results_view() -> str:
    """Expose the first-session default independently from Streamlit state."""
    return DEFAULT_RESULTS_VIEW


def matching_signals_label(count: int) -> str:
    return f"{count} matching signal{'s' if count != 1 else ''}"


def apply_share_filters(data: pd.DataFrame, max_tickets: float, max_money: float) -> pd.DataFrame:
    """Screen shares without changing the source percentages or their meaning."""
    return data[(data["Bets %"].fillna(101) <= max_tickets) & (data["Money %"].fillna(101) <= max_money)]


def history_sports(snapshots: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[str]:
    """Return the persistent-history sport values, rather than a fixed league list."""
    return sorted({str(record["sport"]) for record in [*snapshots, *rows] if record.get("sport")})


def filter_history_by_sport(snapshots: list[dict[str, Any]], rows: list[dict[str, Any]], sport: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scope all visible history records to one selected sport when requested."""
    if sport == "All sports":
        return snapshots, rows
    return ([record for record in snapshots if record.get("sport") == sport],
            [record for record in rows if record.get("sport") == sport])


def history_scope_metrics(snapshots: list[dict[str, Any]], rows: list[dict[str, Any]], sport: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep history counts and performance calculations on the same sport scope."""
    filtered_snapshots, filtered_rows = filter_history_by_sport(snapshots, rows, sport)
    return filtered_snapshots, filtered_rows, performance(filtered_rows)


def history_export_filename(prefix: str, sport: str) -> str:
    suffix = "all_sports" if sport == "All sports" else sport.lower().replace(" ", "_")
    return f"{prefix}_{suffix}.csv"


def insufficient_sample_message(sport: str) -> str:
    qualifier = "" if sport == "All sports" else f" {sport}"
    return f"Insufficient{qualifier} sample size: fewer than 30 settled observations."


def card_market_presentation(row: dict[str, Any]) -> list[tuple[str, str]]:
    """Return market-aware card fields without changing the raw scanner row."""
    market, side = row.get("Market"), row.get("Selection side")
    best_line, split = row.get("Best line"), row.get("Split line")
    if market == "Total":
        current, _ = parse_total(str(best_line or ""))
        split_total, _ = parse_total("o" + str(split or ""))
        return [("Current total", "N/A" if current is None else f"{current:g}"),
                ("Split total", "N/A" if split_total is None else f"{split_total:g}")]
    if market == "Spread":
        current = line_number(str(best_line or ""))
        consensus, _ = parse_spread(str(split or ""))
        index = 0 if side == "away" else 1 if side == "home" else None
        selected_split = consensus[index] if consensus and index is not None else None
        return [("Current spread", "N/A" if current is None else f"{current:+g}"),
                ("Split spread", "N/A" if selected_split is None else f"{selected_split:+g}")]
    return []


def history_audit_rows(rows: list[dict[str, Any]], limit: int = 50) -> pd.DataFrame:
    """Build a bounded, newest-first row-level audit view from settled entries."""
    records = []
    for row in sorted(rows, key=lambda item: str(item.get("event_start_utc") or ""), reverse=True)[:limit]:
        records.append({
            "Event start": row.get("event_start_utc"), "Sport": row.get("sport"),
            "Matchup": row.get("matchup"), "Market": row.get("market"),
            "Selection": row.get("selection"), "Entry line": row.get("best_line"),
            "Entry price": format_price(row.get("best_price")), "Bets %": format_percent(row.get("bets_pct")),
            "Money %": format_percent(row.get("money_pct")), "Gap": format_gap(row.get("money_minus_bets_gap")),
            "Final score": format_final_score(row.get("away_score"), row.get("home_score")),
            "Result": row.get("bet_result"), "Closing line": row.get("closing_line") or "N/A",
            "Closing price": format_price(row.get("closing_price")), "CLV": format_clv(row.get("clv"), row.get("market")),
            "Result source": row.get("result_source") or "N/A",
        })
    return pd.DataFrame(records)


def history_metric_items(stored_observations: int, metrics: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Use compatibility defaults for optional display-only performance fields."""
    average_clv = metrics.get("average_clv")
    clv_market = metrics.get("clv_market")
    return (
        ("Stored observations", stored_observations),
        ("Unique settled baseline signals", metrics["settled"]), ("Wins", metrics["wins"]),
        ("Losses", metrics["losses"]), ("Pushes", metrics["pushes"]),
        ("Win rate", metrics["win_rate"]), ("Units", metrics["units"]), ("ROI", metrics["roi"]),
        ("Average CLV", format_clv(average_clv, clv_market)),
        ("Positive CLV rate", metrics["positive_clv_rate"]),
    )


def history_breakdown_frame(summary: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """Format summary values without presenting average American odds or mixed CLV."""
    records = []
    for bucket, metrics in summary.items():
        records.append({
            "Bucket": bucket, "Sample": metrics["settled"], "Wins": metrics["wins"],
            "Losses": metrics["losses"], "Pushes": metrics["pushes"], "Units": metrics["units"],
            "Win rate": format_percent(None if metrics["win_rate"] is None else metrics["win_rate"] * 100, 1),
            "ROI": format_percent(None if metrics["roi"] is None else metrics["roi"] * 100, 1),
            "Average break-even %": format_percent(metrics.get("average_break_even_pct", metrics.get("average_break_even_probability")), 1),
            "Average CLV": format_clv(metrics.get("average_clv"), metrics.get("clv_market")),
            "Positive CLV rate": format_percent(None if metrics["positive_clv_rate"] is None else metrics["positive_clv_rate"] * 100, 1),
            "Invalid results excluded": metrics.get("invalid_results", 0),
        })
    return pd.DataFrame(records).set_index("Bucket") if records else pd.DataFrame()


def render_signal_cards(data: pd.DataFrame) -> None:
    """Render bounded, touch-friendly native cards without dropping rows."""
    shown = st.session_state.get("sharp_cards_shown", 20)
    shown = min(shown, len(data))
    for _, row in data.iloc[:shown].iterrows():
        with st.container(border=True):
            st.markdown(f"**{display_value(row['Matchup'])}**")
            st.caption(f"{format_card_start(row['Start time'])} · {display_value(row['Market'])} · Selection: {display_value(row['Selection'])}")
            with st.container(horizontal=True, wrap=True, gap="small"):
                st.metric("Money", format_percent(row["Money %"]), width="content")
                st.metric("Bets", format_percent(row["Bets %"]), width="content")
                st.metric("Gap", format_gap(row["Money minus Bets gap"]), width="content")
            with st.container(horizontal=True, wrap=True, gap="small"):
                for label, value in card_market_presentation(row.to_dict()):
                    st.metric(label, value, width="content")
                st.metric("Price", format_price(row["Best price"]), width="content")
                st.metric("Break-even", format_percent(row["Break-even %"], 1), width="content")
            relationship = "" if row["Market"] == "Moneyline" else f"Line vs split: {display_value(row['Line vs split'])} · "
            st.caption(f"{movement_card_caption(row.to_dict())} · {format_first_seen(row.get('First seen'))}")
            st.caption(f"{relationship}Data quality: {display_value(row['Data quality'])} · Session: {display_value(row['Session movement'])}")
    if shown < len(data):
        if st.button(f"Show more ({len(data) - shown} remaining)", use_container_width=True, key="show_more_cards"):
            st.session_state["sharp_cards_shown"] = min(len(data), shown + 20)
            st.rerun()
    elif len(data) > 20:
        st.caption(f"Showing all {len(data)} signals.")


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
            total_flags = DataQualityFlags()
            if market == "Total":
                total_is_valid, total_flags = validate_executable_total(best_line, current_split)
                if not total_is_valid:
                    best_line, best_price = None, None
            odds_flags = parse_american_odds(str(best_price) if best_price is not None else "N/A")[1]
            gap, _ = calculate_money_minus_bets_screen(money, bets)
            # A readable observation, not an inferred probability or rating.
            signal = "Money exceeds bets" if gap is not None and gap > 0 else "Bets exceed money" if gap is not None and gap < 0 else "Unavailable"
            break_even = american_odds_to_break_even_probability(best_price)
            line_vs_split = line_vs_split_label(market, best_line, current_split, quote_key)
            rows.append({
                "Matchup": matchup, "Start time": format_start(card), "Market": market,
                "Selection": selection, "Selection side": quote_key, "Bets %": bets, "Money %": money,
                "Money minus Bets gap": gap, "Signal": signal, "Split line": current_split or "N/A",
                "Best line": best_line or "N/A", "Best price": best_price,
                "Break-even %": round(break_even * 100, 2) if break_even is not None else None,
                "Line vs split": line_vs_split,
                "Data quality": quality_text(*card_flags, percentage_flags, money_flags, total_flags, odds_flags),
                "Last refresh time": refreshed_at.astimezone(PACIFIC),
            })
    return pd.DataFrame(rows, columns=DISPLAY_COLUMNS + ["Selection side"])


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


def live_signal_identity(row: dict[str, Any], sport: str) -> Optional[tuple[str, datetime]]:
    """Build the existing deterministic signal identity for one current row."""
    event_start = utc_timestamp(row.get("Start time"))
    observed = utc_timestamp(row.get("Last refresh time"))
    if event_start is None or observed is None:
        return None
    try:
        game = game_key(sport, str(row["Matchup"]), event_start)
        return signal_key(game, str(row["Market"]), str(row["Selection"])), observed
    except (KeyError, TypeError, ValueError):
        return None


def add_persistent_movement(data: pd.DataFrame, sport: str, store_factory=HistoryStore) -> pd.DataFrame:
    """Attach optional, exact-signal movement using two bounded database reads.

    The closest pregame split snapshot to refresh-minus-60-minutes is accepted
    only within the documented ±20-minute tolerance.  Quote executability is
    intentionally irrelevant: this is a Money-minus-Bets split metric.
    """
    output = data.copy()
    output["Gap Δ 60m"] = None
    output["Historical gap 60m"] = None
    output["First seen"] = None
    identities = {
        index: identity
        for index, row in output.iterrows()
        if (identity := live_signal_identity(row.to_dict(), sport)) is not None
    }
    if not identities or not os.getenv("DATABASE_URL"):
        return output
    signal_keys = [identity[0] for identity in identities.values()]
    observations = [identity[1] for identity in identities.values()]
    try:
        # One bounded candidate query plus one grouped MIN query for all rows.
        store = store_factory(production=True)
        candidates = store.movement_snapshots(
            signal_keys,
            min(observations) - MOVEMENT_WINDOW - MOVEMENT_TOLERANCE,
            max(observations),
        )
        first_seen = store.first_seen_for_signals(signal_keys)
    except Exception:
        # Persistent history is optional: production scanner rendering remains
        # available if DATABASE_URL, PostgreSQL, or these movement reads fail.
        return output
    by_signal: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_signal.setdefault(candidate["signal_key"], []).append(candidate)
    for index, (signal, current_at) in identities.items():
        first = utc_timestamp(first_seen.get(signal), assume_utc=True)
        if first is not None and first <= current_at:
            output.at[index, "First seen"] = first
        current_gap = output.at[index, "Money minus Bets gap"]
        if current_gap is None or pd.isna(current_gap):
            continue
        target = current_at - MOVEMENT_WINDOW
        eligible = []
        for candidate in by_signal.get(signal, []):
            observed = utc_timestamp(candidate.get("observed_at_utc"), assume_utc=True)
            event_start = utc_timestamp(candidate.get("event_start_utc"), assume_utc=True)
            gap = candidate.get("money_minus_bets_gap")
            if observed is None or event_start is None or observed >= current_at or observed >= event_start or gap is None or pd.isna(gap):
                continue
            distance = abs(observed - target)
            if distance <= MOVEMENT_TOLERANCE:
                eligible.append((distance, observed, candidate))
        if not eligible:
            continue
        _, _, prior = min(eligible, key=lambda item: (item[0], item[1]))
        prior_gap = prior["money_minus_bets_gap"]
        output.at[index, "Historical gap 60m"] = prior_gap
        output.at[index, "Gap Δ 60m"] = float(current_gap) - float(prior_gap)
    return output


def render_history() -> None:
    """Persistent-history view; conclusions stay descriptive at small samples."""
    st.divider()
    with st.expander("History / Performance", expanded=False):
        if not os.getenv("DATABASE_URL"):
            st.warning("History unavailable: configure DATABASE_URL.")
            return
        try:
            store = HistoryStore(production=True)
        except Exception as exc:
            st.warning(f"History unavailable: database connection failed ({exc}).")
            return
        snapshots = store.snapshots()
        rows = store.analytics_rows()
        sports = history_sports(snapshots, rows)
        options = ["All sports", *sports]
        current = st.session_state.get("history_sport", "All sports")
        if current not in options:
            st.session_state["history_sport"] = "All sports"
        selected_sport = st.selectbox("History sport", options, key="history_sport")
        filtered_snapshots, filtered_rows, metrics = history_scope_metrics(snapshots, rows, selected_sport)
        st.caption("Persistent database history. Small samples are not evidence of a profitable strategy.")
        with st.expander("Methodology", expanded=False):
            st.markdown("""**Baseline entry:** earliest snapshot for a signal with a valid pregame timestamp, `Data quality = OK`, an executable best price, a valid Spread/Total line where required, and `Money % − Bets % > 0`.

**Result:** settlement uses the captured baseline-entry line, never a later or closing line. **Close:** last captured valid executable pregame quote; it is not an official sportsbook closing line.

**ROI:** one unit is risked per settled baseline signal. A win uses the captured American entry price; loss is −1 unit; push is 0 net units. Pushes remain in the ROI denominator and are excluded from the win-rate denominator.

**CLV:** positive means the baseline entry was bettor-favorable versus that captured pregame close. CLV is not evidence of predictive value or profitability.""")
        metric_items = history_metric_items(len(filtered_snapshots), metrics)
        with st.container(horizontal=True, wrap=True, gap="small"):
            for label, value in metric_items:
                if label in {"Win rate", "ROI", "Positive CLV rate"}:
                    st.metric(label, "N/A" if value is None else f"{value:.1%}", width="content")
                else:
                    st.metric(label, "N/A" if value is None else f"{value:.3f}" if isinstance(value, float) else value, width="content")
        if metrics["settled"] and metrics.get("average_clv") is None and metrics["positive_clv_rate"] is not None:
            st.caption(MIXED_MARKET_CLV_MESSAGE)
        if metrics.get("invalid_results", 0):
            st.warning(f"Excluded {metrics['invalid_results']} row(s) with an invalid settlement result from performance metrics.")
        if not filtered_rows:
            st.info("No settled history is available yet.")
        elif metrics["settled"] < 30:
            st.info(insufficient_sample_message(selected_sport))
        breakdowns = {"Money minus Bets gap": "money_minus_bets_gap", "Sport": "sport", "Market": "market", "Ticket share": "bets_pct", "Line vs split": "line_vs_split", "Price range": "best_price"}
        selected = st.selectbox("Breakdown", list(breakdowns), key="history_breakdown")
        summary = bucket_performance(filtered_rows, breakdowns[selected])
        if summary:
            st.dataframe(history_breakdown_frame(summary), use_container_width=True)
        st.subheader("Recent settled signals")
        audit = history_audit_rows(filtered_rows)
        if audit.empty:
            st.info("No settled signals are available for this history scope.")
        else:
            st.dataframe(audit, use_container_width=True, hide_index=True, column_config={
                "Event start": st.column_config.DatetimeColumn(format="MMM D, h:mm a"),
            })
        with st.expander("Exports", expanded=False):
            for label, records, prefix in (("Export snapshots CSV", filtered_snapshots, "snapshots"), ("Export settled results CSV", filtered_rows, "settled_results")):
                output = io.StringIO()
                if records:
                    writer = csv.DictWriter(output, fieldnames=sorted({key for row in records for key in row}))
                    writer.writeheader()
                    writer.writerows(records)
                st.download_button(label, output.getvalue(), file_name=history_export_filename(prefix, selected_sport), mime="text/csv", use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Sharp Signal V2", layout="wide", initial_sidebar_state="auto")
    st.title("Sharp Signal V2")
    st.caption("Transparent split screening.")
    st.caption("Signals are observations, not betting advice.")
    with st.expander("Filters", expanded=False):
        sport = st.selectbox("Sport", SPORTS, key="filter_sport")
        min_gap = st.slider("Minimum Money minus Bets gap", -50.0, 50.0, 0.0, 0.5, key="filter_gap")
        max_tickets = st.slider("Maximum ticket share", 0.0, 100.0, 100.0, 1.0, key="filter_tickets")
        max_money = st.slider("Maximum money share", 0.0, 100.0, 100.0, 1.0, key="filter_money")
        market = st.selectbox("Market", ["All", "Moneyline", "Spread", "Total"], key="filter_market")
        hours = st.slider("Time window (hours)", 1, 168, 24, key="filter_hours")
        require_price = st.checkbox("Require current best price", value=True, key="filter_price")
        if st.button("Refresh data", use_container_width=True, key="refresh_data"):
            fetch_data.clear()
    st.caption(active_filter_summary(sport, market, hours, max_money, max_tickets))
    try:
        data = fetch_data(sport)
    except requests.RequestException as exc:
        st.error(f"Could not load ScoresAndOdds: {exc}")
        return
    if data.empty:
        st.info("No consensus cards were available for this sport.")
        render_history()
        return
    now = datetime.now(PACIFIC)
    data = data[data["Money minus Bets gap"].fillna(-999) >= min_gap]
    data = apply_share_filters(data, max_tickets, max_money)
    if market != "All":
        data = data[data["Market"] == market]
    data = data[data["Start time"].isna() | ((data["Start time"] >= now) & (data["Start time"] <= now + timedelta(hours=hours)))]
    if require_price:
        data = data[data["Best price"].notna()]
    data = add_session_movement(data)
    data = add_persistent_movement(data, sport).sort_values(["Start time", "Money minus Bets gap"], ascending=[True, False])
    signature = card_filter_signature(sport, market, min_gap, max_tickets, max_money, hours, require_price)
    if st.session_state.get("sharp_cards_filter_signature") != signature:
        st.session_state["sharp_cards_filter_signature"] = signature
        st.session_state["sharp_cards_shown"] = 20
    if data.empty:
        st.info("No matching signals for these filters. Expand Filters to adjust the screen.")
        render_history()
        return
    refresh_time = data["Last refresh time"].max()
    st.caption(f"Last data refresh: {format_card_start(refresh_time).replace('Start time ', '')}")
    st.subheader(matching_signals_label(len(data)))
    view = st.radio("Results view", RESULT_VIEW_OPTIONS, index=RESULT_VIEW_OPTIONS.index(default_results_view()), horizontal=True, key="results_view")
    if view == "Cards":
        render_signal_cards(data)
    else:
        st.caption("Gap Δ 60m uses exact-signal pregame snapshots closest to 60 minutes before the fetched refresh time; session movement remains browser-only.")
        table = data[RESULT_TABLE_COLUMNS].copy()
        table["Gap Δ 60m"] = table["Gap Δ 60m"].map(format_gap)
        st.dataframe(table, use_container_width=True, hide_index=True, column_config={
            "Start time": st.column_config.DatetimeColumn(format="MMM D, h:mm a"),
            "Bets %": st.column_config.NumberColumn(format="%.0f%%"),
            "Money %": st.column_config.NumberColumn(format="%.0f%%"),
            "Money minus Bets gap": st.column_config.NumberColumn("Gap", format="%+.1f"),
            "Best price": st.column_config.NumberColumn("Price", format="%+d"),
            "Break-even %": st.column_config.NumberColumn("Break-even", format="%.1f%%"),
        })
    render_history()


if __name__ == "__main__":
    main()
