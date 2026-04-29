# SearchBench — WebSearch MCP Benchmark & Validation Suite

Production-grade test harness for comprehensively evaluating a websearch MCP's
capabilities, correctness, and stability.

## Quick Start

```bash
# Create venv
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows

# Install
pip install -e ".[dev]"

# Run all benchmarks (mock backend)
searchbench run

# Run specific suites
searchbench run --suite sci_research --suite code_intel

# List all suites
searchbench suites

# View history
searchbench history
```

## Test Suites

| Suite | Purpose | Count |
|-------|---------|-------|
| **sci_research** | Academic queries, paper discovery, cross-domain research | 12 scenarios |
| **code_intel** | API docs, error debugging, library comparison | 15 scenarios |
| **concurrency** | Parallel execution, rate limiting, rapid-fire sequences | 5 batches + 3 rapid-fire |
| **stability** | Malformed inputs, Unicode, injection attempts, recovery | 23 scenarios |
| **longevity** | Multi-hour soak, cache behavior, freshness tracking | 3 sessions |

## What Each Suite Tests

### Sci Research
- Multi-domain paper discovery (life sciences, AI, physics, materials)
- Chinese-language academic queries
- Expected domain validation (nature.com, arxiv.org, cell.com, etc.)
- Keyword coverage scoring

### Code Intelligence
- API documentation lookups (FastAPI, React, Rust, PostgreSQL)
- Error message debugging (asyncio, Docker OOM, K8s CrashLoopBackOff)
- Library comparison queries
- Chinese dev community resources

### Concurrency
- 5/10/20 parallel queries
- Mixed Chinese-English parallel requests
- Same-query hammer (cache/dedup test)
- Rapid-fire: 5 queries in 1s, 30 queries in 6s
- Burst mode to test rate limiting

### Stability
- Empty/whitespace/single-char queries
- Emoji, Unicode math, RTL text, CJK mix
- Very long queries (500-2000 chars)
- Prompt injection / jailbreak attempts
- Recovery: normal queries after errors

### Longevity
- Cache behavior: fast-changing vs stable vs niche queries
- Freshness at 5/10/15/30/60 minute intervals
- Soak tests: 5min, 30min, 2hr sessions
- Detecting memory leaks and gradual degradation

## Output

Reports are saved to `results/`:

- `report_YYYYMMDD_HHMMSS.md` — Markdown report with pass/fail indicators
- `report_YYYYMMDD_HHMMSS.json` — Machine-readable full data
- `report_latest.md` / `report_latest.json` — Always points to newest run

### Metrics Collected

| Metric | Description |
|--------|-------------|
| Success rate | % of queries that returned results |
| Latency (avg, min, max, P50, P95, P99) | Response time distribution |
| Result count | How many results per query |
| Relevance score | Keyword overlap between query and results (0-1) |
| Diversity score | Unique domains / total results (0-1) |
| Error type breakdown | timeout, rate_limit, connection, parse_error, etc. |

## Configuration

Edit `config.yaml` to set:

- Backend type and connection parameters
- Execution parameters (concurrency, delays, retries)
- Pass/fail thresholds for each metric
- Report output settings

## Running Against a Real MCP

With the mock backend (default), results are simulated. To test against your
actual websearch MCP:

1. Set `backend.type: claude_code` in config.yaml (or pass `--no-mock`)
2. Run the scenarios inside Claude Code where your MCP tools are available
3. Claude Code invokes the MCP tools for each query and feeds results to the engine

## Project Structure

```
websearchmcp_testfield/
├── pyproject.toml
├── config.yaml
├── README.md
├── src/searchbench/
│   ├── __init__.py
│   ├── engine.py          # Test orchestrator
│   ├── metrics.py         # Scoring & aggregation
│   ├── reporter.py        # JSON/Markdown reports
│   ├── cli.py             # searchbench CLI
│   └── scenarios/
│       ├── __init__.py
│       ├── sci_research.py
│       ├── code_intel.py
│       ├── concurrency.py
│       ├── stability.py
│       └── longevity.py
├── tests/
│   └── test_scenarios.py
└── results/               # Generated reports
```
