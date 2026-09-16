import pytest
import result_collector as rc
from history_store import HistoryStore
import os
from pathlib import Path
import subprocess
import sys

def team(abbr, display=None): return {"id":abbr,"abbreviation":abbr,"displayName":display or abbr,"shortDisplayName":display or abbr}
def event(event_id="1", status=None, away="DEN", home="KC", date="2026-09-01T20:00:00Z", scores=("20","17")):
    return {"id":event_id,"date":date,"competitions":[{"status":status or {"type":{"name":"STATUS_FINAL","state":"post","completed":True,"description":"Final"}},"competitors":[{"homeAway":"away","team":team(away),"score":{"value":scores[0]}},{"homeAway":"home","team":team(home),"score":{"value":scores[1]}}]}]}
def parsed(**changes): return rc.parse_event(event(**changes),"NFL")
def game(**changes):
    data={"sport":"NFL","matchup":"DEN vs KC","away_team":"DEN","home_team":"KC","event_start_utc":"2026-09-01T20:00:00Z"}; data.update(changes); return data

def test_discovery_nfl_and_ncaaf_date_requests(monkeypatch):
    urls=[]; payload=event()
    def fake(url):
        urls.append(url)
        return {"events":[payload]}
    monkeypatch.setattr(rc,"fetch_json",fake)
    assert rc.fetch_events_for_date("NFL","20260901")[0]["sport"]=="NFL"
    assert rc.fetch_events_for_date("NCAAF","20260901")[0]["sport"]=="NCAAF"
    assert any("/nfl/scoreboard?dates=20260901" in x for x in urls)
    assert any("/college-football/scoreboard?dates=20260901" in x and "groups=80" in x for x in urls)
    assert any("/college-football/scoreboard?dates=20260901" in x and "groups=81" in x for x in urls)

def test_discovery_malformed_listing_fails_and_empty_slate_is_valid(monkeypatch):
    monkeypatch.setattr(rc,"fetch_json",lambda url: {"events":[]}); assert rc.fetch_events_for_date("NFL","20260901")==[]
    monkeypatch.setattr(rc,"fetch_json",lambda url: {})
    with pytest.raises(ValueError,match="events"): rc.fetch_events_for_date("NFL","20260901")

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

@pytest.mark.parametrize("sport", ["NFL", "NCAAF"])
def test_football_multiple_candidates_remain_ambiguous_regardless_of_order(sport):
    first=rc.parse_event(event(event_id="first",date="2026-09-01T19:00:00Z"),sport)
    second=rc.parse_event(event(event_id="second",date="2026-09-01T21:00:00Z"),sport)
    stored=game(sport=sport,event_start_utc="2026-09-01T20:00:00Z")
    assert rc.match_stored_game(stored,[first,second])["status"]=="ambiguous"
    assert rc.match_stored_game(stored,[second,first])["status"]=="ambiguous"
    assert rc.match_stored_game(dict(stored,external_event_id="second"),[first,second])["event"]["external_event_id"]=="second"

def test_date_planning_includes_adjacent_dates_and_deduplicates():
    dates=rc.provider_dates_for_games([game(),game(event_start_utc="2026-09-01T23:59:00+00:00"),game(event_start_utc="2026-09-02T00:01:00Z")])
    assert dates==["20260831","20260901","20260902","20260903"]

def test_invalid_provider_kickoff_is_rejected():
    provider=parsed(); provider["event_start_utc"]="not-a-time"
    assert rc.match_stored_game(game(),[provider])["status"]=="unmatched"

class FakeStore:
    def __init__(self, unresolved=(), recent=()): self.unresolved=list(unresolved); self.recent=list(recent); self.writes=[]
    def unresolved_games(self, now=None, sports=None, lookback_days=None): return self.unresolved
    def recently_settled_games(self, now=None, correction_hours=48, sports=None): assert correction_hours==48; return self.recent
    def record_game_result(self, *args, **kwargs): self.writes.append((args,kwargs))

def run(store, events):
    return rc.collect_results(store, lambda sport,date: events)

def test_collector_writes_only_matched_final_unresolved_game():
    store=FakeStore([game()]); summary=run(store,[parsed()])
    assert len(store.writes)==1 and summary["new_results_recorded"]==1 and summary["matched_finals"]==1
    assert store.writes[0][1]["result_source"]=="espn_scoreboard"

