import pytest
from history_store import HistoryStore, bucket_performance, calculate_clv, event_key, game_key, line_vs_split_bucket, normalize_utc, signal_key, performance

def item(**changes):
    data={"observed_at_utc":"2026-09-13T00:00:00Z","sport":"NFL","matchup":"DEN vs KC","event_start_utc":"2026-09-14T20:00:00Z","market":"Spread","selection":"DEN","selection_side":"away","split_line":"+3 / -3","bets_pct":40,"money_pct":55,"money_minus_bets_gap":15,"best_line":"+3","best_price":-110,"break_even_pct":52.38,"data_quality":"OK","line_vs_split":"Better (+0.5)"}; data.update(changes); return data

def test_game_and_signal_keys_normalize_utc_and_distinguish_signal():
    game=game_key("NFL","DEN vs KC","2026-09-14T20:00:00Z")
    assert game == game_key("nfl","den vs kc","2026-09-14T20:00:00+00:00")
    assert signal_key(game,"Spread","DEN") != signal_key(game,"Spread","KC")
    assert event_key("NFL","DEN vs KC","2026-09-14T20:00:00Z","Spread","DEN") == signal_key(game,"Spread","DEN")

def test_sqlite_and_duplicate_snapshot_prevention():
    store=HistoryStore("sqlite:///:memory:"); assert store.insert_snapshot(item()); assert not store.insert_snapshot(item()); assert len(store.snapshots())==1 and not store.is_postgres

def test_final_score_stored_once_per_game_and_settles_each_snapshot_line():
    store=HistoryStore("sqlite:///:memory:"); early=item(best_line="+3"); late=item(observed_at_utc="2026-09-13T01:00:00Z",best_line="+2.5")
    store.insert_snapshots([early,late]); store.record_game_result("NFL","DEN vs KC","2026-09-14T20:00:00+00:00",20,23)
    assert len(store.results())==1
    rows=store.analytics_rows(); assert len(rows)==1 and rows[0]["bet_result"]=="push"
    from history_store import settle_selection, line_value
    assert settle_selection("Spread","away",line_value(early["best_line"]),20,23)=="push"
    assert settle_selection("Spread","away",line_value(late["best_line"]),20,23)=="loss"

def test_total_snapshots_with_different_lines_settle_differently():
    from history_store import settle_selection, line_value
    assert settle_selection("Total","over",line_value("o45"),24,21)=="push"
    assert settle_selection("Total","over",line_value("o45.5"),24,21)=="loss"

def test_bettor_favorable_clv_directions_and_moneyline_probability():
    assert calculate_clv("Spread","away",6,5.5,-110,-110)==0.5
    assert calculate_clv("Spread","home",-5.5,-6,-110,-110)==0.5
    assert calculate_clv("Total","over",46.5,47,-110,-110)==0.5
    assert calculate_clv("Total","under",47.5,47,-110,-110)==0.5
    assert calculate_clv("Moneyline","away",None,None,150,130)>0

def test_first_current_close_are_pregame_only():
    store=HistoryStore("sqlite:///:memory:"); first=item(observed_at_utc="2026-09-13T00:00:00Z",best_line="+3"); close=item(observed_at_utc="2026-09-14T19:59:00Z",best_line="+2.5"); post=item(observed_at_utc="2026-09-14T20:01:00Z",best_line="+2")
    assert store.insert_snapshots([first,close,post])==2
    signal=event_key("NFL","DEN vs KC","2026-09-14T20:00:00Z","Spread","DEN"); state=store.first_current_close(signal)
    assert state["first"]["best_line"]=="+3" and state["current"]["best_line"]=="+2.5" and state["close"]["best_line"]=="+2.5"

def test_missing_start_and_priceless_rows_are_excluded_from_production_baseline():
    store=HistoryStore("sqlite:///:memory:"); bad=item(event_start_utc=None); price_less=item(best_price=None)
    assert not store.insert_snapshot(bad); assert store.insert_snapshot(price_less); assert store.baseline_entries()==[]
    assert performance([dict(price_less,bet_result="loss")])["settled"]==0

def test_postgres_row_conversion_abstraction_is_dict_like():
    store=HistoryStore("sqlite:///:memory:"); store.insert_snapshot(item()); assert isinstance(store.snapshots()[0],dict)

def test_single_snapshot_has_no_invented_clv():
    store=HistoryStore("sqlite:///:memory:"); entry=item(); store.insert_snapshot(entry); store.record_game_result("NFL","DEN vs KC",entry["event_start_utc"],24,20)
    assert store.analytics_rows()[0]["clv"] is None

def test_later_executable_close_can_be_equal_or_changed():
    store=HistoryStore("sqlite:///:memory:"); entry=item(best_line="+3"); equal=item(observed_at_utc="2026-09-13T01:00:00Z",best_line="+3")
    store.insert_snapshots([entry,equal]); store.record_game_result("NFL","DEN vs KC",entry["event_start_utc"],24,20)
    assert store.analytics_rows()[0]["clv"]==0
    store=HistoryStore("sqlite:///:memory:"); changed=item(best_line="+3"); close=item(observed_at_utc="2026-09-13T01:00:00Z",best_line="+2.5")
    store.insert_snapshots([changed,close]); store.record_game_result("NFL","DEN vs KC",changed["event_start_utc"],24,20)
    assert store.analytics_rows()[0]["clv"]==0.5

