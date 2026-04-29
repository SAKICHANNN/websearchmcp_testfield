"""Tests for scenario definitions, session management, and metrics."""

import json
import tempfile
from pathlib import Path

import pytest

from searchbench.scenario import (
    Scenario, Suite, StabilityKind,
    get_suite, get_scenario, ALL_SUITES, SUITE_META,
    SCI_RESEARCH, CODE_INTEL, STABILITY,
)
from searchbench.session import BenchmarkSession, ResultRecord
from searchbench.metrics import MetricsCalculator, QueryResult, SuiteReport


# ── Scenario definitions ──────────────────────────────────────────

class TestSciResearch:
    def test_count(self):
        assert len(SCI_RESEARCH) == 12

    def test_all_have_required_fields(self):
        for s in SCI_RESEARCH:
            assert s.id.startswith("sci-")
            assert s.name
            assert s.query
            assert s.min_expected_results > 0
            assert s.suite == Suite.SCI_RESEARCH

    def test_includes_chinese(self):
        zh = [s for s in SCI_RESEARCH if s.language == "zh"]
        assert len(zh) == 2

    def test_domains_look_valid(self):
        for s in SCI_RESEARCH:
            for d in s.expected_domains:
                assert "." in d

    def test_get_suite(self):
        result = get_suite("sci_research")
        assert len(result) == 12

    def test_get_scenario(self):
        s = get_scenario("sci-001")
        assert "protein" in s.query.lower()

    def test_get_scenario_not_found(self):
        with pytest.raises(KeyError):
            get_scenario("nonexistent")


class TestCodeIntel:
    def test_count(self):
        assert len(CODE_INTEL) == 15

    def test_has_error_debugging(self):
        queries = " ".join(s.query.lower() for s in CODE_INTEL)
        debug_terms = ["error", "fix", "debug", "troubleshoot", "crash", "oom"]
        assert any(t in queries for t in debug_terms)

    def test_has_chinese(self):
        zh = [s for s in CODE_INTEL if s.language == "zh"]
        assert len(zh) == 2

    def test_all_have_keywords(self):
        for s in CODE_INTEL:
            assert len(s.expected_keywords) >= 2


class TestStability:
    def test_count(self):
        assert len(STABILITY) == 23

    def test_has_empty_kinds(self):
        kinds = [s.stability_kind for s in STABILITY if s.stability_kind]
        assert StabilityKind.EMPTY in kinds
        assert StabilityKind.SPECIAL in kinds
        assert StabilityKind.INJECTION in kinds
        assert StabilityKind.RECOVERY in kinds
        assert StabilityKind.VERY_LONG in kinds

    def test_recovery_queries_are_valid(self):
        recovery = [s for s in STABILITY if s.stability_kind == StabilityKind.RECOVERY]
        assert len(recovery) == 4
        for s in recovery:
            assert len(s.query) > 10, f"Recovery query too short: {s.query}"

    def test_injection_scenarios_exist(self):
        injections = [s for s in STABILITY if s.stability_kind == StabilityKind.INJECTION]
        assert len(injections) == 3


class TestSuiteRegistry:
    def test_all_suites_registered(self):
        assert set(ALL_SUITES.keys()) == {"sci_research", "code_intel", "stability"}

    def test_suite_meta(self):
        for name in ALL_SUITES:
            assert name in SUITE_META
            assert "name" in SUITE_META[name]
            assert SUITE_META[name]["scenario_count"] == len(ALL_SUITES[name])

    def test_get_suite_unknown(self):
        with pytest.raises(KeyError):
            get_suite("nope")


# ── Session ───────────────────────────────────────────────────────

class TestResultRecord:
    def test_from_mcp_output_success(self):
        rr = ResultRecord.from_mcp_output(
            success=True,
            latency_ms=234.5,
            results=[
                {"url": "https://a.com/1", "snippet": "result one"},
                {"url": "https://b.com/2", "snippet": "result two"},
            ],
        )
        assert rr.success is True
        assert rr.latency_ms == 234.5
        assert rr.result_count == 2
        assert len(rr.result_urls) == 2
        assert rr.error_message == ""

    def test_from_mcp_output_failure(self):
        rr = ResultRecord.from_mcp_output(
            success=False,
            latency_ms=50,
            results=[],
            error="connection timed out",
        )
        assert rr.success is False
        assert rr.error_type == "timeout"

    def test_from_mcp_output_none_results(self):
        rr = ResultRecord.from_mcp_output(success=True, latency_ms=100, results=None)
        assert rr.success is True
        assert rr.result_count == 0
        assert rr.result_urls == []


