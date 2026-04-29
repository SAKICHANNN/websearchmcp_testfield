"""SearchBench — Agent-driven WebSearch MCP Benchmark & Validation Suite.

The agent IS the executor. You drive the benchmark step by step:

    from searchbench import BenchmarkSession, ResultRecord

    session = BenchmarkSession("sci_research")
    for s in session.pending():
        # <call your MCP tool with s.query>
        session.record(s.id, ResultRecord.from_mcp_output(...))
        print(session.progress())

    report = session.finish()
    print(f"Success rate: {report.success_rate:.1%}")
"""

__version__ = "0.2.0"

from searchbench.scenario import Scenario, Suite, get_suite, ALL_SUITES, SUITE_META
from searchbench.session import BenchmarkSession, ResultRecord, SessionState
from searchbench.metrics import MetricsCalculator, QueryResult, SuiteReport
from searchbench.reporter import Reporter