def test_invalid_later_quote_does_not_become_closing_line():
    store=HistoryStore("sqlite:///:memory:"); entry=item(best_line="+3"); invalid=item(observed_at_utc="2026-09-13T01:00:00Z",best_line="+2.5",best_price=None)
    store.insert_snapshots([entry,invalid]); store.record_game_result("NFL","DEN vs KC",entry["event_start_utc"],24,20)
    assert store.analytics_rows()[0]["clv"] is None

def test_opposite_side_negative_gap_is_not_a_second_baseline_wager():
    store=HistoryStore("sqlite:///:memory:"); positive=item(selection="DEN",money_minus_bets_gap=20); negative=item(selection="KC",selection_side="home",money_minus_bets_gap=-20)
    store.insert_snapshots([positive,negative]); assert len(store.baseline_entries())==1

def test_line_vs_split_grouping_is_transparent():
    assert line_vs_split_bucket("Better (+0.5)")=="bettor-favorable"
    assert line_vs_split_bucket("Worse (-0.5)")=="bettor-unfavorable"
    assert line_vs_split_bucket("Same (+0)")=="same"
    assert line_vs_split_bucket("No Material Movement")=="same"
    assert line_vs_split_bucket("N/A")=="not-applicable"
    assert set(bucket_performance([dict(item(),line_vs_split="N/A",bet_result="win")],"line_vs_split"))=={"not-applicable"}

@pytest.mark.parametrize("url",[None,"bogus://database","sqlite:///history.db"])
def test_production_requires_postgresql(url):
    with pytest.raises(RuntimeError): HistoryStore(url,production=True)

def test_explicit_sqlite_is_local_only():
    assert not HistoryStore("sqlite:///:memory:",production=False).is_postgres

def stored_game(store, start="2026-09-01T20:00:00Z", away="DEN", home="KC"):
    away_row=item(observed_at_utc="2026-08-31T00:00:00Z",event_start_utc=start,selection=away,selection_side="away")
    home_row=item(observed_at_utc="2026-08-31T00:00:00Z",event_start_utc=start,selection=home,selection_side="home")
    store.insert_snapshots([away_row,home_row]); return away_row

def test_provenance_migration_is_idempotent_and_preserves_existing_result(tmp_path):
    import sqlite3
    database=tmp_path / "legacy.db"; connection=sqlite3.connect(database)
    connection.execute("CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, game_key TEXT NOT NULL UNIQUE, sport TEXT NOT NULL, matchup TEXT NOT NULL, event_start_utc TEXT NOT NULL, away_score INTEGER NOT NULL, home_score INTEGER NOT NULL, settled_at_utc TEXT NOT NULL)")
    connection.execute("INSERT INTO results (game_key,sport,matchup,event_start_utc,away_score,home_score,settled_at_utc) VALUES ('old','NFL','OLD vs ROW','2026-01-01T00:00:00Z',1,2,'2026-01-02T00:00:00Z')")
    connection.commit(); connection.close()
    store=HistoryStore(f"sqlite:///{database}")
    store.ensure_result_provenance_columns(); store.ensure_result_provenance_columns()
    assert {"result_source","external_event_id","source_status","result_fetched_at_utc"} <= store.result_columns()
    assert store.results()[0]["matchup"] == "OLD vs ROW"