class TestBenchmarkSession:
    def test_create(self):
        s = BenchmarkSession("sci_research", results_dir="results")
        assert s.suite_name == "sci_research"
        assert s.total_count() == 12
        assert s.pending_count() == 12
        assert s.done_count() == 0
        assert "12" in s.progress()
        assert s.session_id.endswith("_sci_research")

    def test_record_and_progress(self):
        s = BenchmarkSession("code_intel", results_dir="results")
        pending = s.pending()
        assert len(pending) == 15

        # Record first scenario
        first = pending[0]
        rr = ResultRecord.from_mcp_output(
            success=True, latency_ms=100,
            results=[{"url": "https://x.com", "snippet": "test"}],
        )
        score = s.record(first.id, rr)
        assert "relevance" in score
        assert s.done_count() == 1
        assert s.pending_count() == 14

    def test_duplicate_record_raises(self):
        s = BenchmarkSession("sci_research", results_dir="results")
        first = s.pending()[0]
        rr = ResultRecord.from_mcp_output(success=True, latency_ms=100, results=[])
        s.record(first.id, rr)
        with pytest.raises(ValueError, match="already recorded"):
            s.record(first.id, rr)

    def test_replace(self):
        s = BenchmarkSession("sci_research", results_dir="results")
        first = s.pending()[0]
        rr1 = ResultRecord.from_mcp_output(success=True, latency_ms=100, results=[])
        s.record(first.id, rr1)

        rr2 = ResultRecord.from_mcp_output(success=False, latency_ms=50, error="timeout")
        s.replace(first.id, rr2)

        stored = s.get_result(first.id)
        assert stored.success is False
        assert stored.latency_ms == 50

    def test_finish_generates_report(self):
        s = BenchmarkSession("stability", results_dir="results")
        for scenario in s.pending():
            rr = ResultRecord.from_mcp_output(
                success=True, latency_ms=100,
                results=[{"url": f"https://{scenario.id}.com", "snippet": scenario.query}],
            )
            s.record(scenario.id, rr)

        report = s.finish()
        assert isinstance(report, SuiteReport)
        assert report.total_queries == 23
        assert report.successful == 23
        assert report.success_rate == 1.0
        assert report.avg_latency_ms == 100
        assert report.avg_result_count == 1

    def test_finish_auto_fails_missing(self):
        s = BenchmarkSession("sci_research", results_dir="results")
        # Only record half
        for scenario in s.pending()[:6]:
            rr = ResultRecord.from_mcp_output(success=True, latency_ms=100, results=[])
            s.record(scenario.id, rr)

        report = s.finish()
        assert report.total_queries == 12
        assert report.successful == 6
        assert report.failed == 6
        assert "not_executed" in report.error_types

    def test_persist_and_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = BenchmarkSession("code_intel", results_dir=tmpdir)
            pending = s1.pending()

            # Record 3 results
            for scenario in pending[:3]:
                rr = ResultRecord.from_mcp_output(
                    success=True, latency_ms=200,
                    results=[{"url": "https://x.com", "snippet": "test"}],
                )
                s1.record(scenario.id, rr)

            session_id = s1.session_id

            # Resume
            s2 = BenchmarkSession.resume(session_id, results_dir=tmpdir)
            assert s2.suite_name == "code_intel"
            assert s2.done_count() == 3
            assert s2.pending_count() == 12

            # Record rest and finish
            for scenario in s2.pending():
                rr = ResultRecord.from_mcp_output(success=True, latency_ms=200, results=[])
                s2.record(scenario.id, rr)

            report = s2.finish()
            assert report.successful == 15

    def test_list_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BenchmarkSession("stability", results_dir=tmpdir)
            sid = s.session_id
            # Record all and finish
            for scenario in s.pending():
                rr = ResultRecord.from_mcp_output(success=True, latency_ms=100, results=[])
                s.record(scenario.id, rr)
            s.finish()

            sessions = BenchmarkSession.list_sessions(tmpdir)
            assert len(sessions) == 1
            assert sessions[0]["session_id"] == sid
            assert sessions[0]["status"] == "finished"

    def test_record_batch(self):
        s = BenchmarkSession("sci_research", results_dir="results")
        pending = s.pending()[:3]
        batch = {
            p.id: ResultRecord.from_mcp_output(success=True, latency_ms=100, results=[])
            for p in pending
        }
        s.record_batch(batch)
        assert s.done_count() == 3


# ── Metrics ───────────────────────────────────────────────────────

