"""CLI — listing suites, checking session status, viewing history.

The agent does the actual MCP execution. This CLI is for
utility commands: inspect scenarios, check session progress, view reports.
"""

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from searchbench import __version__
from searchbench.scenario import ALL_SUITES, SUITE_META, get_suite
from searchbench.session import BenchmarkSession

console = Console(force_terminal=True, legacy_windows=False)


@click.group()
@click.version_option(__version__)
def main():
    """SearchBench — agent-driven MCP benchmark toolkit."""


# ── suites ────────────────────────────────────────────────────────

@main.command()
@click.argument("suite_name", required=False)
def suites(suite_name: str | None):
    """List available suites, or show scenarios in a specific suite."""
    if suite_name:
        if suite_name not in ALL_SUITES:
            console.print(f"[red]Unknown suite '{suite_name}'.[/]")
            console.print(f"Available: {', '.join(ALL_SUITES.keys())}")
            sys.exit(1)

        scenarios = get_suite(suite_name)
        meta = SUITE_META[suite_name]
        console.print(f"\n[bold]{meta['name']}[/] — {meta['description']}")
        console.print(f"Total: {len(scenarios)} scenarios\n")

        table = Table(title=f"Scenarios in '{suite_name}'")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Query")
        table.add_column("Expected Keywords")
        table.add_column("Lang")

        for s in scenarios:
            table.add_row(
                s.id, s.name, s.query[:60], ", ".join(s.expected_keywords[:3]), s.language
            )
        console.print(table)

    else:
        table = Table(title="Available Test Suites")
        table.add_column("Suite", style="cyan")
        table.add_column("Scenarios")
        table.add_column("Description")

        for name, meta in SUITE_META.items():
            table.add_row(name, str(meta["scenario_count"]), meta["description"])

        console.print(table)
        console.print("\n[dim]Use 'searchbench suites <name>' to see individual scenarios.[/]")


# ── session ───────────────────────────────────────────────────────

@main.group()
def session():
    """Manage benchmark sessions (init, status, list)."""


@session.command("init")
@click.argument("suite_name")
@click.option("--output-dir", "-o", default="results")
@click.option("--notes", "-n", default="")
def session_init(suite_name: str, output_dir: str, notes: str):
    """Start a new benchmark session for SUITE_NAME.

    Prints the session_id so the agent can reference it.
    """
    if suite_name not in ALL_SUITES:
        console.print(f"[red]Unknown suite '{suite_name}'.[/]")
        console.print(f"Available: {', '.join(ALL_SUITES.keys())}")
        sys.exit(1)

    s = BenchmarkSession(suite_name, results_dir=output_dir, notes=notes)
    scenarios = get_suite(suite_name)

    console.print(f"[green]Session started:[/] {s.session_id}")
    console.print(f"Suite: {suite_name} — {len(scenarios)} scenarios pending")
    console.print(f"")
    console.print(f"[bold]Agent workflow:[/]")
    console.print(f"  1. Load session:  session = BenchmarkSession('{suite_name}')")
    console.print(f"  2. Get pending:   session.pending()  → {len(scenarios)} scenarios")
    console.print(f"  3. For each scenario, call MCP, then:")
    console.print(f"     session.record(scenario.id, ResultRecord.from_mcp_output(...))")
    console.print(f"  4. Finish:        session.finish()  → saves report")


@session.command("status")
@click.argument("session_id")
@click.option("--output-dir", "-o", default="results")
def session_status(session_id: str, output_dir: str):
    """Show progress of a session."""
    s = BenchmarkSession.resume(session_id, results_dir=output_dir)

    done = s.done_count()
    total = s.total_count()
    pending = s.pending()

    console.print(f"[bold]Session:[/] {session_id}")
    console.print(f"Suite: {s.suite_name}")
    console.print(f"Progress: {done}/{total} ({done/total:.0%})" if total else "Progress: N/A")
    console.print(f"")

    if pending:
        console.print(f"[yellow]{len(pending)} pending:[/]")
        for p in pending[:10]:
            console.print(f"  [{p.id}] {p.query[:60]}")
        if len(pending) > 10:
            console.print(f"  ... and {len(pending) - 10} more")
    else:
        console.print("[green]All scenarios executed![/]")
        console.print("Run [bold]session.finish()[/] to generate the report.")


