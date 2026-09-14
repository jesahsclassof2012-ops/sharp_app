"""CLI collector for persistent Sharp Signal V2 snapshots."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from history_store import HistoryStore, event_key
from streamlit_app import SPORTS, parse_scoresandodds_html


def collect_sport(store: HistoryStore, sport: str) -> int:
    response = requests.get(f"https://www.scoresandodds.com/{sport.lower()}/consensus-picks", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    observed_at = datetime.now(timezone.utc)
    data = parse_scoresandodds_html(response.text, observed_at)
    snapshots = []
    for _, row in data.iterrows():
        start = row["Start time"].astimezone(timezone.utc).isoformat() if row["Start time"] is not None else None
        selection = row["Selection"]
        side = row["Selection side"]
        snapshot = {"observed_at_utc": observed_at.isoformat(), "sport": sport, "matchup": row["Matchup"], "event_start_utc": start, "market": row["Market"], "selection": selection, "selection_side": side, "split_line": row["Split line"], "bets_pct": row["Bets %"], "money_pct": row["Money %"], "money_minus_bets_gap": row["Money minus Bets gap"], "best_line": row["Best line"], "best_price": row["Best price"], "break_even_pct": row["Break-even %"], "data_quality": row["Data quality"]}
        snapshot["event_key"] = event_key(sport, snapshot["matchup"], start, snapshot["market"], selection)
        snapshots.append(snapshot)
    return store.insert_snapshots(snapshots)


def main() -> None:
    if not os.getenv("DATABASE_URL") and os.getenv("GITHUB_ACTIONS") == "true":
        raise SystemExit("DATABASE_URL must be configured for scheduled production collection.")
    store = HistoryStore()
    inserted = sum(collect_sport(store, sport) for sport in SPORTS)
    print(f"Inserted {inserted} snapshots.")


if __name__ == "__main__":
    main()