@pytest.mark.parametrize("status",["scheduled","in_progress","postponed","cancelled","suspended","unknown"])
def test_collector_never_writes_nonfinal_matches(status):
    store=FakeStore([game()]); provider=dict(parsed(),status=status,source_status=status,away_score=None,home_score=None)
    summary=run(store,[provider]); assert store.writes==[] and summary["not_final_skipped"]==1

def test_collector_skips_unmatched_ambiguous_and_incomplete_identity():
    unmatched=FakeStore([game(away_team="BUF")]); assert run(unmatched,[parsed()])["unmatched_skipped"]==1 and not unmatched.writes
    ambiguous=FakeStore([game()]); assert run(ambiguous,[parsed(),parsed(event_id="2")])["ambiguous_skipped"]==1 and not ambiguous.writes
    incomplete=FakeStore([game(away_team=None)]); assert run(incomplete,[parsed()])["incomplete_identity_skipped"]==1 and not incomplete.writes

def test_adjacent_date_duplicates_are_deduplicated_and_conflicts_fail():
    one=parsed(); store=FakeStore([game()]); summary=run(store,[one,dict(one)])
    assert summary["provider_events_fetched"]==1 and len(store.writes)==1
    conflict=dict(one,home_score=99)
    with pytest.raises(ValueError,match="conflicting duplicate"): rc.deduplicate_provider_events([one,conflict])

def test_recent_correction_updates_only_changed_final_and_keeps_external_id():
    recent_game=game(away_score=20,home_score=17,external_event_id="1")
    store=FakeStore(recent=[recent_game]); summary=run(store,[parsed(scores=(21,17))])
    assert len(store.writes)==1 and summary["corrected_results_updated"]==1
    same=FakeStore(recent=[dict(recent_game,away_score=21)]); summary=run(same,[parsed(scores=(21,17))])
    assert len(same.writes)==1 and summary["corrected_results_updated"]==0
    switched=FakeStore(recent=[recent_game]); assert run(switched,[parsed(event_id="different")])["unmatched_skipped"]==1 and not switched.writes

def test_collector_correction_keeps_one_row_and_original_settlement_time():
    from datetime import datetime, timezone
    store=HistoryStore("sqlite:///:memory:")
    base={"observed_at_utc":"2026-08-31T00:00:00Z","sport":"NFL","matchup":"DEN vs KC","event_start_utc":"2026-09-01T20:00:00Z","market":"Spread","split_line":"+3 / -3","bets_pct":40,"money_pct":55,"money_minus_bets_gap":15,"best_line":"+3","best_price":-110,"break_even_pct":52.3,"data_quality":"OK","line_vs_split":"Same"}
    store.insert_snapshots([dict(base,selection="DEN",selection_side="away"),dict(base,selection="KC",selection_side="home")])
    store.record_game_result("NFL","DEN vs KC",base["event_start_utc"],20,17,settled_at_utc="2026-09-01T12:00:00Z",external_event_id="1")
    before=store.results()[0]
    summary=rc.collect_results(store,lambda sport,date:[parsed(scores=(21,17))],datetime(2026,9,2,tzinfo=timezone.utc))
    after=store.results()[0]
    assert len(store.results())==1 and summary["corrected_results_updated"]==1
    assert after["away_score"]==21 and after["settled_at_utc"]==before["settled_at_utc"]

def test_outside_correction_window_is_not_rechecked():
    class WindowStore(FakeStore):
        def recently_settled_games(self, now=None, correction_hours=48, sports=None): return []
    store=WindowStore(); summary=run(store,[parsed()]); assert summary["recent_rechecked"]==0 and store.writes==[]

def test_provider_failures_and_malformed_events_fail_loudly():
    with pytest.raises(RuntimeError,match="provider down"): rc.collect_results(FakeStore([game()]),lambda sport,date: (_ for _ in ()).throw(RuntimeError("provider down")))
    with pytest.raises(KeyError): rc.collect_results(FakeStore([game()]),lambda sport,date: [{"broken":True}])

def test_run_level_ref_cache_avoids_duplicate_http_calls(monkeypatch):
    calls=[]; rc.clear_run_fetch_cache()
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"ok":True}
    monkeypatch.setattr(rc.requests,"get",lambda url,**kwargs: calls.append(url) or Response())
    assert rc.fetch_json("http://example.test/ref")==rc.fetch_json("https://example.test/ref")
    assert calls==["https://example.test/ref"]

