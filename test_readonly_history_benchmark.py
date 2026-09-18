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
    audit = {"total_snapshots": 1, "unsafe": "postgresql://secret-value@host/example"}
    gates = {"passed": False, "failures": ["binary W/L rows"]}
    report = benchmark.render_report(audit, {"main_gate": gates, "movement_gate": gates, "models": {}, "uncertainty": {}})
    # Aggregate report generation never receives raw rows or a DSN in normal use.
    # Guard against accidental inclusion if an unexpected value reaches audit data.
    assert "secret-value" not in report
    assert "postgresql://" not in report
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
