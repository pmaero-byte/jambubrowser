# Evaluation Harness

> **⚠️ Disclaimer — read before quoting any numbers from this harness.**
> The 9 suites shipped here (`smoke`, `gaia`, `gaia-mini`, `webarena-mini`,
> `webshop`, `swebench`, `memory`, `privacy`, `alfworld` — 77 tasks total)
> are **inspired-by, self-contained QA pairs graded with fuzzy/substring
> matching**. They are **NOT** the official GAIA, WebArena, SWE-bench,
> WebShop, or ALFWorld benchmarks, and results from this harness are **not
> comparable** to published leaderboard numbers for those benchmarks. Never
> present a score from this harness as a GAIA/WebArena/SWE-bench result.

A lightweight benchmark framework for measuring Jambubrowser's research-agent
quality across LLM providers. Inspired by GAIA (multi-step reasoning) and
WebArena (browser-based task completion) but built as small, self-contained
suites that run in minutes, not days.

## Quick Start

```bash
# List all available suites and tasks
python -m backend.eval list

# Run the smoke suite (5 tasks, ~30s) on any provider
python -m backend.eval run --suite smoke --provider mock

# Compare providers on the same suite
python -m backend.eval compare --suite smoke \
  --providers mock,ollama,anthropic,openai

# View past runs
python -m backend.eval report --limit 20

# Get a markdown report for a specific run
python -m backend.eval report --run-id <id> --format markdown

# Get a JSON report
python -m backend.eval report --run-id <id> --format json
```

## Suite Catalog

| Suite | Tasks | Description |
|-------|-------|-------------|
| `smoke` | 5 | Fast sanity check (~30s). Use as a CI gate. |
| `gaia-mini` | 10 | Multi-step reasoning, arithmetic, common sense. |
| `gaia` | 12 | GAIA-style factual lookup / reasoning (inspired-by, not official). |
| `webarena-mini` | 8 | Browser-based task completion via the agent loop. |
| `webshop` | 11 | Shopping-task-inspired tool-use QA pairs. |
| `swebench` | 10 | Software-engineering-inspired QA pairs (not the real SWE-bench). |
| `alfworld` | 9 | Household-task-inspired instruction QA pairs. |
| `privacy` | 7 | PII redaction + prompt injection resistance. |
| `memory` | 5 | v3 memory system: recall, procedural, store+recall round-trip. |

## Task Format

Each task is defined as:

```python
register_task(Task(
    id="gaia.q2.arithmetic",       # unique id
    suite="gaia-mini",
    prompt="If I have 3 apples...",  # the user query
    expected=["56", "fifty-six"],     # acceptable answers (any match → pass)
    category="qa",                   # qa | research | browser | memory | privacy
    difficulty=1,                    # 1-5
    timeout_seconds=10,
    use_agent=False,                 # True = full ReAct loop
    max_steps=5,                     # for agent mode
    system="Think step by step.",    # optional system prompt
    grader=None,                     # optional custom grader
))
```

## Metrics

Built-in metrics (in `backend.eval.metrics`):

| Metric | Use case |
|--------|----------|
| `exact_match` | Strict equality (case-insensitive, stripped) |
| `contains_match` | Substring presence (any expected value) |
| `fuzzy_match` | Word-overlap partial credit |
| `number_match` | Extract numbers from answer, compare to expected |
| `email_redaction_match` | Verify no email pattern remains |

Default grader is `contains_match` (case-insensitive, partial-credit fallback).

## Architecture

```
backend/eval/
├── __init__.py            # Public API
├── __main__.py            # `python -m backend.eval` entrypoint
├── harness.py             # Core: Task, TaskResult, SuiteResult, Harness
├── metrics.py             # Built-in grading metrics
├── store.py               # SQLite-backed results storage
├── report.py              # Markdown + JSON report generation
├── cli.py                 # argparse-based CLI
├── runners/               # (extensible) different execution modes
└── tasks/
    ├── smoke.py           # 5 fast sanity tasks
    ├── gaia_mini.py       # 10 reasoning tasks
    ├── gaia.py            # 12 GAIA-inspired tasks
    ├── webarena_mini.py   # 8 browser tasks
    ├── webshop.py         # 11 shopping-inspired tasks
    ├── swebench.py        # 10 SWE-inspired tasks
    ├── alfworld.py        # 9 household-inspired tasks
    ├── privacy.py         # 7 PII/injection tasks
    └── memory.py          # 5 memory layer tasks
```

## Extending

Add a new task:

```python
# In backend/eval/tasks/my_suite.py
from ..harness import Task, register_task

register_task(Task(
    id="mysuite.q1.example",
    suite="my-suite",
    prompt="What is the meaning of life?",
    expected=["42"],
    category="qa",
    difficulty=2,
    use_agent=False,
))
```

Tasks are auto-discovered via the `tasks/__init__.py` import chain.

Add a new metric:

```python
# In backend/eval/metrics.py
def my_metric(answer: str, expected) -> float:
    """Returns 0.0-1.0 score."""
    return 1.0 if expected in answer else 0.0

ALL_METRICS["my_metric"] = my_metric
```

## Real-Provider Example

```bash
# With Anthropic configured
export ANTHROPIC_API_KEY="sk-ant-..."
python -m backend.eval compare \
  --suite gaia-mini \
  --providers mock,ollama,anthropic \
  --out reports/gaia-mini-comparison.md
```

Sample output (representative):

| Provider | Model | Pass | Total | Success | Tokens | Cost | Avg Time |
|----------|-------|------|-------|---------|--------|------|----------|
| mock | mock-echo | 2 | 10 | 20% | 350 | $0.00 | 5ms |
| ollama | gemma4:12b-it-qat | 6 | 10 | 60% | 8,200 | $0.00 | 4,200ms |
| anthropic | claude-sonnet-4-6 | 9 | 10 | 90% | 12,400 | $0.04 | 2,800ms |

## CI Integration

`test.yml` (already in `.github/workflows/`) runs the smoke suite on every
push. To gate PRs on a minimum pass rate:

```yaml
- name: Run smoke eval
  run: |
    python -m backend.eval run --suite smoke --provider mock \
      --min-pass-rate 0.5
```

## Limitations

- The included `*-mini` suites are small (5-10 tasks each) and meant to
  run in CI. For serious benchmarks, use the full GAIA or WebArena
  datasets (hundreds of tasks, hours of runtime).
- Mock provider scores are essentially zero on knowledge tasks; it's
  only useful for harness validation.
- The `webarena-mini` suite uses self-contained prompts that exercise
  the agent's tool-use paths without requiring real live websites.

## Roadmap

- [ ] Full GAIA dataset loader
- [ ] Full WebArena dataset loader (with sandboxed browsers)
- [ ] Live website integration (against local test sites)
- [ ] Per-category metrics breakdown
- [ ] Leaderboard-style HTML report
- [ ] Web UI for browsing past runs
