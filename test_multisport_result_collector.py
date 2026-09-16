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

# Read-only audit fixtures: ScoresAndOdds code, ESPN abbreviation, ESPN
# displayName.  These are deliberately offline so a provider response change
# cannot hide an identity regression.
PROFESSIONAL_IDENTITIES = {
    "NBA": (
        ("ATL", "ATL", "Atlanta Hawks"), ("BOS", "BOS", "Boston Celtics"), ("BKN", "BKN", "Brooklyn Nets"), ("CHA", "CHA", "Charlotte Hornets"), ("CHI", "CHI", "Chicago Bulls"), ("CLE", "CLE", "Cleveland Cavaliers"), ("DAL", "DAL", "Dallas Mavericks"), ("DEN", "DEN", "Denver Nuggets"), ("DET", "DET", "Detroit Pistons"), ("GSW", "GS", "Golden State Warriors"), ("HOU", "HOU", "Houston Rockets"), ("IND", "IND", "Indiana Pacers"), ("LAC", "LAC", "LA Clippers"), ("LAL", "LAL", "Los Angeles Lakers"), ("MEM", "MEM", "Memphis Grizzlies"), ("MIA", "MIA", "Miami Heat"), ("MIL", "MIL", "Milwaukee Bucks"), ("MIN", "MIN", "Minnesota Timberwolves"), ("NOP", "NO", "New Orleans Pelicans"), ("NYK", "NY", "New York Knicks"), ("OKC", "OKC", "Oklahoma City Thunder"), ("ORL", "ORL", "Orlando Magic"), ("PHI", "PHI", "Philadelphia 76ers"), ("PHX", "PHX", "Phoenix Suns"), ("POR", "POR", "Portland Trail Blazers"), ("SAC", "SAC", "Sacramento Kings"), ("SAS", "SA", "San Antonio Spurs"), ("TOR", "TOR", "Toronto Raptors"), ("UTA", "UTAH", "Utah Jazz"), ("WAS", "WSH", "Washington Wizards"),
    ),
    "MLB": (
        ("ARI", "ARI", "Arizona Diamondbacks"), ("ATH", "ATH", "Athletics"), ("ATL", "ATL", "Atlanta Braves"), ("BAL", "BAL", "Baltimore Orioles"), ("BOS", "BOS", "Boston Red Sox"), ("CHC", "CHC", "Chicago Cubs"), ("CWS", "CHW", "Chicago White Sox"), ("CIN", "CIN", "Cincinnati Reds"), ("CLE", "CLE", "Cleveland Guardians"), ("COL", "COL", "Colorado Rockies"), ("DET", "DET", "Detroit Tigers"), ("HOU", "HOU", "Houston Astros"), ("KC", "KC", "Kansas City Royals"), ("LAA", "LAA", "Los Angeles Angels"), ("LAD", "LAD", "Los Angeles Dodgers"), ("MIA", "MIA", "Miami Marlins"), ("MIL", "MIL", "Milwaukee Brewers"), ("MIN", "MIN", "Minnesota Twins"), ("NYM", "NYM", "New York Mets"), ("NYY", "NYY", "New York Yankees"), ("PHI", "PHI", "Philadelphia Phillies"), ("PIT", "PIT", "Pittsburgh Pirates"), ("SD", "SD", "San Diego Padres"), ("SF", "SF", "San Francisco Giants"), ("SEA", "SEA", "Seattle Mariners"), ("STL", "STL", "St. Louis Cardinals"), ("TB", "TB", "Tampa Bay Rays"), ("TEX", "TEX", "Texas Rangers"), ("TOR", "TOR", "Toronto Blue Jays"), ("WSH", "WSH", "Washington Nationals"),
    ),
    "NHL": (
        ("ANA", "ANA", "Anaheim Ducks"), ("BOS", "BOS", "Boston Bruins"), ("BUF", "BUF", "Buffalo Sabres"), ("CGY", "CGY", "Calgary Flames"), ("CAR", "CAR", "Carolina Hurricanes"), ("CHI", "CHI", "Chicago Blackhawks"), ("COL", "COL", "Colorado Avalanche"), ("CBJ", "CBJ", "Columbus Blue Jackets"), ("DAL", "DAL", "Dallas Stars"), ("DET", "DET", "Detroit Red Wings"), ("EDM", "EDM", "Edmonton Oilers"), ("FLA", "FLA", "Florida Panthers"), ("LA", "LA", "Los Angeles Kings"), ("MIN", "MIN", "Minnesota Wild"), ("MTL", "MTL", "Montreal Canadiens"), ("NSH", "NSH", "Nashville Predators"), ("NJ", "NJ", "New Jersey Devils"), ("NYI", "NYI", "New York Islanders"), ("NYR", "NYR", "New York Rangers"), ("OTT", "OTT", "Ottawa Senators"), ("PHI", "PHI", "Philadelphia Flyers"), ("PIT", "PIT", "Pittsburgh Penguins"), ("SJ", "SJ", "San Jose Sharks"), ("SEA", "SEA", "Seattle Kraken"), ("STL", "STL", "St. Louis Blues"), ("TB", "TB", "Tampa Bay Lightning"), ("TOR", "TOR", "Toronto Maple Leafs"), ("UTA", "UTAH", "Utah Hockey Club"), ("VAN", "VAN", "Vancouver Canucks"), ("VGK", "VGK", "Vegas Golden Knights"), ("WSH", "WSH", "Washington Capitals"), ("WPG", "WPG", "Winnipeg Jets"),
    ),
}

