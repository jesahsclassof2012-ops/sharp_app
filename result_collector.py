"""Read-only ESPN Core discovery, parsing, and deterministic game matching."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable
import requests
from history_store import normalize_utc

CORE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues"
LEAGUES = {"NFL": "nfl", "NCAAF": "college-football"}
TOLERANCE_SECONDS = 12 * 60 * 60
HTTP_HEADERS = {"User-Agent": "SharpSignal/2.0"}
# Sport-scoped whole-team aliases only. Never map generic "miami".
ALIASES = {
    "NFL": {"arizonacardinals":"ari","atlantafalcons":"atl","baltimoreravens":"bal","buffalobills":"buf","carolinapanthers":"car","chicagobears":"chi","cincinnatibengals":"cin","clevelandbrowns":"cle","dallascowboys":"dal","denverbroncos":"den","detroitlions":"det","greenbaypackers":"gb","houstontexans":"hou","indianapoliscolts":"ind","jacksonvillejaguars":"jax","kansascitychiefs":"kc","lasvegasraiders":"lv","losangeleschargers":"lac","losangelesrams":"lar","miamidolphins":"mia","minnesotavikings":"min","newenglandpatriots":"ne","neworleanssaints":"no","newyorkgiants":"nyg","newyorkjets":"nyj","philadelphiaeagles":"phi","pittsburghsteelers":"pit","sanfrancisco49ers":"sf","seattleseahawks":"sea","tampabaybuccaneers":"tb","tennesseetitans":"ten","washingtoncommanders":"was"},
    "NCAAF": {"miamifl":"miamifl","miamihurricanes":"miamifl","miamioh":"miamioh","miamiohredhawks":"miamioh"},
}

def _identifier(value: Any) -> str: return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())
def normalize_team(sport: str, value: Any) -> str:
    if sport not in LEAGUES: raise ValueError(f"Unsupported sport: {sport}")
    key = _identifier(value); return ALIASES[sport].get(key, key)
def _ref(value: Any) -> str | None: return value.get("$ref") if isinstance(value, dict) else None

def fetch_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30, headers=HTTP_HEADERS); response.raise_for_status(); payload = response.json()
    if not isinstance(payload, dict): raise ValueError("ESPN payload is not an object")
    return payload

def _resolved(value: Any) -> dict[str, Any]:
    url = _ref(value)
    return fetch_json(url.replace("http:", "https:", 1)) if url else (value if isinstance(value, dict) else {})

def parse_status(status: Any) -> str:
    status = _resolved(status); kind = status.get("type") if isinstance(status.get("type"), dict) else {}
    text = " ".join(str(kind.get(key, "")) for key in ("name","state","description","detail")).lower()
    if any(word in text for word in ("postponed","postpone")): return "postponed"
    if any(word in text for word in ("cancelled","canceled","cancel")): return "cancelled"
    if "suspend" in text: return "suspended"
    if kind.get("completed") is True and ("final" in text or kind.get("state") == "post"): return "final"
    if "halftime" in text or kind.get("state") in {"in","in_progress"} or "live" in text: return "in_progress"
    if kind.get("state") in {"pre","scheduled"} or any(word in text for word in ("scheduled","preview")): return "scheduled"
    return "unknown"

def _score(value: Any) -> int:
    value = _resolved(value) if isinstance(value,dict) else value; raw = value.get("value") if isinstance(value, dict) else value
    if isinstance(raw,bool): raise ValueError("final event has an invalid score")
    if isinstance(raw,float):
        if not raw.is_integer() or raw < 0: raise ValueError("final event has an invalid score")
        return int(raw)
    if not isinstance(raw,(int,str)) or not re.fullmatch(r"\d+",str(raw).strip()): raise ValueError("final event has an invalid score")
    return int(raw)

def _team_payload(value: Any) -> dict[str, Any]: return _resolved(value)
def provider_team_keys(sport: str, team: dict[str, Any]) -> set[str]:
    """Use explicit identity fields; deliberately exclude mascot-only `name`."""
    return {normalize_team(sport, team[field]) for field in ("id","abbreviation","displayName","shortDisplayName","location") if team.get(field) not in (None, "")}

def _parse_competitor(item: Any, sport: str, final: bool) -> tuple[str, dict[str,Any], int | None]:
    competitor = _resolved(item); side = competitor.get("homeAway")
    if side not in {"away","home"}: raise ValueError("event competitor is missing homeAway")
    team = _team_payload(competitor.get("team"))
    if not provider_team_keys(sport,team): raise ValueError("event competitor is missing team identity")
    return side, team, _score(competitor.get("score")) if final else None

def parse_event(payload: dict[str,Any], sport: str) -> dict[str,Any]:
    if sport not in LEAGUES or not isinstance(payload,dict): raise ValueError("invalid ESPN event")
    if payload.get("id") in (None,"") or not payload.get("date"): raise ValueError("event is missing id or kickoff")
    start = normalize_utc(payload["date"]); competitions = payload.get("competitions")
    if not isinstance(competitions,list) or len(competitions)!=1: raise ValueError("event must have one competition")
    competition = _resolved(competitions[0]); category = parse_status(competition.get("status")); competitors = competition.get("competitors")
    if not isinstance(competitors,list): raise ValueError("event is missing competitors")
    teams, scores = {}, {}
    for raw in competitors:
        side, team, score = _parse_competitor(raw,sport,category=="final")
        if side in teams: raise ValueError(f"event has duplicate {side} competitor")
        teams[side]=team
        if score is not None: scores[side]=score
    if set(teams)!={"away","home"}: raise ValueError("event must have exactly one away and one home competitor")
    if category=="final" and set(scores)!={"away","home"}: raise ValueError("final event is missing scores")
    return {"sport":sport,"external_event_id":str(payload["id"]),"event_start_utc":start,"status":category,"source_status":category,"away":teams["away"],"home":teams["home"],"away_score":scores.get("away"),"home_score":scores.get("home")}

def fetch_events_for_date(sport: str, date: Any) -> list[dict[str,Any]]:
    if sport not in LEAGUES: raise ValueError(f"Unsupported sport: {sport}")
    if isinstance(date,datetime): date=date.astimezone(timezone.utc).strftime("%Y%m%d")
    elif hasattr(date,"strftime"): date=date.strftime("%Y%m%d")
    elif not re.fullmatch(r"\d{8}",str(date)): raise ValueError("date must be YYYYMMDD")
    listing=fetch_json(f"{CORE_BASE}/{LEAGUES[sport]}/events?dates={date}&limit=500"); items=listing.get("items")
    if not isinstance(items,list): raise ValueError("ESPN event listing is missing items")
    if any(not _ref(item) for item in items): raise ValueError("ESPN event listing has malformed item")
    return [parse_event(_resolved(item),sport) for item in items]

def provider_dates_for_games(games: Iterable[dict[str,Any]]) -> list[str]:
    dates=set()
    for game in games:
        start=datetime.fromisoformat(normalize_utc(game["event_start_utc"]).replace("Z","+00:00")); dates.update((start+timedelta(days=offset)).strftime("%Y%m%d") for offset in (-1,0,1))
    return sorted(dates)
def _kickoff(value: Any) -> datetime: return datetime.fromisoformat(normalize_utc(value).replace("Z","+00:00"))

def match_stored_game(game: dict[str,Any], provider_events: Iterable[dict[str,Any]]) -> dict[str,Any]:
    if not game.get("away_team") or not game.get("home_team"): return {"status":"incomplete_identity","event":None}
    try: start=_kickoff(game["event_start_utc"])
    except (TypeError,ValueError): return {"status":"unmatched","event":None}
    existing=str(game["external_event_id"]) if game.get("external_event_id") not in (None,"") else None; candidates=[]
    for event in provider_events:
        try:
            if event.get("sport")!=game.get("sport") or (existing and str(event.get("external_event_id"))!=existing): continue
            if normalize_team(game["sport"],game["away_team"]) not in provider_team_keys(game["sport"],event["away"]): continue
            if normalize_team(game["sport"],game["home_team"]) not in provider_team_keys(game["sport"],event["home"]): continue
            if abs((_kickoff(event["event_start_utc"])-start).total_seconds())<=TOLERANCE_SECONDS: candidates.append(event)
        except (KeyError,TypeError,ValueError): continue
    return {"status":"matched","event":candidates[0]} if len(candidates)==1 else {"status":"ambiguous" if len(candidates)>1 else "unmatched","event":None}

def main() -> None:
    # Step 2 intentionally performs no production writes or discovery orchestration.
    print("Result ingestion is not enabled in Step 2.")
