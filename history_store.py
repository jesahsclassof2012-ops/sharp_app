"""Persistent Phase 2 history storage, settlement, CLV, and performance math."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_key(sport: str, matchup: str, event_start_utc: Any, market: str, selection: str) -> str:
    """Stable event/market/selection identity independent of card order."""
    value = "|".join(str(v or "").strip().lower() for v in (sport, matchup, event_start_utc, market, selection))
    return hashlib.sha256(value.encode()).hexdigest()


def american_profit(odds: int, stake: float = 1.0) -> float:
    return stake * odds / 100 if odds > 0 else stake * 100 / abs(odds)


def implied_probability(odds: Optional[int]) -> Optional[float]:
    if odds is None or odds == 0:
        return None
    return 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)


def settle_selection(market: str, side: str, line: Optional[float], away_score: int, home_score: int) -> str:
    """Return win/loss/push using the captured historical line, never a new line."""
    side = side.lower()
    if market == "Moneyline":
        value = away_score - home_score if side == "away" else home_score - away_score
    elif market == "Spread":
        if line is None:
            raise ValueError("Spread settlement requires the recorded line")
        value = (away_score - home_score + line) if side == "away" else (home_score - away_score + line)
    elif market == "Total":
        if line is None:
            raise ValueError("Total settlement requires the recorded line")
        value = (away_score + home_score - line) if side == "over" else (line - away_score - home_score)
    else:
        raise ValueError(f"Unsupported market: {market}")
    return "win" if value > 0 else "loss" if value < 0 else "push"


def calculate_clv(market: str, side: str, entry_line: Optional[float], closing_line: Optional[float], entry_price: Optional[int], closing_price: Optional[int]) -> Optional[float]:
    """Positive CLV means the captured entry was bettor-favorable; None means unavailable."""
    side = side.lower()
    if market == "Moneyline":
        entry, closing = implied_probability(entry_price), implied_probability(closing_price)
        return None if entry is None or closing is None else closing - entry
    if entry_line is None or closing_line is None:
        return None
    movement = closing_line - entry_line
    if market == "Spread":
        return movement  # higher is better for either spread side
    if market == "Total":
        return -movement if side == "over" else movement if side == "under" else None
    return None


def gap_bucket(value: Optional[float]) -> str:
    if value is None:
        return "missing"
    for lower, upper, label in ((0, 5, "0-5"), (5, 10, "5-10"), (10, 15, "10-15"), (15, 20, "15-20"), (20, 30, "20-30")):
        if lower <= value < upper:
            return label
    return "30+" if value >= 30 else "negative"


@dataclass
class HistoryStore:
    database_url: Optional[str] = None

    def __post_init__(self) -> None:
        self.database_url = self.database_url or os.getenv("DATABASE_URL")
        self.is_postgres = bool(self.database_url and self.database_url.startswith(("postgres://", "postgresql://")))
        if self.is_postgres:
            try:
                import psycopg  # type: ignore
            except ImportError as exc:  # clear production failure, local fallback otherwise
                raise RuntimeError("DATABASE_URL requires psycopg; install requirements.txt") from exc
            self.connection = psycopg.connect(self.database_url)
        else:
            path = self.database_url.removeprefix("sqlite:///") if self.database_url and self.database_url.startswith("sqlite:///") else "sharp_signal_history.db"
            Path(path).parent.mkdir(parents=True, exist_ok=True) if path != ":memory:" else None
            self.connection = sqlite3.connect(path)
            self.connection.row_factory = sqlite3.Row
        self.initialize()

    @property
    def placeholder(self) -> str:
        return "%s" if self.is_postgres else "?"

    def execute(self, sql: str, params: Iterable[Any] = ()):
        return self.connection.execute(sql.replace("?", "%s") if self.is_postgres else sql, tuple(params))

    def initialize(self) -> None:
        identity = "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self.execute(f"""CREATE TABLE IF NOT EXISTS snapshots (
            id {identity}, event_key TEXT NOT NULL, observed_at_utc TEXT NOT NULL, sport TEXT NOT NULL,
            matchup TEXT NOT NULL, event_start_utc TEXT, market TEXT NOT NULL, selection TEXT NOT NULL,
            selection_side TEXT, split_line TEXT, bets_pct REAL, money_pct REAL, money_minus_bets_gap REAL,
            best_line TEXT, best_price INTEGER, break_even_pct REAL, data_quality TEXT,
            UNIQUE(event_key, observed_at_utc))""")
        self.execute(f"""CREATE TABLE IF NOT EXISTS results (
            id {identity}, event_key TEXT NOT NULL, sport TEXT NOT NULL, matchup TEXT NOT NULL,
            market TEXT NOT NULL, selection TEXT NOT NULL, away_score INTEGER, home_score INTEGER,
            bet_result TEXT NOT NULL CHECK(bet_result IN ('win','loss','push')),
            settled_at_utc TEXT NOT NULL, UNIQUE(event_key))""")
        self.connection.commit()

    def insert_snapshot(self, snapshot: dict[str, Any]) -> bool:
        observed = snapshot.get("observed_at_utc") or utc_now()
        key = snapshot.get("event_key") or event_key(snapshot["sport"], snapshot["matchup"], snapshot.get("event_start_utc"), snapshot["market"], snapshot["selection"])
        fields = ["event_key", "observed_at_utc", "sport", "matchup", "event_start_utc", "market", "selection", "selection_side", "split_line", "bets_pct", "money_pct", "money_minus_bets_gap", "best_line", "best_price", "break_even_pct", "data_quality"]
        values = [key, observed] + [snapshot.get(field) for field in fields[2:]]
        clause = "ON CONFLICT(event_key, observed_at_utc) DO NOTHING"
        cursor = self.execute(f"INSERT INTO snapshots ({','.join(fields)}) VALUES ({','.join([self.placeholder] * len(fields))}) {clause}", values)
        self.connection.commit()
        return cursor.rowcount > 0

    def insert_snapshots(self, snapshots: Iterable[dict[str, Any]]) -> int:
        return sum(self.insert_snapshot(snapshot) for snapshot in snapshots)

    def record_result(self, snapshot: dict[str, Any], away_score: int, home_score: int, side: str, settled_at_utc: Optional[str] = None) -> str:
        result = settle_selection(snapshot["market"], side, line_value(snapshot.get("best_line")), away_score, home_score)
        key = snapshot.get("event_key") or event_key(snapshot["sport"], snapshot["matchup"], snapshot.get("event_start_utc"), snapshot["market"], snapshot["selection"])
        fields = (key, snapshot["sport"], snapshot["matchup"], snapshot["market"], snapshot["selection"], away_score, home_score, result, settled_at_utc or utc_now())
        self.execute(f"INSERT INTO results (event_key,sport,matchup,market,selection,away_score,home_score,bet_result,settled_at_utc) VALUES ({','.join([self.placeholder] * 9)}) ON CONFLICT(event_key) DO UPDATE SET away_score=excluded.away_score,home_score=excluded.home_score,bet_result=excluded.bet_result,settled_at_utc=excluded.settled_at_utc", fields)
        self.connection.commit()
        return result

    def snapshots(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.execute("SELECT * FROM snapshots ORDER BY observed_at_utc").fetchall()]

    def settled_observations(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.execute("SELECT s.*, r.bet_result FROM snapshots s JOIN results r ON s.event_key=r.event_key ORDER BY s.observed_at_utc").fetchall()]

    def export_csv_rows(self, settled: bool = False) -> list[dict[str, Any]]:
        return self.settled_observations() if settled else self.snapshots()

    def analytics_rows(self) -> list[dict[str, Any]]:
        """Attach valid captured closing data to settled snapshots only."""
        rows = self.settled_observations()
        by_key: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_key.setdefault(row["event_key"], []).append(row)
        output = []
        for event_rows in by_key.values():
            closing = max(event_rows, key=lambda row: row["observed_at_utc"])
            for row in event_rows:
                item = dict(row)
                item["clv"] = calculate_clv(item["market"], item.get("selection_side") or "", line_value(item.get("best_line")), line_value(closing.get("best_line")), item.get("best_price"), closing.get("best_price")) if closing["observed_at_utc"] > item["observed_at_utc"] else None
                output.append(item)
        return output


def line_value(value: Optional[str]) -> Optional[float]:
    if not value or value == "N/A":
        return None
    normalized = str(value).upper().replace("PICK'EM", "0").replace("PICK", "0").replace("PK", "0")
    import re
    match = re.search(r"(?:[OU])?([+-]?\d+(?:\.\d+)?)", normalized)
    return float(match.group(1)) if match else None


def performance(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    observations = list(rows)
    wins = sum(row["bet_result"] == "win" for row in observations)
    losses = sum(row["bet_result"] == "loss" for row in observations)
    pushes = sum(row["bet_result"] == "push" for row in observations)
    units = sum(american_profit(int(row["best_price"])) if row["bet_result"] == "win" and row.get("best_price") is not None else -1 if row["bet_result"] == "loss" else 0 for row in observations)
    settled = wins + losses + pushes
    graded = wins + losses
    prices = [row["best_price"] for row in observations if row.get("best_price") is not None]
    breaks = [row["break_even_pct"] for row in observations if row.get("break_even_pct") is not None]
    clvs = [row["clv"] for row in observations if row.get("clv") is not None]
    return {"settled": settled, "wins": wins, "losses": losses, "pushes": pushes, "win_rate": wins / graded if graded else None, "units": units, "roi": units / settled if settled else None, "average_american_odds": sum(prices) / len(prices) if prices else None, "average_break_even_probability": sum(breaks) / len(breaks) if breaks else None, "average_clv": sum(clvs) / len(clvs) if clvs else None, "positive_clv_rate": sum(value > 0 for value in clvs) / len(clvs) if clvs else None}


def bucket_performance(rows: Iterable[dict[str, Any]], field: str = "money_minus_bets_gap") -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = gap_bucket(row.get(field)) if field == "money_minus_bets_gap" else str(row.get(field, "missing"))
        groups.setdefault(key, []).append(row)
    return {key: performance(value) for key, value in groups.items()}