def test_migration_errors_propagate(monkeypatch):
    store=HistoryStore("sqlite:///:memory:")
    monkeypatch.setattr(store, "result_columns", lambda: set())
    monkeypatch.setattr(store, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schema failure")))
    with pytest.raises(RuntimeError, match="schema failure"): store.ensure_result_provenance_columns()

def test_unresolved_game_filters_and_safe_identity():
    from datetime import datetime, timezone
    store=HistoryStore("sqlite:///:memory:"); past=stored_game(store)
    future=stored_game(store,"2026-09-03T20:00:00Z","BUF","NYJ")
    store.insert_snapshot(item(observed_at_utc="2026-08-31T00:00:00Z",event_start_utc=past["event_start_utc"],market="Total",selection="Over",selection_side="over"))
    games=store.unresolved_games(datetime(2026,9,2,tzinfo=timezone.utc))
    assert len(games)==1 and games[0]["away_team"]=="DEN" and games[0]["home_team"]=="KC"
    store.record_game_result("NFL","DEN vs KC",past["event_start_utc"],20,17)
    assert store.unresolved_games(datetime(2026,9,2,tzinfo=timezone.utc))==[]

@pytest.mark.parametrize("side,extra", [("away", "ALT"), ("home", "ALT")])
def test_unresolved_identity_is_none_when_missing_or_conflicting(side, extra):
    from datetime import datetime, timezone
    store=HistoryStore("sqlite:///:memory:"); row=stored_game(store)
    if extra:
        store.insert_snapshot(item(observed_at_utc="2026-08-31T01:00:00Z",event_start_utc=row["event_start_utc"],selection=extra,selection_side=side))
    game=store.unresolved_games(datetime(2026,9,2,tzinfo=timezone.utc))[0]
    assert game[f"{side}_team"] is None
    other="home" if side=="away" else "away"; assert game[f"{other}_team"] is not None

@pytest.mark.parametrize("side", ["away", "home"])
def test_unresolved_identity_is_none_when_missing(side):
    from datetime import datetime, timezone
    store=HistoryStore("sqlite:///:memory:"); row=stored_game(store)
    store.execute("DELETE FROM snapshots WHERE game_key=? AND selection_side=?", (game_key(row["sport"],row["matchup"],row["event_start_utc"]),side)); store.connection.commit()
    assert store.unresolved_games(datetime(2026,9,2,tzinfo=timezone.utc))[0][f"{side}_team"] is None

def test_recent_results_window_and_identity_are_safe():
    from datetime import datetime, timezone
    store=HistoryStore("sqlite:///:memory:"); row=stored_game(store)
    store.record_game_result("NFL","DEN vs KC",row["event_start_utc"],20,17,settled_at_utc="2026-09-01T12:00:00+00:00",result_source="espn",external_event_id="x",source_status="final")
    assert len(store.recently_settled_games(datetime(2026,9,2,tzinfo=timezone.utc)))==1
    assert store.recently_settled_games(datetime(2026,9,4,tzinfo=timezone.utc))==[]
    store.insert_snapshot(item(observed_at_utc="2026-08-31T01:00:00Z",event_start_utc=row["event_start_utc"],selection="ALT",selection_side="away"))
    assert store.recently_settled_games(datetime(2026,9,2,tzinfo=timezone.utc))[0]["away_team"] is None

def test_result_upsert_preserves_original_settlement_and_updates_result(monkeypatch):
    import history_store
    from datetime import datetime, timezone
    timestamps=iter([datetime(2026,9,1,12,tzinfo=timezone.utc),datetime(2026,9,1,13,tzinfo=timezone.utc),datetime(2026,9,1,14,tzinfo=timezone.utc)])
    class Clock:
        fromisoformat=staticmethod(datetime.fromisoformat)
        now=staticmethod(lambda tz: next(timestamps))
    monkeypatch.setattr(history_store, "datetime", Clock)
    store=HistoryStore("sqlite:///:memory:")
    args=("NFL","DEN vs KC","2026-09-01T20:00:00+00:00")
    store.record_game_result(*args,20,17,settled_at_utc="2026-09-01T12:00:00Z",result_source="espn",external_event_id="one",source_status="final")
    first=store.results()[0]
    store.record_game_result(*args,20,17)
    same=store.results()[0]
    store.record_game_result(*args,21,17,source_status="corrected")
    corrected=store.results()[0]
    assert len(store.results())==1 and corrected["away_score"]==21
    assert first["settled_at_utc"]==same["settled_at_utc"]==corrected["settled_at_utc"]
    assert first["result_fetched_at_utc"] < same["result_fetched_at_utc"] < corrected["result_fetched_at_utc"]
    assert corrected["result_source"]=="espn" and corrected["external_event_id"]=="one" and corrected["source_status"]=="corrected"

def test_timestamp_normalization_makes_z_and_offset_boundaries_equivalent():
    from datetime import datetime, timezone
    assert normalize_utc("2026-09-01T12:00:00Z")==normalize_utc("2026-09-01T12:00:00+00:00")
    store=HistoryStore("sqlite:///:memory:"); row=stored_game(store,"2026-09-01T20:00:00+00:00")
    store.record_game_result("NFL","DEN vs KC",row["event_start_utc"],1,2,settled_at_utc="2026-09-01T12:00:00+00:00")
    assert len(store.recently_settled_games(datetime(2026,9,2,tzinfo=timezone.utc)))==1

def test_game_queries_filter_sports_and_routine_lookback_in_sql():
    from datetime import datetime, timezone
    store=HistoryStore("sqlite:///:memory:")
    def snapshot(sport,start,selection):
        observed="2026-07-30T00:00:00Z" if start.startswith("2026-08") else "2026-08-30T00:00:00Z"
        return item(sport=sport,matchup=f"{sport} game",observed_at_utc=observed,event_start_utc=start,selection=selection,selection_side="away")
    store.insert_snapshots([snapshot("NFL","2026-09-10T20:00:00Z","DEN"),snapshot("NCAAF","2026-09-10T20:00:00Z","TEX"),snapshot("NBA","2026-09-10T20:00:00Z","LAL"),snapshot("NFL","2026-08-01T20:00:00Z","BUF")])
    now=datetime(2026,9,14,tzinfo=timezone.utc)
    routine=store.unresolved_games(now,sports=("NFL","NCAAF"),lookback_days=14)
    assert {row["sport"] for row in routine}=={"NFL","NCAAF"}
    assert len(store.unresolved_games(now,sports=("NFL",),lookback_days=120))==2