def test_summary_has_no_credentials():
    text=rc.format_summary({"unresolved_checked":1,"recent_rechecked":2,"provider_events_fetched":3,"matched_finals":4,"new_results_recorded":5,"corrected_results_updated":6,"not_final_skipped":7,"unmatched_skipped":8,"ambiguous_skipped":9,"incomplete_identity_skipped":10})
    assert "DATABASE_URL" not in text and "password" not in text.lower()

def test_main_has_historystore_and_invokes_collector(monkeypatch, capsys):
    store=FakeStore(); calls=[]
    monkeypatch.setattr(rc,"HistoryStore",lambda production: calls.append(production) or store)
    monkeypatch.setattr(rc,"collect_results",lambda supplied,**kwargs: {"unresolved_checked":0,"recent_rechecked":0,"provider_events_fetched":0,"matched_finals":0,"new_results_recorded":0,"corrected_results_updated":0,"not_final_skipped":0,"unmatched_skipped":0,"ambiguous_skipped":0,"incomplete_identity_skipped":0} if supplied is store else None)
    rc.main([])
    assert calls==[True] and "Checked 0 unresolved games." in capsys.readouterr().out

def test_script_entrypoint_runs_and_missing_database_url_fails():
    environment=dict(os.environ); environment.pop("DATABASE_URL",None)
    script=Path(rc.__file__)
    result=subprocess.run([sys.executable,str(script)],cwd=script.parent,env=environment,capture_output=True,text=True,timeout=30)
    assert result.returncode != 0
    assert "DATABASE_URL" in (result.stdout+result.stderr)

def test_routine_collects_all_supported_sports_but_ignores_unsupported_sports():
    supported=list(rc.SUPPORTED_SPORTS)
    store=FakeStore([game(sport=sport) for sport in supported]+[game(sport="WNBA")]); requests=[]
    def fetch(sport,date):
        requests.append(sport)
        return [rc.parse_event(event(event_id=sport),sport)]
    summary=rc.collect_results(store,fetch)
    assert summary["unresolved_checked"]==len(supported) and summary["new_results_recorded"]==len(supported)
    assert set(requests)==set(supported) and summary["unmatched_skipped"]==0

def test_routine_lookback_and_backfill_cli_arguments():
    assert rc.parse_args([]).backfill_days is None
    assert rc.parse_args(["--backfill-days","120"]).backfill_days==120
    with pytest.raises(SystemExit): rc.parse_args(["--backfill-days","0"])
    with pytest.raises(SystemExit): rc.parse_args(["--backfill-days","not-a-number"])

def test_main_default_and_backfill_pass_explicit_safe_windows(monkeypatch):
    store=FakeStore(); calls=[]
    monkeypatch.setattr(rc,"HistoryStore",lambda production: store)
    monkeypatch.setattr(rc,"collect_results",lambda supplied,**kwargs: calls.append(kwargs["unresolved_lookback_days"]) or {"unresolved_checked":0,"recent_rechecked":0,"provider_events_fetched":0,"matched_finals":0,"new_results_recorded":0,"corrected_results_updated":0,"not_final_skipped":0,"unmatched_skipped":0,"ambiguous_skipped":0,"incomplete_identity_skipped":0})
    rc.main([]); rc.main(["--backfill-days","120"])
    assert calls==[14,120]


def assert_per_sport_totals(summary):
    for counter in rc.SUMMARY_COUNTERS:
        assert summary[counter] == sum(
            summary["by_sport"][sport][counter] for sport in rc.SUPPORTED_SPORTS
        )


def test_per_sport_summary_is_zero_filled_and_preserves_aggregate_format():
    summary=rc.collect_results(FakeStore(),lambda sport,date: [])
    assert list(summary["by_sport"]) == list(rc.SUPPORTED_SPORTS)
    assert all(
        counts == {counter: 0 for counter in rc.SUMMARY_COUNTERS}
        for counts in summary["by_sport"].values()
    )
    assert_per_sport_totals(summary)
    lines=rc.format_summary(summary).splitlines()
    assert lines[:10] == [
        "Checked 0 unresolved games.", "Rechecked 0 recent results.",
        "Fetched 0 ESPN events.", "Matched 0 final games.",
        "Recorded 0 new results.", "Updated 0 corrected results.",
        "Skipped 0 not final.", "Skipped 0 unmatched.",
        "Skipped 0 ambiguous.", "Skipped 0 incomplete identity.",
    ]
    assert lines[10] == "Per-sport settlement:"
    assert [line.split(":",1)[0] for line in lines[11:]] == list(rc.SUPPORTED_SPORTS)
    assert all(
        "unresolved=0 recent=0 events=0 finals=0 new=0 corrected=0 "
        "not_final=0 unmatched=0 ambiguous=0 incomplete_identity=0" in line
        for line in lines[11:]
    )


