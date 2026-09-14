from history_store import HistoryStore, calculate_clv, event_key, game_key, signal_key, performance

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
