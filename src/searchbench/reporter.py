"""Report generation — Markdown + JSON from session results."""

import json
from datetime import datetime, timezone
from pathlib import Path

from searchbench.metrics import SuiteReport, MetricsCalculator


class Reporter:
    """Generates reports from benchmark sessions."""

    def __init__(self, output_dir: str | Path = "results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Single-suite report (primary path) ────────────────────────

    def save_session_report(self, report: SuiteReport, session_id: str) -> dict:
        """Save a single-suite report. Returns paths."""
        markdown = self._render_session_markdown(report, session_id)
        json_str = self._render_session_json(report, session_id)

        md_path = self.output_dir / f"report_{session_id}.md"
        json_path = self.output_dir / f"report_{session_id}.json"

        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(json_str, encoding="utf-8")

        # Also write "latest" copy
        (self.output_dir / "report_latest.md").write_text(markdown, encoding="utf-8")
        (self.output_dir / "report_latest.json").write_text(json_str, encoding="utf-8")

        return {"markdown": str(md_path), "json": str(json_path)}

    def _render_session_markdown(self, report: SuiteReport, session_id: str) -> str:
        """Render a single suite report as Markdown."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            f"# SearchBench Report — {report.suite_name}",
            "",
            f"**Session:** {session_id}",
            f"**Date:** {now}",
            f"**Duration:** {report.duration_sec:.1f}s",
            "",
            "---",
            "",
            "## Summary",
            "",
            f"| Metric | Value | Threshold | Status |",
            f"|--------|-------|-----------|--------|",
        ]

        checks = {
            "success_rate":     ("Success rate",     f"{report.success_rate:.1%}",  0.90, "gte"),
            "error_rate":       ("Error rate",       f"{report.error_rate:.1%}",  0.05, "lte"),
            "avg_latency_ms":   ("Avg latency",      f"{report.avg_latency_ms:.0f}ms", 5000, "lte"),
            "avg_result_count": ("Avg result count", f"{report.avg_result_count:.1f}",  3, "gte"),
            "avg_relevance":    ("Avg relevance",    f"{report.avg_relevance:.2f}",    0.60, "gte"),
        }

        for metric, (label, display, threshold, cmp) in checks.items():
            actual = getattr(report, metric)
            if cmp == "gte":
                ok = actual >= threshold
            else:
                ok = actual <= threshold
            icon = "[PASS]" if ok else "[FAIL]"
            lines.append(f"| {label} | {display} | {cmp} {threshold} | {icon} |")

        lines.append(f"| Min latency | {report.min_latency_ms:.0f}ms | — | — |")
        lines.append(f"| Max latency | {report.max_latency_ms:.0f}ms | — | — |")
        lines.append(f"| P50 latency | {report.p50_latency_ms:.0f}ms | — | — |")
        lines.append(f"| P95 latency | {report.p95_latency_ms:.0f}ms | — | — |")
        lines.append(f"| P99 latency | {report.p99_latency_ms:.0f}ms | — | — |")
        lines.append(f"| Avg diversity | {report.avg_diversity:.2f} | — | — |")
        lines.append("")

        # Error breakdown
        if report.error_types:
            lines.append("### Error Breakdown")
            lines.append("")
            for etype, count in sorted(report.error_types.items(), key=lambda x: -x[1]):
                lines.append(f"- **{etype}**: {count}")
            lines.append("")

        # Per-query table
        lines.append("### Per-Query Results")
        lines.append("")
        lines.append("| ID | Name | Status | Latency | Results | Relevance | Diversity |")
        lines.append("|----|------|--------|---------|---------|-----------|-----------|")
        for r in report.results:
            status = "OK" if r.success else "FAIL"
            err = r.error_message[:30] if r.error_message else "-"
            rel = MetricsCalculator.compute_relevance(r.query, r)
            div = MetricsCalculator.compute_diversity(r)
            lines.append(
                f"| {r.scenario_id} | {r.query[:40]} | {status} | "
                f"{r.latency_ms:.0f}ms | {r.result_count} | {rel:.2f} | {div:.2f} |"
            )
        lines.append("")

        return "\n".join(lines)

    def _render_session_json(self, report: SuiteReport, session_id: str) -> str:
        """Render a single suite report as JSON."""
        output = {
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "suite": report.suite_name,
            "total_queries": report.total_queries,
            "successful": report.successful,
            "failed": report.failed,
            "success_rate": report.success_rate,
            "error_rate": report.error_rate,
            "avg_latency_ms": report.avg_latency_ms,
            "min_latency_ms": report.min_latency_ms if report.min_latency_ms != float("inf") else 0,
            "max_latency_ms": report.max_latency_ms,
            "p50_latency_ms": report.p50_latency_ms,
            "p95_latency_ms": report.p95_latency_ms,
            "p99_latency_ms": report.p99_latency_ms,
            "avg_result_count": report.avg_result_count,
            "avg_relevance": report.avg_relevance,
            "avg_diversity": report.avg_diversity,
            "duration_sec": report.duration_sec,
            "error_types": report.error_types,
            "results": [r.to_dict() for r in report.results],
        }
        return json.dumps(output, indent=2, ensure_ascii=False)