class TestMetrics:
    def test_relevance_perfect(self):
        result = QueryResult(
            scenario_id="x", query="python async programming",
            category="test", success=True, latency_ms=100, result_count=1,
            result_snippets=["python async programming is great"],
        )
        score = MetricsCalculator.compute_relevance("python async programming", result)
        assert score >= 0.5

    def test_relevance_no_match(self):
        result = QueryResult(
            scenario_id="x", query="quantum computing",
            category="test", success=True, latency_ms=100, result_count=1,
            result_snippets=["cooking recipes"],
        )
        score = MetricsCalculator.compute_relevance("quantum computing", result)
        assert score < 0.5

    def test_relevance_empty(self):
        result = QueryResult(scenario_id="x", query="test", category="t",
                             success=True, latency_ms=100, result_count=0)
        assert MetricsCalculator.compute_relevance("test", result) == 0.0

    def test_diversity_all_unique(self):
        result = QueryResult(
            scenario_id="x", query="t", category="t", success=True, latency_ms=100,
            result_count=3,
            result_urls=["https://a.com", "https://b.org", "https://c.net"],
        )
        assert MetricsCalculator.compute_diversity(result) == 1.0

    def test_diversity_same_domain(self):
        result = QueryResult(
            scenario_id="x", query="t", category="t", success=True, latency_ms=100,
            result_count=3,
            result_urls=["https://github.com/a", "https://github.com/b", "https://github.com/c"],
        )
        assert MetricsCalculator.compute_diversity(result) < 1.0

    def test_diversity_single(self):
        result = QueryResult(scenario_id="x", query="t", category="t",
                             success=True, latency_ms=100, result_count=1,
                             result_urls=["https://x.com"])
        assert MetricsCalculator.compute_diversity(result) == 0.0

    def test_categorize_errors(self):
        assert MetricsCalculator.categorize_error("timed out after 30s") == "timeout"
        assert MetricsCalculator.categorize_error("rate limit exceeded") == "rate_limit"
        assert MetricsCalculator.categorize_error("connection refused") == "connection"
        assert MetricsCalculator.categorize_error("not executed by agent") == "not_executed"
        assert MetricsCalculator.categorize_error("???") == "unknown"

    def test_percentiles(self):
        p = MetricsCalculator.compute_percentiles(list(range(1, 101)))
        assert abs(p["p50"] - 50.5) < 1
        assert abs(p["p95"] - 95.05) < 1

    def test_percentiles_empty(self):
        assert MetricsCalculator.compute_percentiles([]) == {"p50": 0, "p95": 0, "p99": 0}

    def test_build_report(self):
        results = [
            QueryResult("a", "test a", "cat", True, 100, 5,
                        ["https://a.com"], ["snippet a"]),
            QueryResult("b", "test b", "cat", True, 300, 3,
                        ["https://b.com"], ["snippet b"]),
            QueryResult("c", "test c", "cat", False, 50,
                        error_message="timeout"),
        ]
        report = MetricsCalculator.build_report("suite", results, 10.0)
        assert report.total_queries == 3
        assert report.successful == 2
        assert report.failed == 1
        assert report.success_rate == 2/3
        assert report.avg_latency_ms == 200
        assert report.min_latency_ms == 100
        assert report.max_latency_ms == 300
        assert report.avg_result_count == 4
        assert "timeout" in report.error_types
        assert report.duration_sec == 10.0

    def test_thresholds_all_pass(self):
        results = []
        for i in range(10):
            results.append(QueryResult(
                f"s{i}", f"query {i}", "test", True, 100, 5,
                ["https://a.com"], [f"query {i} result"],
            ))
        report = MetricsCalculator.build_report("test", results, 1.0)
        failures = MetricsCalculator.check_thresholds(report)
        assert len(failures) == 0

    def test_thresholds_fail_low_success(self):
        results = [
            QueryResult("a", "q", "t", False, 50, error_message="x"),
            QueryResult("b", "q", "t", False, 50, error_message="x"),
            QueryResult("c", "q", "t", True, 100, 1, ["https://a.com"], ["s"]),
        ]
        report = MetricsCalculator.build_report("test", results, 1.0)
        failures = MetricsCalculator.check_thresholds(report)
        assert any(f["metric"] == "success_rate" for f in failures)


# ── Scenario serialization ────────────────────────────────────────

class TestScenarioSerialization:
    def test_to_dict(self):
        s = Scenario(id="test-1", name="Test", query="hello",
                     suite=Suite.CODE_INTEL, expected_keywords=["a", "b"])
        d = s.to_dict()
        assert d["id"] == "test-1"
        assert d["suite"] == "code_intel"
        assert d["expected_keywords"] == ["a", "b"]

    def test_all_scenarios_serializable(self):
        for name in ALL_SUITES:
            for s in get_suite(name):
                d = s.to_dict()
                json.dumps(d)  # Must not raise
                assert d["id"] == s.id
                assert d["query"] == s.query
