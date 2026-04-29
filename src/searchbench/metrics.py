"""Metrics — pure scoring functions and data structures. No I/O, no async."""

import time
from dataclasses import dataclass, field


@dataclass
class QueryResult:
    """Result from a single search query execution."""

    scenario_id: str
    query: str
    category: str
    success: bool
    latency_ms: float
    result_count: int = 0
    result_urls: list[str] = field(default_factory=list)
    result_snippets: list[str] = field(default_factory=list)
    error_message: str = ""
    error_type: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "query": self.query,
            "category": self.category,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "result_count": self.result_count,
            "result_urls": self.result_urls,
            "result_snippets": self.result_snippets,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "timestamp": self.timestamp,
        }


@dataclass
class SuiteReport:
    """Aggregated report for a test suite run."""

    suite_name: str
    total_queries: int = 0
    successful: int = 0
    failed: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    avg_result_count: float = 0.0
    avg_relevance: float = 0.0
    avg_diversity: float = 0.0
    error_types: dict[str, int] = field(default_factory=dict)
    results: list[QueryResult] = field(default_factory=list)
    duration_sec: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successful / self.total_queries if self.total_queries else 0.0

    @property
    def error_rate(self) -> float:
        return self.failed / self.total_queries if self.total_queries else 0.0


class MetricsCalculator:
    """Pure scoring functions. No state, no I/O."""

    # English stop words for relevance scoring
    STOP_WORDS = {
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "and",
        "or", "is", "are", "was", "were", "be", "been", "being", "have",
        "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "with", "from", "by",
        "about", "as", "into", "through", "during", "before", "after",
        "above", "below", "between", "under", "again", "further", "then",
        "once", "here", "there", "when", "where", "why", "how", "all",
        "both", "each", "few", "more", "most", "other", "some", "such",
        "no", "nor", "not", "only", "own", "same", "so", "than", "too",
        "very", "just", "because", "now",
    }

    @staticmethod
    def compute_relevance(query: str, result: QueryResult) -> float:
        """Score relevance by keyword overlap between query terms and result snippets.

        Returns [0, 1]. Does NOT require the original scenario — works purely from
        the query string and the ResultRecord.
        """
        if not result.success or result.result_count == 0:
            return 0.0

        query_terms = set(query.lower().split()) - MetricsCalculator.STOP_WORDS
        if not query_terms:
            return 0.5

        scores = []
        for snippet in result.result_snippets:
            snippet_lower = snippet.lower()
            matches = sum(1 for t in query_terms if t in snippet_lower)
            scores.append(matches / len(query_terms))

        return sum(scores) / len(scores) if scores else 0.0

    @staticmethod
    def compute_diversity(result: QueryResult) -> float:
        """Score domain diversity. 1.0 = all unique domains."""
        if not result.success or result.result_count <= 1:
            return 0.0

        from urllib.parse import urlparse

        domains = set()
        for url in result.result_urls:
            try:
                domain = urlparse(url).netloc.replace("www.", "")
                domains.add(domain)
            except Exception:
                domains.add(url)

        return len(domains) / len(result.result_urls) if domains else 0.0

    @staticmethod
    def categorize_error(error_message: str) -> str:
        """Classify an error string into a type."""
        msg = error_message.lower()
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        if "rate" in msg and ("limit" in msg or "exceeded" in msg):
            return "rate_limit"
        if "connection" in msg or "refused" in msg or "unreachable" in msg:
            return "connection"
        if "parse" in msg or "malformed" in msg or "json" in msg:
            return "parse_error"
        if "empty" in msg or "no result" in msg:
            return "empty_result"
        if "not executed" in msg:
            return "not_executed"
        return "unknown"

    @staticmethod
    def compute_percentiles(latencies: list[float]) -> dict[str, float]:
        """Return p50, p95, p99 from a list of latencies."""
        if not latencies:
            return {"p50": 0, "p95": 0, "p99": 0}

        sorted_lats = sorted(latencies)
        n = len(sorted_lats)

        def _pct(p: float) -> float:
            k = (p / 100) * (n - 1)
            f = int(k)
            c = k - f
            if f + 1 < n:
                return sorted_lats[f] + c * (sorted_lats[f + 1] - sorted_lats[f])
            return sorted_lats[f]

        return {"p50": _pct(50), "p95": _pct(95), "p99": _pct(99)}

    @classmethod
    def build_report(cls, suite_name: str, results: list[QueryResult],
                     duration_sec: float) -> SuiteReport:
        """Aggregate QueryResults into a SuiteReport."""
        report = SuiteReport(suite_name=suite_name, duration_sec=duration_sec)
        report.results = results
        report.total_queries = len(results)

        latencies = []
        total_relevance = 0.0
        total_diversity = 0.0
        total_result_count = 0

        for r in results:
            if r.success:
                report.successful += 1
                latencies.append(r.latency_ms)
                total_result_count += r.result_count
                total_relevance += cls.compute_relevance(r.query, r)
                total_diversity += cls.compute_diversity(r)
            else:
                report.failed += 1
                err_type = r.error_type or cls.categorize_error(r.error_message)
                report.error_types[err_type] = report.error_types.get(err_type, 0) + 1

        if latencies:
            report.min_latency_ms = min(latencies)
            report.max_latency_ms = max(latencies)
            report.avg_latency_ms = sum(latencies) / len(latencies)
            report.total_latency_ms = sum(latencies)
            p = cls.compute_percentiles(latencies)
            report.p50_latency_ms = p["p50"]
            report.p95_latency_ms = p["p95"]
            report.p99_latency_ms = p["p99"]
        else:
            report.min_latency_ms = 0

        if report.successful > 0:
            report.avg_result_count = total_result_count / report.successful
            report.avg_relevance = total_relevance / report.successful
            report.avg_diversity = total_diversity / report.successful

        return report

    @staticmethod
    def check_thresholds(report: SuiteReport,
                         thresholds: dict | None = None) -> list[dict]:
        """Check a report against thresholds. Returns list of failures.

        Each failure: {"metric": "...", "label": "...", "value": 0.0,
                        "threshold": 0.0, "comparison": "gte"}
        """
        if thresholds is None:
            thresholds = {
                "success_rate":     {"label": "Success rate",    "value": 0.90, "cmp": "gte"},
                "error_rate":       {"label": "Error rate",      "value": 0.05, "cmp": "lte"},
                "avg_latency_ms":   {"label": "Avg latency",     "value": 5000, "cmp": "lte"},
                "avg_result_count": {"label": "Avg result count","value": 3,    "cmp": "gte"},
                "avg_relevance":    {"label": "Avg relevance",   "value": 0.60, "cmp": "gte"},
            }

        failures = []
        for metric, spec in thresholds.items():
            actual = getattr(report, metric, None)
            if actual is None:
                continue
            cmp = spec["cmp"]
            ok = False
            if cmp == "gte":
                ok = actual >= spec["value"]
            elif cmp == "lte":
                ok = actual <= spec["value"]

            if not ok:
                failures.append({
                    "metric": metric,
                    "label": spec["label"],
                    "value": actual,
                    "threshold": spec["value"],
                    "comparison": cmp,
                })

        return failures
