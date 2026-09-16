"""Deterministic provider and matching coverage for non-football settlement."""
from datetime import datetime, timezone

import pytest

import result_collector as rc
from test_result_collector import FakeStore, event, game, team


SPORT_PATHS = {
    "NFL": ("football", "nfl", ()),
    "NCAAF": ("football", "college-football", ("80", "81")),
    "NBA": ("basketball", "nba", ()),
    "NCAAB": ("basketball", "mens-college-basketball", ("50",)),
    "MLB": ("baseball", "mlb", ()),
    "NHL": ("hockey", "nhl", ()),
}


@pytest.mark.parametrize("sport,parts", SPORT_PATHS.items())
def test_provider_paths_and_group_configuration(sport, parts):
    family, league, groups = parts
    assert rc.SPORT_CONFIG[sport]["sport_path"] == family
    assert rc.SPORT_CONFIG[sport]["league_path"] == league
    assert tuple(rc.SPORT_CONFIG[sport]["groups"]) == groups
    url = rc.scoreboard_url(sport, "20251125", groups[0] if groups else None)
    assert f"/sports/{family}/{league}/scoreboard?dates=20251125&limit=500" in url
    if groups:
        assert f"groups={groups[0]}" in url


def test_college_boards_request_all_required_groups(monkeypatch):
    urls = []
    monkeypatch.setattr(rc, "fetch_json", lambda url: urls.append(url) or {"events": []})
    rc.fetch_events_for_date("NCAAF", "20251122")
    rc.fetch_events_for_date("NCAAB", "20251125")
    assert {"groups=80", "groups=81"}.issubset({part for url in urls for part in url.split("&")})
    assert any("mens-college-basketball" in url and "groups=50" in url for url in urls)


@pytest.mark.parametrize("sport", tuple(SPORT_PATHS))
def test_every_supported_sport_parses_explicit_final_and_nonfinal(sport):
    final = rc.parse_event(event(event_id=f"{sport}-final", scores=("112", "107")), sport)
    assert final["sport"] == sport and final["status"] == "final"
    nonfinal = rc.parse_event(event(event_id=f"{sport}-pre", status={"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}}, scores=(None, None)), sport)
    assert nonfinal["status"] == "scheduled" and nonfinal["away_score"] is None


@pytest.mark.parametrize("name", ["STATUS_FINAL", "STATUS_FINAL_OT", "STATUS_FINAL_2OT", "STATUS_FINAL_SO", "STATUS_FINAL_10"])
def test_completed_overtime_shootout_and_extra_inning_statuses_are_final(name):
    assert rc.parse_status({"type": {"name": name, "state": "post", "completed": True, "description": "Final/OT"}}) == "final"


@pytest.mark.parametrize("sport,abbr,display", [
    ("NBA", "NY", "New York Knicks"),
    ("NCAAB", "M-OH", "Miami (OH) RedHawks"),
    ("MLB", "SD", "San Diego Padres"),
    ("NHL", "VGK", "Vegas Golden Knights"),
])
def test_new_sport_identity_is_exact_and_sport_scoped(sport, abbr, display):
    keys = rc.provider_team_keys(sport, team(abbr, display))
    assert rc.normalize_team(sport, abbr) in keys
    assert rc.normalize_team(sport, display) in keys
    # The NCAAF Miami alias must not leak to another sport.
    assert rc.normalize_team(sport, "Miami Hurricanes") != "miamifl"


@pytest.mark.parametrize("sport", ("NBA", "NCAAB", "MLB", "NHL"))
def test_new_sport_matching_rejects_wrong_and_reversed_teams(sport):
    provider = rc.parse_event(event(away="AAA", home="BBB"), sport)
    base = game(sport=sport, away_team="AAA", home_team="BBB")
    assert rc.match_stored_game(base, [provider])["status"] == "matched"
    assert rc.match_stored_game(dict(base, away_team="BBB", home_team="AAA"), [provider])["status"] == "unmatched"
    assert rc.match_stored_game(dict(base, away_team="CCC"), [provider])["status"] == "unmatched"


def _mlb_event(event_id, hour):
    return rc.parse_event(event(event_id=event_id, away="SD", home="LAD", date=f"2025-09-28T{hour:02d}:00:00Z", scores=("5", "3")), "MLB")


def test_mlb_doubleheader_uses_unique_nearest_kickoff_not_provider_order():
    first, second = _mlb_event("first", 12), _mlb_event("second", 18)
    first_game = game(sport="MLB", away_team="SD", home_team="LAD", event_start_utc="2025-09-28T12:20:00Z")
    second_game = dict(first_game, event_start_utc="2025-09-28T17:40:00Z")
    assert rc.match_stored_game(first_game, [second, first])["event"]["external_event_id"] == "first"
    assert rc.match_stored_game(second_game, [first, second])["event"]["external_event_id"] == "second"


def test_mlb_doubleheader_tied_nearest_is_ambiguous_and_existing_event_id_wins():
    first, second = _mlb_event("first", 12), _mlb_event("second", 18)
    tied = game(sport="MLB", away_team="SD", home_team="LAD", event_start_utc="2025-09-28T15:00:00Z")
    assert rc.match_stored_game(tied, [second, first])["status"] == "ambiguous"
    assert rc.match_stored_game(dict(tied, external_event_id="second"), [first, second])["event"]["external_event_id"] == "second"


def test_nonfootball_correction_and_collector_write_are_preserved():
    stored = game(sport="NBA", away_score=100, home_score=99, external_event_id="nba-final")
    store = FakeStore(recent=[stored])
    provider = rc.parse_event(event(event_id="nba-final", scores=("101", "99")), "NBA")
    summary = rc.collect_results(store, lambda sport, date: [provider], datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert len(store.writes) == 1 and summary["corrected_results_updated"] == 1
    assert store.writes[0][1]["result_source"] == "espn_scoreboard"

