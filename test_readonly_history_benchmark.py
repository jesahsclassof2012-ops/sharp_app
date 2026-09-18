from pathlib import Path
import os
import subprocess
import sys
import types

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


def test_module_entrypoint_imports_then_fails_closed_without_readonly_url():
    """The workflow entrypoint must preserve package imports without a DB connection."""
    environment = os.environ.copy()
    environment.pop("READONLY_DATABASE_URL", None)
    completed = subprocess.run(
        [sys.executable, "-m", "analysis.readonly_history_benchmark"],
        cwd=Path(__file__).parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "READONLY_DATABASE_URL is required" in output
    assert "ModuleNotFoundError" not in output
    assert "Unable to connect to the configured read-only database" not in output


def test_readonly_connection_requires_ssl_without_exposing_connection_string(monkeypatch, capsys):
    captured = {}
    fake_psycopg = types.ModuleType("psycopg")
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = object()
    def connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()
    fake_psycopg.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)
    secret_url = "postgresql://secret-value@host/benchmark"
    monkeypatch.setenv("READONLY_DATABASE_URL", secret_url)
    benchmark.connect_readonly()
    assert captured["kwargs"]["sslmode"] == "require"
    assert captured["kwargs"]["autocommit"] is True
    assert captured["kwargs"]["options"] == benchmark.CONNECTION_OPTIONS
    assert secret_url not in capsys.readouterr().out


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


def _benchmark_runner_result(names):
    return {
        "predictions": {name: [] for name in names},
        "metrics": {name: {"rows": 0, "log_loss": None, "brier": None} for name in names},
        "fold_metrics": [{"model": name, "fold": 0, "rows": 0, "log_loss": None, "brier": None} for name in names],
        "calibration": {name: [{"bucket": "50–60%", "count": 0}] for name in names},
        "edge_buckets": {name: [{"bucket": "<=0%", "bets": 0}] for name in names},
    }


@pytest.mark.parametrize(
    ("main_passed", "movement_passed", "expected_calls", "expected_models"),
    (
        (False, False, [], set()),
        (True, False, [(False, True)], {"Model 0A", "Model 0B", "Model 1", "Model 2", "Model 3"}),
        (False, True, [(True, False)], {"Model 3 movement cohort", "Model 4"}),
        (True, True, [(False, True), (True, False)], {"Model 0A", "Model 0B", "Model 1", "Model 2", "Model 3", "Model 3 movement cohort", "Model 4"}),
    ),
)
def test_main_and_movement_gates_execute_independently(monkeypatch, main_passed, movement_passed, expected_calls, expected_models):
    """Movement fitting is independently gated and never refits primary models."""
    calls = []
    def fake_gate(_entries, movement=False):
        return {"passed": movement_passed if movement else main_passed, "failures": [], "folds": [], "cohort": [{"movement_60m": 1.0}]}
    def fake_runner(_rows, *, folds, include_movement, include_primary=True):
        calls.append((include_movement, include_primary))
        names = ("Model 3 movement cohort", "Model 4") if not include_primary else ("Model 0A", "Model 0B", "Model 1", "Model 2", "Model 3")
        return _benchmark_runner_result(names)
    monkeypatch.setattr(benchmark, "sufficiency_gate", fake_gate)
    monkeypatch.setattr(benchmark, "run_walk_forward_benchmark", fake_runner)
    result = benchmark.benchmark_if_sufficient([{}])
    assert calls == expected_calls
    assert set(result["models"]) == expected_models
    if movement_passed:
        assert result["calibration"]["Model 3 movement cohort"]
        assert result["calibration"]["Model 4"]
        assert result["edge_buckets"]["Model 3 movement cohort"]
        assert result["edge_buckets"]["Model 4"]
        assert "Model 4 vs Model 3 movement cohort" in result["comparisons"]
        assert "Model 4 vs Model 3 movement cohort" in result["uncertainty"]
        assert "Model 4" in result["roi_uncertainty"]
    else:
        assert "Model 4" not in result["models"]
    if not main_passed:
        assert result["comparisons"] == ({"Model 4 vs Model 3 movement cohort": result["comparisons"]["Model 4 vs Model 3 movement cohort"]} if movement_passed else {})
        assert result["diagnostics"] == {}


def test_workflow_is_manual_and_only_uses_readonly_secret():
    workflow = Path(".github/workflows/run-readonly-history-benchmark.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow and "push:" not in workflow
    assert "contents: read" in workflow
    assert "secrets.READONLY_DATABASE_URL" in workflow
    assert "secrets.DATABASE_URL" not in workflow
    assert "set -x" not in workflow
    assert "run: python -m analysis.readonly_history_benchmark" in workflow
    assert "run: python analysis/readonly_history_benchmark.py" not in workflow


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
    for heading in ("Market-state pair audit", "Legacy per-selection baseline audit", "Direction-flip / material-reversal audit", "Landmark coverage", "Threshold-strategy observation coverage", "Threshold lock-policy economics", "Fold-by-fold OOS metrics", "Calibration buckets", "Fixed predicted-edge bucket economics", "Game-clustered ROI uncertainty", "Diagnostic breakdowns"):
        assert heading in report
    assert "one-sided implied market probability" in report
    assert "same-book paired-price provenance: **unavailable**" in report
    assert "official sportsbook closing line" in report


def test_horizon_keyed_report_has_no_pooled_benchmark_sections():
    gates = {"passed": False, "failures": ["binary W/L rows"]}
    benchmark_data = {"by_landmark": {"T-360m": {"main_gate": gates, "movement_gate": gates, "models": {}, "comparisons": {}, "fold_metrics": [], "calibration": {}, "edge_buckets": {}, "roi_uncertainty": {}, "uncertainty": {}, "diagnostics": {}}, "T-180m": {"main_gate": gates, "movement_gate": gates, "models": {}, "comparisons": {}, "fold_metrics": [], "calibration": {}, "edge_buckets": {}, "roi_uncertainty": {}, "uncertainty": {}, "diagnostics": {}}}}
    report = benchmark.render_report({}, benchmark_data)
    assert "## Sufficiency by landmark" in report and "T-360m" in report and "T-180m" in report
    assert "## Sufficiency\n" not in report


def test_market_state_report_sections_remain_aggregate_only():
    gates = {"passed": False, "failures": ["binary W/L rows"]}
    report = benchmark.render_report(
        {"market_state_pair_audit": {"valid_split_states": 2}, "landmark_coverage": {"T-60m": {"usable_landmark_rows": 1}}},
        {"main_gate": gates, "movement_gate": gates, "models": {}, "uncertainty": {}},
    )
    assert "valid_split_states" in report
    assert "matchup" not in report.casefold() and "signal_key" not in report


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


def test_economic_summary_does_not_invent_zero_predicted_edge():
    rows = [{"outcome": "win", "best_price": -110}, {"outcome": "loss", "best_price": -110, "predicted_edge": .04}]
    summary = benchmark._economic_summary(rows)
    assert summary["average_predicted_edge"] == .04
    assert benchmark._economic_summary(rows[:1])["average_predicted_edge"] is None
