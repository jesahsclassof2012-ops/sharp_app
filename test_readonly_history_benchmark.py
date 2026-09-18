from pathlib import Path

import pytest

from analysis import readonly_history_benchmark as benchmark


class FakeCursor:
    def __init__(self, responses):
        self.responses = responses
        self.current = None
        self.executed = []

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, query):
        self.executed.append(query)
        self.current = query
    def fetchone(self): return self.responses[self.current][0]
    def fetchall(self): return self.responses[self.current]


class FakeConnection:
    def __init__(self, responses):
        self.cursor_instance = FakeCursor(responses)
        self.closed = False
    def cursor(self): return self.cursor_instance
    def close(self): self.closed = True


def verified_connection(user="sharp_benchmark_reader", readonly="on"):
    return FakeConnection({
        benchmark.CURRENT_USER_SQL: [{"current_user": user}],
        benchmark.READ_ONLY_SQL: [{"transaction_read_only": readonly}],
        benchmark.SNAPSHOTS_SQL: [],
        benchmark.RESULTS_SQL: [],
    })


def test_missing_readonly_database_url_fails_closed(monkeypatch):
    monkeypatch.delenv("READONLY_DATABASE_URL", raising=False)
    with pytest.raises(benchmark.BenchmarkSafetyError, match="READONLY_DATABASE_URL is required"):
        benchmark.connect_readonly()


def test_wrong_reader_role_and_readonly_off_fail_before_data_reads():
    with pytest.raises(benchmark.BenchmarkSafetyError, match="not the benchmark reader"):
        benchmark.verify_readonly_connection(verified_connection(user="postgres"))
    with pytest.raises(benchmark.BenchmarkSafetyError, match="not read-only"):
        benchmark.verify_readonly_connection(verified_connection(readonly="off"))


def test_fixed_queries_are_readonly_and_fetch_uses_no_user_sql():
    benchmark._assert_fixed_read_only_sql()
    connection = verified_connection()
    snapshots, results = benchmark.fetch_production_rows(connection)
    assert snapshots == results == []
    assert connection.cursor_instance.executed == [benchmark.SNAPSHOTS_SQL, benchmark.RESULTS_SQL]
    source = Path(benchmark.__file__).read_text(encoding="utf-8")
    assert "HistoryStore(" not in source
    assert "sys.argv" not in source and "input(" not in source


def test_report_is_aggregate_only_and_does_not_serialize_secret_or_raw_rows():
    audit = {"total_snapshots": 1, "unsafe": "postgresql://secret-value@host/example", "raw_rows": [{"matchup": "raw-game"}]}
    gates = {"passed": False, "failures": ["binary W/L rows"]}
    report = benchmark.render_report(audit, {"main_gate": gates, "movement_gate": gates, "models": {}, "uncertainty": {}})
    # Aggregate report generation never receives raw rows or a DSN in normal use.
    # Guard against accidental inclusion if an unexpected value reaches audit data.
    assert "secret-value" not in report
    assert "postgresql://" not in report
    assert "raw-game" not in report
    assert "raw rows" in report


def test_underpowered_data_stops_fitting_and_preserves_one_sided_label():
    result = benchmark.benchmark_if_sufficient([])
    assert result["models"] == {}
    assert result["main_gate"]["passed"] is False
    assert "one-sided implied market probability" in benchmark.render_report(
        {"total_snapshots": 0}, result
    )


def test_workflow_is_manual_and_only_uses_readonly_secret():
    workflow = Path(".github/workflows/run-readonly-history-benchmark.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow and "push:" not in workflow
    assert "contents: read" in workflow
    assert "secrets.READONLY_DATABASE_URL" in workflow
    assert "secrets.DATABASE_URL" not in workflow
    assert "set -x" not in workflow


def oos_row(game, signal, *, sport="NFL", market="Moneyline", outcome="win", edge=.03, clv=.02, bets=40, start="2026-01-02T20:00:00Z"):
    return {"game_key": game, "signal_key": signal, "sport": sport, "market": market, "outcome": outcome, "best_price": -110, "predicted_edge": edge, "clv": clv, "bets_pct": bets, "raw_gap": 15, "minutes_to_start": 120, "event_start_utc": start}


def test_report_renders_complete_aggregate_sections_and_provenance_wording():
    gates = {"passed": True, "failures": []}
    benchmark_data = {
        "main_gate": gates, "movement_gate": gates,
        "models": {"Model 0B": {"rows": 20, "log_loss": .6, "brier": .2}},
        "comparisons": {"Model 1 vs Model 0B": {"matched_rows": 20, "delta_log_loss": -.01}},
        "fold_metrics": [{"model": "Model 0B", "fold": 0, "rows": 20, "log_loss": .6, "brier": .2}],
        "calibration": {"Model 0B": [{"bucket": "50–60%", "count": 20}]},
        "edge_buckets": {"Model 0B": [{"bucket": ">2% to 5%", "bets": 20, "wins": 11, "losses": 7, "pushes": 2, "units": 2, "roi": .1}]},
        "roi_uncertainty": {"Model 0B": {">2% to 5%": {"available": True, "roi_ci_95": [-.1, .2]}}},
        "uncertainty": {"Model 1": {"log_loss_delta_ci_95": [-.02, .01]}},
        "diagnostics": {"sport": {"minimum_rows": 20, "buckets": {"NFL": {"bets": 20}}, "suppressed_bucket_rows": {}}},
    }
    report = benchmark.render_report({"total_snapshots": 100, "result_coverage_by_sport": {"NFL": {"settled_games": 20}}}, benchmark_data)
    for heading in ("Fold-by-fold OOS metrics", "Calibration buckets", "Fixed predicted-edge bucket economics", "Game-clustered ROI uncertainty", "Diagnostic breakdowns"):
        assert heading in report
    assert "one-sided implied market probability" in report
    assert "same-book paired-price provenance: **unavailable**" in report
    assert "official sportsbook closing line" in report


def test_diagnostics_suppress_small_buckets_and_result_coverage_is_aggregate():
    rows = [oos_row(f"g{index}", f"s{index}") for index in range(19)]
    diagnostics = benchmark.diagnostic_breakdowns(rows)
    assert diagnostics["sport"]["buckets"] == {}
    assert diagnostics["sport"]["suppressed_bucket_rows"] == {"NFL": 19}
    audit = benchmark.audit_statistics([], [{"game_key": "g1"}], [])
    assert "result_coverage_by_sport" in audit and "result_coverage_by_market" in audit


def test_roi_uncertainty_clusters_by_game_and_marks_small_samples_unavailable():
    rows = [oos_row(f"g{index}", f"s{index}a") for index in range(20)]
    rows += [oos_row(f"g{index}", f"s{index}b", outcome="loss") for index in range(20)]
    bootstrap = benchmark.roi_bootstrap_by_bucket(rows)
    bucket = bootstrap[">2% to 5%"]
    assert bucket["available"] is True and bucket["distinct_games"] == 20 and len(bucket["roi_ci_95"]) == 2
    assert benchmark.roi_bootstrap_by_bucket(rows[:19])[">2% to 5%"]["available"] is False