@session.command("list")
@click.option("--output-dir", "-o", default="results")
def session_list(output_dir: str):
    """List all saved sessions."""
    sessions = BenchmarkSession.list_sessions(output_dir)
    if not sessions:
        console.print("[yellow]No sessions found.[/]")
        return

    table = Table(title="Saved Sessions")
    table.add_column("Session ID", style="cyan")
    table.add_column("Suite")
    table.add_column("Status")
    table.add_column("Done")
    table.add_column("Started")

    for s in sessions[:20]:
        status_color = "green" if s["status"] == "finished" else "yellow"
        table.add_row(
            s["session_id"],
            s["suite"],
            f"[{status_color}]{s['status']}[/]",
            str(s["done"]),
            s["started"],
        )

    console.print(table)


# ── history ───────────────────────────────────────────────────────

@main.command()
@click.option("--output-dir", "-o", default="results")
def history(output_dir: str):
    """Show historical report results."""
    results_dir = Path(output_dir)
    if not results_dir.exists():
        console.print(f"[red]No results directory found.[/]")
        return

    json_files = sorted(results_dir.glob("report_*.json"), reverse=True)
    if not json_files:
        console.print("[yellow]No reports found.[/]")
        return

    table = Table(title="Benchmark History")
    table.add_column("Session ID", style="cyan")
    table.add_column("Suite")
    table.add_column("Success")
    table.add_column("Avg Lat")
    table.add_column("P95 Lat")

    for f in json_files[:20]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sid = data.get("session_id", f.stem.replace("report_", ""))
            suite = data.get("suite", "?")
            sr = f"{data.get('success_rate', 0):.1%}"
            avg_lat = f"{data.get('avg_latency_ms', 0):.0f}ms"
            p95 = f"{data.get('p95_latency_ms', 0):.0f}ms"
            table.add_row(sid, suite, sr, avg_lat, p95)
        except Exception:
            pass

    console.print(table)


# ── run (agent simulation) ────────────────────────────────────────

@main.command()
@click.argument("suite_name")
@click.option("--output-dir", "-o", default="results")
def quickstart(suite_name: str, output_dir: str):
    """Print agent-ready instructions for running a suite.

    This does NOT execute the MCP — it prints the exact Python code
    the agent needs to follow.
    """
    if suite_name not in ALL_SUITES:
        console.print(f"[red]Unknown suite '{suite_name}'.[/]")
        sys.exit(1)

    scenarios = get_suite(suite_name)
    console.print(f"[bold]Quickstart for '{suite_name}'[/] — {len(scenarios)} scenarios\n")
    console.print("Copy-paste into your agent context:\n")
    console.print("[dim]#─── Agent instructions ───[/]")
    console.print(f"from searchbench.session import BenchmarkSession, ResultRecord")
    console.print(f"")
    console.print(f"session = BenchmarkSession('{suite_name}')")
    console.print(f"for s in session.pending():")
    console.print(f"    # Call your MCP tool with s.query")
    console.print(f"    result = ...  # MCP response")
    console.print(f"    session.record(s.id, ResultRecord.from_mcp_output(")
    console.print(f"        success=..., latency_ms=..., results=..., error=...")
    console.print(f"    ))")
    console.print(f"    print(f'{{session.progress()}} {{s.id}} recorded')")
    console.print(f"")
    console.print(f"report = session.finish()")
    console.print(f"print(f'Done. Success rate: {{report.success_rate:.1%}}')")
    console.print("[dim]#─────────────────────────[/]")


if __name__ == "__main__":
    main()