NCAAB_AUDIT_MISMATCHES = (
    ("AC", "ACU", "Abilene Christian Wildcats"), ("CAMP", "CAM", "Campbell Fighting Camels"),
    ("CHS", "CHST", "Chicago State Cougars"), ("CSB", "CSUB", "Cal State Bakersfield Roadrunners"),
    ("IND", "IU", "Indiana Hoosiers"), ("L-IL", "LUC", "Loyola Chicago Ramblers"),
    ("MCNS", "MCN", "McNeese Cowboys"), ("MIZZ", "MIZ", "Missouri Tigers"),
    ("MTU", "MTSU", "Middle Tennessee Blue Raiders"), ("MURR", "MUR", "Murray State Racers"),
    ("NEOM", "OMA", "Omaha Mavericks"), ("PEAY", "APSU", "Austin Peay Governors"),
    ("SBON", "SBU", "St. Bonaventure Bonnies"), ("SCUS", "UPST", "South Carolina Upstate Spartans"),
    ("TXAM", "TA&M", "Texas A&M Aggies"),
)


@pytest.mark.parametrize("sport", ("NBA", "MLB", "NHL"))
def test_every_audited_professional_source_code_intersects_espn_identity(sport):
    fixtures = PROFESSIONAL_IDENTITIES[sport]
    expected_counts = {"NBA": 30, "MLB": 30, "NHL": 32}
    assert len(fixtures) == expected_counts[sport]
    for source_code, espn_code, display_name in fixtures:
        keys = rc.provider_team_keys(sport, team(espn_code, display_name))
        assert rc.normalize_team(sport, source_code) in keys


def test_targeted_observed_ncaab_identity_mismatches_are_sport_scoped():
    for source_code, espn_code, display_name in NCAAB_AUDIT_MISMATCHES:
        keys = rc.provider_team_keys("NCAAB", team(espn_code, display_name))
        assert rc.normalize_team("NCAAB", source_code) in keys
    miami_oh = team("M-OH", "Miami (OH) RedHawks")
    assert rc.normalize_team("NCAAB", "Miami") not in rc.provider_team_keys("NCAAB", miami_oh)


@pytest.mark.parametrize("sport,source,other_sport", [
    ("NBA", "NYK", "NCAAB"), ("MLB", "CWS", "NBA"), ("NHL", "UTA", "MLB"), ("NCAAB", "TXAM", "NFL"),
])
def test_identity_aliases_do_not_leak_between_sports(sport, source, other_sport):
    assert rc.normalize_team(sport, source) != rc.normalize_team(other_sport, source)
    assert rc.normalize_team(sport, "Unknown Identity") == "unknownidentity"


@pytest.mark.parametrize("sport,event_id,start,stored_away,stored_home,espn_away,away_name,espn_home,home_name", [
    ("NBA", "401809238", "2025-12-25T17:00:00Z", "CLE", "NYK", "CLE", "Cleveland Cavaliers", "NY", "New York Knicks"),
    ("NCAAB", "401819892", "2025-11-26T01:00:00Z", "KSU", "IND", "KSU", "Kansas State Wildcats", "IU", "Indiana Hoosiers"),
    ("MLB", "401697324", "2025-09-28T19:05:00Z", "CWS", "WSH", "CHW", "Chicago White Sox", "WSH", "Washington Nationals"),
    ("NHL", "401688896", "2025-04-16T00:00:00Z", "UTA", "STL", "UTAH", "Utah Hockey Club", "STL", "St. Louis Blues"),
])
def test_real_source_to_provider_identity_fixture_matches_end_to_end(
    sport, event_id, start, stored_away, stored_home, espn_away, away_name, espn_home, home_name,
):
    payload = event(event_id=event_id, away=espn_away, home=espn_home, date=start)
    payload["competitions"][0]["competitors"][0]["team"] = team(espn_away, away_name)
    payload["competitions"][0]["competitors"][1]["team"] = team(espn_home, home_name)
    provider = rc.parse_event(payload, sport)
    stored = game(sport=sport, away_team=stored_away, home_team=stored_home, event_start_utc=start)
    assert rc.match_stored_game(stored, [provider])["status"] == "matched"


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


@pytest.mark.parametrize("sport", tuple(SPORT_PATHS))
@pytest.mark.parametrize("score", [112, 112.0, "112"])
def test_every_supported_sport_accepts_only_integer_compatible_final_scores(sport, score):
    assert rc.parse_event(event(scores=(score, score)), sport)["away_score"] == 112


@pytest.mark.parametrize("sport", tuple(SPORT_PATHS))
@pytest.mark.parametrize("score", [None, "", "112.5", -1, "-1", "bad"])
def test_every_supported_sport_rejects_missing_or_invalid_final_scores(sport, score):
    with pytest.raises(ValueError, match="score"):
        rc.parse_event(event(scores=(score, "107")), sport)


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


@pytest.mark.parametrize("sport", ("NBA", "NCAAB", "NHL"))
def test_non_mlb_multiple_candidates_remain_fail_closed(sport):
    first = rc.parse_event(event(event_id="first", date="2026-09-01T19:00:00Z"), sport)
    second = rc.parse_event(event(event_id="second", date="2026-09-01T21:00:00Z"), sport)
    assert rc.match_stored_game(game(sport=sport), [first, second])["status"] == "ambiguous"


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