def test_per_sport_counters_track_existing_outcomes_independently():
    unresolved=[
        game(sport="NFL"),
        game(sport="NBA"),
        game(sport="NCAAB",away_team="BAD"),
        game(sport="NHL"),
        game(sport="MLB",away_team=None),
    ]
    recent=[game(sport="NCAAF",away_score=18,home_score=17)]
    events_by_sport={
        "NFL": [rc.parse_event(event(event_id="nfl",scores=(21,17)),"NFL")],
        "NCAAF": [rc.parse_event(event(event_id="ncaaf",scores=(20,17)),"NCAAF")],
        "NBA": [rc.parse_event(event(event_id="nba",status="scheduled",scores=(None,None)),"NBA")],
        "NCAAB": [rc.parse_event(event(event_id="ncaab",scores=(20,17)),"NCAAB")],
        "MLB": [],
        "NHL": [
            rc.parse_event(event(event_id="nhl-1",scores=(4,3)),"NHL"),
            rc.parse_event(event(event_id="nhl-2",scores=(4,3)),"NHL"),
        ],
    }
    summary=rc.collect_results(FakeStore(unresolved,recent),lambda sport,date: events_by_sport[sport])
    by_sport=summary["by_sport"]
    assert by_sport["NFL"] == {**{counter: 0 for counter in rc.SUMMARY_COUNTERS}, "unresolved_checked":1, "provider_events_fetched":1, "matched_finals":1, "new_results_recorded":1}
    assert by_sport["NCAAF"] == {**{counter: 0 for counter in rc.SUMMARY_COUNTERS}, "recent_rechecked":1, "provider_events_fetched":1, "matched_finals":1, "corrected_results_updated":1}
    assert by_sport["NBA"] == {**{counter: 0 for counter in rc.SUMMARY_COUNTERS}, "unresolved_checked":1, "provider_events_fetched":1, "not_final_skipped":1}
    assert by_sport["NCAAB"] == {**{counter: 0 for counter in rc.SUMMARY_COUNTERS}, "unresolved_checked":1, "provider_events_fetched":1, "unmatched_skipped":1}
    assert by_sport["MLB"] == {**{counter: 0 for counter in rc.SUMMARY_COUNTERS}, "unresolved_checked":1, "incomplete_identity_skipped":1}
    assert by_sport["NHL"] == {**{counter: 0 for counter in rc.SUMMARY_COUNTERS}, "unresolved_checked":1, "provider_events_fetched":2, "ambiguous_skipped":1}
    assert_per_sport_totals(summary)


def test_unchanged_recent_result_is_final_but_not_a_correction_per_sport():
    store=FakeStore(recent=[game(sport="MLB",away_score=5,home_score=3)])
    summary=rc.collect_results(
        store,
        lambda sport,date: [rc.parse_event(event(event_id="mlb",scores=(5,3)),"MLB")] if sport=="MLB" else [],
    )
    assert summary["by_sport"]["MLB"]["matched_finals"] == 1
    assert summary["by_sport"]["MLB"]["corrected_results_updated"] == 0
    assert_per_sport_totals(summary)


def test_provider_event_counts_are_sport_scoped_after_deduplication_and_unsupported_ignored():
    store=FakeStore([game(sport="NFL"),game(sport="MLB"),game(sport="WNBA")])
    def fetch(sport,date):
        if sport=="NFL":
            duplicate=rc.parse_event(event(event_id="same",scores=(21,17)),"NFL")
            return [duplicate,dict(duplicate)]
        if sport=="MLB":
            return [rc.parse_event(event(event_id="same",scores=(5,3)),"MLB")]
        return []
    summary=rc.collect_results(store,fetch)
    assert summary["by_sport"]["NFL"]["provider_events_fetched"] == 1
    assert summary["by_sport"]["MLB"]["provider_events_fetched"] == 1
    assert "WNBA" not in summary["by_sport"]
    assert_per_sport_totals(summary)
