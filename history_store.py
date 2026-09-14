"""Leakage-safe persistent storage and baseline analytics for Sharp Signal."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def normalize_utc(value: Any) -> str:
    if not value:
        raise ValueError("event_start_utc is required")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("event_start_utc must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(part.strip().lower() for part in parts).encode()).hexdigest()


def game_key(sport: str, matchup: str, event_start_utc: Any) -> str:
    return _key(str(sport), str(matchup), normalize_utc(event_start_utc))


def signal_key(game: str, market: str, selection: str) -> str:
    return _key(game, str(market), str(selection))


def event_key(sport: str, matchup: str, event_start_utc: Any, market: str, selection: str) -> str:
    """Backward-compatible alias for the deterministic signal identity."""
    return signal_key(game_key(sport, matchup, event_start_utc), market, selection)


def line_value(value: Optional[str]) -> Optional[float]:
    if not value or value == "N/A":
        return None
    text = str(value).upper().replace("PICK'EM", "0").replace("PICK", "0").replace("PK", "0")
    found = re.search(r"(?:[OU])?([+-]?\d+(?:\.\d+)?)", text)
    return float(found.group(1)) if found else None


def implied_probability(odds: Optional[int]) -> Optional[float]:
    return None if odds in (None, 0) else 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)


def american_profit(odds: int, stake: float = 1.0) -> float:
    return stake * odds / 100 if odds > 0 else stake * 100 / abs(odds)


def settle_selection(market: str, side: str, line: Optional[float], away_score: int, home_score: int) -> str:
    side = side.lower()
    if market == "Moneyline": value = away_score - home_score if side == "away" else home_score - away_score
    elif market == "Spread":
        if line is None: raise ValueError("Spread settlement requires captured line")
        value = away_score - home_score + line if side == "away" else home_score - away_score + line
    elif market == "Total":
        if line is None: raise ValueError("Total settlement requires captured line")
        value = away_score + home_score - line if side == "over" else line - away_score - home_score
    else: raise ValueError(f"Unsupported market: {market}")
    return "win" if value > 0 else "loss" if value < 0 else "push"


def calculate_clv(market: str, side: str, entry_line: Optional[float], closing_line: Optional[float], entry_price: Optional[int], closing_price: Optional[int]) -> Optional[float]:
    """Positive means entry was better for the bettor than the pregame close."""
    if market == "Moneyline":
        entry, close = implied_probability(entry_price), implied_probability(closing_price)
        return None if entry is None or close is None else close - entry
    if entry_line is None or closing_line is None: return None
    if market == "Spread": return entry_line - closing_line
    if market == "Total" and side.lower() == "over": return closing_line - entry_line
    if market == "Total" and side.lower() == "under": return entry_line - closing_line
    return None


def gap_bucket(value: Optional[float]) -> str:
    if value is None: return "missing"
    for low, high, label in ((0,5,"0-5"),(5,10,"5-10"),(10,15,"10-15"),(15,20,"15-20"),(20,30,"20-30")):
        if low <= value < high: return label
    return "30+" if value >= 30 else "negative"


def ticket_bucket(value: Optional[float]) -> str:
    if value is None: return "missing"
    return "0-25" if value < 25 else "25-50" if value < 50 else "50-75" if value < 75 else "75-100"


def price_bucket(value: Optional[int]) -> str:
    if value is None: return "missing"
    return "plus-money" if value > 0 else "-110 to -100" if value >= -110 else "-150 to -111" if value >= -150 else "-151 or shorter"


def is_valid_pregame(row: dict[str, Any], at: Optional[datetime] = None) -> bool:
    try:
        observed = datetime.fromisoformat(str(row["observed_at_utc"]).replace("Z", "+00:00"))
        start = datetime.fromisoformat(normalize_utc(row["event_start_utc"]).replace("Z", "+00:00"))
        return observed < start and (at is None or observed <= at)
    except (KeyError, ValueError, TypeError): return False


def baseline_eligible(row: dict[str, Any]) -> bool:
    if not is_valid_pregame(row) or row.get("data_quality") != "OK" or row.get("best_price") is None: return False
    return row.get("market") == "Moneyline" or line_value(row.get("best_line")) is not None


class HistoryStore:
    def __init__(self, database_url: Optional[str] = None, production: bool = False):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.is_postgres = bool(self.database_url and self.database_url.startswith(("postgres://", "postgresql://")))
        if production and not self.database_url: raise RuntimeError("DATABASE_URL is required for production history")
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc: raise RuntimeError("DATABASE_URL requires psycopg") from exc
            self.connection = psycopg.connect(self.database_url, row_factory=dict_row)
        else:
            path = self.database_url.removeprefix("sqlite:///") if self.database_url and self.database_url.startswith("sqlite:///") else "sharp_signal_history.db"
            if path != ":memory:": Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path); self.connection.row_factory = sqlite3.Row
        self.initialize()

    @property
    def p(self) -> str: return "%s" if self.is_postgres else "?"
    def execute(self, sql: str, values: Iterable[Any] = ()): return self.connection.execute(sql.replace("?", "%s") if self.is_postgres else sql, tuple(values))
    def rows(self, cursor) -> list[dict[str, Any]]: return [dict(row) for row in cursor.fetchall()]

    def initialize(self) -> None:
        ident = "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self.execute(f"CREATE TABLE IF NOT EXISTS snapshots (id {ident}, game_key TEXT NOT NULL, signal_key TEXT NOT NULL, observed_at_utc TEXT NOT NULL, sport TEXT NOT NULL, matchup TEXT NOT NULL, event_start_utc TEXT NOT NULL, market TEXT NOT NULL, selection TEXT NOT NULL, selection_side TEXT, split_line TEXT, bets_pct REAL, money_pct REAL, money_minus_bets_gap REAL, best_line TEXT, best_price INTEGER, break_even_pct REAL, data_quality TEXT, line_vs_split TEXT, UNIQUE(signal_key,observed_at_utc))")
        self.execute(f"CREATE TABLE IF NOT EXISTS results (id {ident}, game_key TEXT NOT NULL UNIQUE, sport TEXT NOT NULL, matchup TEXT NOT NULL, event_start_utc TEXT NOT NULL, away_score INTEGER NOT NULL, home_score INTEGER NOT NULL, settled_at_utc TEXT NOT NULL)")
        self.connection.commit()

    def insert_snapshot(self, item: dict[str, Any]) -> bool:
        if not is_valid_pregame(item): return False
        start = normalize_utc(item["event_start_utc"]); game = game_key(item["sport"], item["matchup"], start); signal = signal_key(game,item["market"],item["selection"])
        fields = "game_key,signal_key,observed_at_utc,sport,matchup,event_start_utc,market,selection,selection_side,split_line,bets_pct,money_pct,money_minus_bets_gap,best_line,best_price,break_even_pct,data_quality,line_vs_split".split(",")
        values = [game,signal,item["observed_at_utc"],item["sport"],item["matchup"],start] + [item.get(name) for name in fields[6:]]
        cur=self.execute(f"INSERT INTO snapshots ({','.join(fields)}) VALUES ({','.join([self.p]*len(fields))}) ON CONFLICT(signal_key,observed_at_utc) DO NOTHING",values); self.connection.commit(); return cur.rowcount > 0
    def insert_snapshots(self, items: Iterable[dict[str, Any]]) -> int: return sum(self.insert_snapshot(item) for item in items)
    def record_game_result(self, sport:str, matchup:str, event_start_utc:Any, away_score:int, home_score:int, settled_at_utc:Optional[str]=None) -> None:
        start=normalize_utc(event_start_utc); game=game_key(sport,matchup,start)
        self.execute(f"INSERT INTO results (game_key,sport,matchup,event_start_utc,away_score,home_score,settled_at_utc) VALUES ({','.join([self.p]*7)}) ON CONFLICT(game_key) DO UPDATE SET away_score=excluded.away_score,home_score=excluded.home_score,settled_at_utc=excluded.settled_at_utc",(game,sport,matchup,start,away_score,home_score,settled_at_utc or datetime.now(timezone.utc).isoformat())); self.connection.commit()
    def snapshots(self) -> list[dict[str,Any]]: return self.rows(self.execute("SELECT * FROM snapshots ORDER BY observed_at_utc"))
    def results(self) -> list[dict[str,Any]]: return self.rows(self.execute("SELECT * FROM results"))
    def signal_snapshots(self, signal:str) -> list[dict[str,Any]]: return [x for x in self.snapshots() if x["signal_key"]==signal and is_valid_pregame(x)]
    def first_current_close(self, signal:str, at:Optional[datetime]=None) -> dict[str,Optional[dict[str,Any]]]:
        rows=self.signal_snapshots(signal); current=[x for x in rows if is_valid_pregame(x,at)]
        return {"first": min(rows,key=lambda x:x["observed_at_utc"]) if rows else None,"current":max(current,key=lambda x:x["observed_at_utc"]) if current else None,"close":max(rows,key=lambda x:x["observed_at_utc"]) if rows else None}
    def baseline_entries(self) -> list[dict[str,Any]]:
        groups={}
        for row in self.snapshots():
            if baseline_eligible(row): groups.setdefault(row["signal_key"],[]).append(row)
        return [min(rows,key=lambda x:x["observed_at_utc"]) for rows in groups.values()]
    def analytics_rows(self) -> list[dict[str,Any]]:
        scores={row["game_key"]:row for row in self.results()}; output=[]
        for entry in self.baseline_entries():
            result=scores.get(entry["game_key"])
            if not result: continue
            close=self.first_current_close(entry["signal_key"])["close"]
            row=dict(entry); row["bet_result"]=settle_selection(row["market"],row.get("selection_side") or "",line_value(row.get("best_line")),result["away_score"],result["home_score"]); row["clv"]=calculate_clv(row["market"],row.get("selection_side") or "",line_value(row.get("best_line")),line_value(close.get("best_line")) if close else None,row.get("best_price"),close.get("best_price") if close else None); output.append(row)
        return output


def performance(rows: Iterable[dict[str,Any]]) -> dict[str,Any]:
    data=[row for row in rows if row.get("best_price") is not None]; wins=sum(x["bet_result"]=="win" for x in data); losses=sum(x["bet_result"]=="loss" for x in data); pushes=sum(x["bet_result"]=="push" for x in data); units=sum(american_profit(x["best_price"]) if x["bet_result"]=="win" else -1 if x["bet_result"]=="loss" else 0 for x in data); graded=wins+losses; clv=[x["clv"] for x in data if x.get("clv") is not None]; prices=[x["best_price"] for x in data]; bre=[x["break_even_pct"] for x in data if x.get("break_even_pct") is not None]
    return {"settled":len(data),"wins":wins,"losses":losses,"pushes":pushes,"win_rate":wins/graded if graded else None,"units":units,"roi":units/len(data) if data else None,"average_american_odds":sum(prices)/len(prices) if prices else None,"average_break_even_probability":sum(bre)/len(bre) if bre else None,"average_clv":sum(clv)/len(clv) if clv else None,"positive_clv_rate":sum(x>0 for x in clv)/len(clv) if clv else None}


def bucket_performance(rows: Iterable[dict[str,Any]], field:str="money_minus_bets_gap") -> dict[str,dict[str,Any]]:
    groups={}
    for row in rows:
        key=gap_bucket(row.get(field)) if field=="money_minus_bets_gap" else ticket_bucket(row.get(field)) if field=="bets_pct" else price_bucket(row.get(field)) if field=="best_price" else "bettor-favorable" if field=="line_vs_split" and str(row.get(field)).startswith("Better") else "not-bettor-favorable" if field=="line_vs_split" else str(row.get(field,"missing")); groups.setdefault(key,[]).append(row)
    return {key:performance(value) for key,value in groups.items()}
