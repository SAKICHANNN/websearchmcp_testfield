"""Stateful benchmark session — driven by the coding agent.

The agent loop:
  1. session = BenchmarkSession("sci_research")
  2. for scenario in session.pending():
         result = <agent calls MCP tool with scenario.query>
         session.record(scenario.id, result)
  3. session.finish()  → saves report, prints summary

The session handles all state management, persistence, scoring, and reporting.
The agent only needs to: get pending queries, call MCP, feed results back.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from searchbench.scenario import Scenario, Suite, get_suite, ALL_SUITES, SUITE_META
from searchbench.metrics import MetricsCalculator, QueryResult, SuiteReport


# ── Data structures ───────────────────────────────────────────────

@dataclass
class ResultRecord:
    """What the agent feeds back after each MCP call."""
    success: bool
    latency_ms: float
    result_count: int = 0
    result_urls: list[str] = field(default_factory=list)
    result_snippets: list[str] = field(default_factory=list)
    error_message: str = ""
    error_type: str = ""

    @classmethod
    def from_mcp_output(cls, success: bool, latency_ms: float,
                        results: list[dict] | None = None,
                        error: str = "") -> "ResultRecord":
        """Parse a typical MCP tool output into a ResultRecord.

        `results` is expected to be a list of dicts like:
            [{"url": "...", "snippet": "...", "title": "..."}, ...]
        """
        results = results or []
        urls = [r.get("url", "") for r in results]
        snippets = [r.get("snippet", r.get("title", "")) for r in results]
        err_type = MetricsCalculator.categorize_error(error) if error else ""

        return cls(
            success=success,
            latency_ms=latency_ms,
            result_count=len(results),
            result_urls=urls,
            result_snippets=snippets,
            error_message=error,
            error_type=err_type,
        )


@dataclass
class SessionState:
    """Serializable session state for persistence."""
    session_id: str
    suite_name: str
    status: str = "running"  # running | finished | abandoned
    results: dict[str, dict] = field(default_factory=dict)  # scenario_id → ResultRecord as dict
    started_at: str = ""
    finished_at: str = ""
    notes: str = ""

    @property
    def done_count(self) -> int:
        return len(self.results)

    @property
    def pending_ids(self) -> list[str]:
        if not hasattr(self, "_scenario_ids"):
            return []
        return [sid for sid in self._scenario_ids if sid not in self.results]

    def set_scenario_ids(self, ids: list[str]):
        self._scenario_ids = ids


# ── Session manager ───────────────────────────────────────────────

class BenchmarkSession:
    """Manages one benchmark run. The agent drives it step by step."""

    def __init__(self, suite_name: str, results_dir: str | Path = "results",
                 notes: str = ""):
        if suite_name not in ALL_SUITES:
            raise KeyError(f"Unknown suite '{suite_name}'. Available: {list(ALL_SUITES.keys())}")

        self.suite_name = suite_name
        self.scenarios = get_suite(suite_name)
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.metrics = MetricsCalculator()

        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{suite_name}"
        self._results: dict[str, ResultRecord] = {}
        self._started_at = time.time()
        self._started_at_iso = datetime.now(timezone.utc).isoformat()
        self._finished = False
        self.notes = notes

        self._save_state("running")

    # ── Agent-facing API ───────────────────────────────────────────

    def pending(self) -> list[Scenario]:
        """Return scenarios the agent still needs to execute.

        The agent calls this to know what to search for next.
        """
        return [s for s in self.scenarios if s.id not in self._results]

    def pending_count(self) -> int:
        return len(self.pending())

    def done_count(self) -> int:
        return len(self._results)

    def total_count(self) -> int:
        return len(self.scenarios)

    def progress(self) -> str:
        """Human-readable progress string like '7/12 (58%)'."""
        return f"{self.done_count()}/{self.total_count()} ({self.done_count()/self.total_count():.0%})"

    def record(self, scenario_id: str, result: ResultRecord):
        """Feed an MCP result back into the session.

        The agent calls this immediately after receiving the MCP tool response.
        """
        if scenario_id not in {s.id for s in self.scenarios}:
            raise KeyError(f"Unknown scenario '{scenario_id}' for suite '{self.suite_name}'")

        if scenario_id in self._results:
            raise ValueError(f"Scenario '{scenario_id}' already recorded. Use replace() to overwrite.")

        # Look up the original scenario for scoring context
        scenario = next(s for s in self.scenarios if s.id == scenario_id)

        # Store the record
        self._results[scenario_id] = result

        # Persist after each record so we never lose progress
        self._save_state("running")

        return self._quick_score(scenario, result)

    def record_batch(self, results: dict[str, ResultRecord]):
        """Feed multiple results at once. Keyed by scenario_id."""
        for sid, result in results.items():
            self.record(sid, result)

    def replace(self, scenario_id: str, result: ResultRecord):
        """Overwrite a previously recorded result. Use for retries."""
        if scenario_id not in {s.id for s in self.scenarios}:
            raise KeyError(f"Unknown scenario '{scenario_id}'")
        self._results[scenario_id] = result
        self._save_state("running")

    def get_result(self, scenario_id: str) -> Optional[ResultRecord]:
        """Retrieve a previously recorded result."""
        return self._results.get(scenario_id)

    def finish(self) -> SuiteReport:
        """Mark the session complete and generate the final report.

        Returns the SuiteReport so the agent can display it.
        """
        if self._finished:
            # Already finished, just reload the report
            return self._build_report()

        # Ensure all scenarios have been attempted
        missing = self.pending()
        if missing:
            # Auto-record failures for unexecuted scenarios
            for s in missing:
                self._results[s.id] = ResultRecord(
                    success=False,
                    latency_ms=0,
                    error_message="not executed by agent",
                    error_type="not_executed",
                )

        self._finished = True
        self._save_state("finished")

        report = self._build_report()

        # Save reports
        from searchbench.reporter import Reporter
        reporter = Reporter(output_dir=self.results_dir)
        reporter.save_session_report(report, self.session_id)

        return report

    # ── Internal ───────────────────────────────────────────────────

    def _quick_score(self, scenario: Scenario, result: ResultRecord) -> dict:
        """Return a quick scoring summary for the agent to see immediately."""
        qr = QueryResult(
            scenario_id=scenario.id,
            query=scenario.query,
            category=self.suite_name,
            success=result.success,
            latency_ms=result.latency_ms,
            result_count=result.result_count,
            result_urls=result.result_urls,
            result_snippets=result.result_snippets,
            error_message=result.error_message,
            error_type=result.error_type,
        )
        return {
            "relevance": round(MetricsCalculator.compute_relevance(scenario.query, qr), 3),
            "diversity": round(MetricsCalculator.compute_diversity(qr), 3),
            "result_count": result.result_count,
            "latency_ms": result.latency_ms,
        }

    def _build_report(self) -> SuiteReport:
        """Build a SuiteReport from all recorded results."""
        query_results = []
        for scenario in self.scenarios:
            rr = self._results.get(scenario.id)
            if rr is None:
                rr = ResultRecord(success=False, latency_ms=0,
                                  error_message="not executed", error_type="not_executed")
            qr = QueryResult(
                scenario_id=scenario.id,
                query=scenario.query,
                category=self.suite_name,
                success=rr.success,
                latency_ms=rr.latency_ms,
                result_count=rr.result_count,
                result_urls=rr.result_urls,
                result_snippets=rr.result_snippets,
                error_message=rr.error_message,
                error_type=rr.error_type,
            )
            query_results.append(qr)

        duration = time.time() - self._started_at
        return self.metrics.build_report(self.suite_name, query_results, duration)

    def _save_state(self, status: str):
        """Persist current session state to disk."""
        state = SessionState(
            session_id=self.session_id,
            suite_name=self.suite_name,
            status=status,
            results={sid: asdict(r) for sid, r in self._results.items()},
            started_at=self._started_at_iso,
            finished_at=datetime.now(timezone.utc).isoformat() if status == "finished" else "",
            notes=self.notes,
        )
        state.set_scenario_ids([s.id for s in self.scenarios])

        state_path = self.results_dir / f"session_{self.session_id}.json"
        # Only serialize the known fields
        d = {
            "session_id": state.session_id,
            "suite_name": state.suite_name,
            "status": state.status,
            "results": state.results,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "notes": state.notes,
        }
        state_path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Class methods ──────────────────────────────────────────────

    @classmethod
    def resume(cls, session_id: str, results_dir: str | Path = "results") -> "BenchmarkSession":
        """Resume an existing session from disk."""
        results_dir = Path(results_dir)
        state_path = results_dir / f"session_{session_id}.json"
        if not state_path.exists():
            raise FileNotFoundError(f"Session file not found: {state_path}")

        data = json.loads(state_path.read_text(encoding="utf-8"))
        session = cls(suite_name=data["suite_name"], results_dir=results_dir,
                      notes=data.get("notes", ""))
        session.session_id = data["session_id"]
        session._started_at_iso = data.get("started_at", "")
        session._finished = data.get("status") == "finished"

        for sid, rd in data.get("results", {}).items():
            session._results[sid] = ResultRecord(
                success=rd.get("success", False),
                latency_ms=rd.get("latency_ms", 0),
                result_count=rd.get("result_count", 0),
                result_urls=rd.get("result_urls", []),
                result_snippets=rd.get("result_snippets", []),
                error_message=rd.get("error_message", ""),
                error_type=rd.get("error_type", ""),
            )

        return session

    @classmethod
    def list_sessions(cls, results_dir: str | Path = "results") -> list[dict]:
        """List all saved sessions."""
        results_dir = Path(results_dir)
        if not results_dir.exists():
            return []

        sessions = []
        for f in sorted(results_dir.glob("session_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data.get("session_id", f.stem),
                    "suite": data.get("suite_name", "?"),
                    "status": data.get("status", "?"),
                    "done": len(data.get("results", {})),
                    "started": data.get("started_at", "")[:16],
                })
            except Exception:
                sessions.append({"session_id": f.stem, "suite": "?", "status": "error", "done": 0, "started": ""})
        return sessions


# ── Multi-suite runner ────────────────────────────────────────────

def run_all_suites(results_dir: str | Path = "results",
                   suites: list[str] | None = None) -> dict[str, SuiteReport]:
    """Convenience: run all suites sequentially and return reports.

    This is a synchronous helper — the agent can also run suites
    one by one with more control using BenchmarkSession directly.
    """
    suite_names = suites or list(ALL_SUITES.keys())
    reports = {}

    for name in suite_names:
        session = BenchmarkSession(name, results_dir=results_dir)
        # Note: This won't actually execute MCP calls.
        # The agent must call record() for each pending scenario.
        # This function is a placeholder for programmatic orchestration.
        reports[name] = None  # Will be populated after agent finishes

    return reports
