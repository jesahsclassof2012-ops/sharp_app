"""Safe final-only ESPN Core result ingestion (no credentials required)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
import requests
from history_store import HistoryStore

BASE="https://sports.core.api.espn.com/v2/sports/football/leagues"
LEAGUES={"NFL":"nfl","NCAAF":"college-football"}
FINAL={"final","completed"}
TOLERANCE_SECONDS=12*3600
ALIASES={"nfl":{"la":"lar","l.a.rams":"lar"},"ncaaf":{"miami":"mia"}}

def normalize_team(value):
    key="".join(c for c in str(value).lower() if c.isalnum()); return ALIASES.get("nfl",{}).get(key,ALIASES.get("ncaaf",{}).get(key,key))

def fetch_json(url):
    response=requests.get(url,timeout=30,headers={"User-Agent":"SharpSignal/2.0"}); response.raise_for_status()
    payload=response.json()
    if not isinstance(payload,dict): raise ValueError("ESPN payload is not an object")
    return payload

def parse_event(payload,sport):
    comp=(payload.get("competitions") or [{}])[0]; status=comp.get("status",{}).get("type",{}).get("name","").lower()
    if status not in FINAL: return None
    teams={}
    for item in comp.get("competitors",[]):
        score=item.get("score",{}).get("value")
        if item.get("homeAway") not in {"away","home"} or not isinstance(score,int): raise ValueError("final event missing valid scores")
        teams[item["homeAway"]]=(item.get("team",{}).get("displayName") or item.get("team",{}).get("shortDisplayName"),score)
    if set(teams)!={"away","home"}: raise ValueError("final event missing competitors")
    return {"sport":sport,"external_event_id":str(payload.get("id")),"event_start_utc":payload.get("date"),"away":teams["away"][0],"home":teams["home"][0],"away_score":teams["away"][1],"home_score":teams["home"][1],"source_status":status}

def match_game(game,event):
    if game["sport"]!=event["sport"] or not game.get("away_team") or not game.get("home_team"): return False
    if normalize_team(game["away_team"])!=normalize_team(event["away"]) or normalize_team(game["home_team"])!=normalize_team(event["home"]): return False
    a=datetime.fromisoformat(game["event_start_utc"].replace("Z","+00:00")); b=datetime.fromisoformat(event["event_start_utc"].replace("Z","+00:00")); return abs((a-b).total_seconds())<=TOLERANCE_SECONDS

def main():
    store=HistoryStore(production=True); games=store.unresolved_games(); print(f"Checked {len(games)} unresolved games.")
    # Event-list discovery is deliberately provider-specific and failures are fatal; never treat errors as zero games.
    raise SystemExit("ESPN Core event discovery must be configured with season/week references before production settlement.")
