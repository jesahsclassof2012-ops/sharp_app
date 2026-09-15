"""CLI collector for persistent Sharp Signal V2 snapshots."""

from __future__ import annotations

import os
import math
from datetime import datetime, timezone
from typing import Any

import requests

from history_store import HistoryStore
from streamlit_app import SPORTS, parse_scoresandodds_html


OPTIONAL_NUMERIC_FIELDS = {
    "bets_pct": ("Bets %", False),
    "money_pct": ("Money %", False),
    "money_minus_bets_gap": ("Money minus Bets gap", False),
    "best_price": ("Best price", True),
    "break_even_pct": ("Break-even %", False),
}
POSTGRES_INTEGER_MIN = -(2 ** 31)
POSTGRES_INTEGER_MAX = 2 ** 31 - 1


def normalize_optional_numeric(value: Any, field: str, integer: bool = False) -> float | int | None:
    """Translate pandas scalar nulls to SQL NULL without inventing values."""
    if value is None or type(value).__name__ == "NAType":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid snapshot numeric field {field}: type={type(value).__name__}") from exc
    if not math.isfinite(number):
        return None
    if integer:
        if not number.is_integer():
            raise ValueError(f"Invalid integer snapshot field {field}: type={type(value).__name__}")
        value_as_int = int(number)
        if not POSTGRES_INTEGER_MIN <= value_as_int <= POSTGRES_INTEGER_MAX:
            raise ValueError(f"Integer snapshot field {field} is outside PostgreSQL INTEGER range")
        return value_as_int
    return number


def snapshot_from_row(row: Any, sport: str, observed_at: datetime) -> dict[str, Any]:
    """Build one persistence record with only SQL-safe optional numerics."""
    start = row["Start time"].astimezone(timezone.utc).isoformat()
    snapshot = {
        "observed_at_utc": observed_at.isoformat(), "sport": sport,
        "matchup": row["Matchup"], "event_start_utc": start,
        "market": row["Market"], "selection": row["Selection"],
        "selection_side": row["Selection side"], "split_line": row["Split line"],
        "best_line": row["Best line"], "data_quality": row["Data quality"],
        "line_vs_split": row["Line vs split"],
    }
    for field, (column, integer) in OPTIONAL_NUMERIC_FIELDS.items():
        snapshot[field] = normalize_optional_numeric(row[column], field, integer)
    return snapshot


def collect_sport(store: HistoryStore, sport: str) -> int:
    response = requests.get(f"https://www.scoresandodds.com/{sport.lower()}/consensus-picks", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    observed_at = datetime.now(timezone.utc)
    data = parse_scoresandodds_html(response.text, observed_at)
    snapshots = []
    for _, row in data.iterrows():
        if row["Start time"] is None:
            continue  # A missing kickoff time can never safely become pregame history.
        start = row["Start time"].astimezone(timezone.utc).isoformat()
        if observed_at >= datetime.fromisoformat(start):
            continue  # Never collect in-game or postgame rows as pregame observations.
        try:
            snapshots.append(snapshot_from_row(row, sport, observed_at))
        except ValueError as exc:
            raise ValueError(
                f"Snapshot normalization failed sport={sport} matchup={row['Matchup']} "
                f"market={row['Market']} selection={row['Selection']}: {exc}"
            ) from exc
    return store.insert_snapshots(snapshots)


def main() -> None:
    if not os.getenv("DATABASE_URL") and os.getenv("GITHUB_ACTIONS") == "true":
        raise SystemExit("DATABASE_URL must be configured for scheduled production collection.")
    store = HistoryStore(production=os.getenv("GITHUB_ACTIONS") == "true")
    inserted = sum(collect_sport(store, sport) for sport in SPORTS)
    print(f"Inserted {inserted} snapshots.")


if __name__ == "__main__":
    main()
