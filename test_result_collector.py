import pytest
import result_collector as rc

def team(abbr, display=None): return {"id":abbr,"abbreviation":abbr,"displayName":display or abbr,"shortDisplayName":display or abbr}
def event(event_id="1", status=None, away="DEN", home="KC", date="2026-09-01T20:00:00Z", scores=("20","17")):
    return {"id":event_id,"date":date,"competitions":[{"status":status or {"type":{"name":"STATUS_FINAL","state":"post","completed":True,"description":"Final"}},"competitors":[{"homeAway":"away","team":team(away),"score":{"value":scores[0]}},{"homeAway":"home","team":team(home),"score":{"value":scores[1]}}]}]}
def parsed(**changes): return rc.parse_event(event(**changes),"NFL")
def game(**changes):
    data={"sport":"NFL","away_team":"DEN","home_team":"KC","event_start_utc":"2026-09-01T20:00:00Z"}; data.update(changes); return data

def test_discovery_nfl_and_ncaaf_date_requests(monkeypatch):
    urls=[]; payload=event()
    def fake(url):
        urls.append(url)
        return {"items":[{"$ref":"https://event"}]} if "/events?" in url else payload
    monkeypatch.setattr(rc,"fetch_json",fake)
    assert rc.fetch_events_for_date("NFL","20260901")[0]["sport"]=="NFL"
    assert rc.fetch_events_for_date("NCAAF","20260901")[0]["sport"]=="NCAAF"
    assert any("/nfl/events?dates=20260901" in x for x in urls) and any("/college-football/events?dates=20260901" in x for x in urls)

def test_discovery_malformed_listing_fails_and_empty_slate_is_valid(monkeypatch):
    monkeypatch.setattr(rc,"fetch_json",lambda url: {"items":[]}); assert rc.fetch_events_for_date("NFL","20260901")==[]
    monkeypatch.setattr(rc,"fetch_json",lambda url: {}); 
    with pytest.raises(ValueError,match="items"): rc.fetch_events_for_date("NFL","20260901")

def test_http_failures_propagate(monkeypatch):
    class Response:
        def raise_for_status(self): raise RuntimeError("HTTP 500")
    monkeypatch.setattr(rc.requests,"get",lambda *args,**kwargs: Response())
    with pytest.raises(RuntimeError,match="HTTP 500"): rc.fetch_json("https://example.test")

@pytest.mark.parametrize("source,expected", [
    ({"name":"STATUS_SCHEDULED","state":"pre","completed":False},"scheduled"),
    ({"name":"STATUS_IN_PROGRESS","state":"in","completed":False},"in_progress"),
    ({"name":"STATUS_HALFTIME","state":"in","completed":False,"description":"Halftime"},"in_progress"),
    ({"name":"STATUS_FINAL","state":"post","completed":True},"final"),
    ({"name":"STATUS_POSTPONED","state":"post","completed":False},"postponed"),
    ({"name":"STATUS_CANCELED","state":"post","completed":False},"cancelled"),
    ({"name":"STATUS_SUSPENDED","state":"in","completed":False},"suspended"),
    ({"name":"ODD","state":"mystery","completed":False},"unknown"),
])
def test_explicit_status_categories(source,expected): assert rc.parse_status({"type":source})==expected

def test_nonfinal_events_remain_parseable():
    parsed_event=rc.parse_event(event(status={"type":{"name":"STATUS_SCHEDULED","state":"pre","completed":False}},scores=(None,None)),"NFL")
    assert parsed_event["status"]=="scheduled" and parsed_event["away_score"] is None

@pytest.mark.parametrize("score", [20,20.0,"20"])
def test_final_integer_and_numeric_string_scores(score): assert parsed(scores=(score,score))["away_score"]==20
@pytest.mark.parametrize("score", [None,"", "20.5", -1, "-1", "x"])
def test_final_invalid_scores_are_rejected(score):
    with pytest.raises(ValueError,match="score"): parsed(scores=(score,"17"))

def test_team_normalization_is_sport_specific_and_miami_does_not_collide():
    assert rc.normalize_team("NFL","Los Angeles Rams")=="lar"
    assert rc.normalize_team("NFL"," L.A. Rams ")=="larams" # punctuation normalization, no unlisted alias
    assert rc.normalize_team("NCAAF","Miami (FL)")=="miamifl"
    assert rc.normalize_team("NCAAF","Miami (OH)")=="miamioh"
    assert rc.normalize_team("NCAAF","Miami") != rc.normalize_team("NCAAF","Miami (OH)")
    assert rc.normalize_team("NCAAF","Los Angeles Rams")=="losangelesrams"

def test_provider_keys_accept_abbreviation_and_safe_display_name():
    assert "den" in rc.provider_team_keys("NFL",team("DEN","Denver Broncos"))
    assert "miamifl" in rc.provider_team_keys("NCAAF",team("MIA","Miami Hurricanes"))
    assert "miamioh" in rc.provider_team_keys("NCAAF",team("M-OH","Miami (OH) RedHawks"))

@pytest.mark.parametrize("hours,status", [(0,"matched"),(1,"matched"),(12,"matched"),(12.01,"unmatched")])
def test_matching_kickoff_tolerance(hours,status):
    provider=parsed(date="2026-09-01T20:00:00Z")
    start=f"2026-09-{'02' if hours>=4 else '01'}T{'08' if hours==12 else '21'}:00:00Z" if hours else "2026-09-01T20:00:00Z"
    if hours==1: start="2026-09-01T21:00:00Z"
    if hours==12.01: start="2026-09-02T08:01:00Z"
    assert rc.match_stored_game(game(event_start_utc=start),[provider])["status"]==status

def test_matching_rejects_reversed_and_handles_cardinality_and_identity():
    provider=parsed()
    assert rc.match_stored_game(game(away_team="KC",home_team="DEN"),[provider])["status"]=="unmatched"
    assert rc.match_stored_game(game(),[])["status"]=="unmatched"
    assert rc.match_stored_game(game(),[provider,dict(provider)])["status"]=="ambiguous"
    assert rc.match_stored_game(game(away_team=None),[provider])["status"]=="incomplete_identity"

def test_existing_external_id_is_preferred_and_never_silently_replaced():
    provider=parsed(event_id="correct"); other=parsed(event_id="other")
    assert rc.match_stored_game(game(external_event_id="correct"),[other,provider])["event"]["external_event_id"]=="correct"
    assert rc.match_stored_game(game(external_event_id="correct"),[other])["status"]=="unmatched"

def test_date_planning_includes_adjacent_dates_and_deduplicates():
    dates=rc.provider_dates_for_games([game(),game(event_start_utc="2026-09-01T23:59:00+00:00"),game(event_start_utc="2026-09-02T00:01:00Z")])
    assert dates==["20260831","20260901","20260902","20260903"]

def test_invalid_provider_kickoff_is_rejected():
    provider=parsed(); provider["event_start_utc"]="not-a-time"
    assert rc.match_stored_game(game(),[provider])["status"]=="unmatched"
