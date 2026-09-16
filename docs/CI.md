# Jambubrowser in CI

Run AI-employee audits (security, performance, accessibility, SEO, UX,
code quality) on every pull request or deploy, gate the build on findings,
and surface results in GitHub code scanning.

The workflow is intentionally thin: the `jambu` CLI drives a Jambubrowser
engine, streams findings as SSE, and writes **SARIF 2.1.0** — the format
GitHub Code Scanning, GitLab Code Quality, Azure DevOps, and the VS Code
SARIF Viewer already understand.

---

## Quick start (GitHub Actions)

1. Add `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) to your repository secrets.
2. Copy [`examples/github-actions/jambu-audit.yml`](../examples/github-actions/jambu-audit.yml)
   to `.github/workflows/jambu-audit.yml`.
3. Push. Findings appear under **Security → Code scanning alerts**, and the
   job fails when a finding at or above `fail-on` exists.

```yaml
- uses: pmaero-byte/jambubrowser@main
  with:
    url: https://staging.example.com
    mode: quick        # quick = 3 employees, full = 6
    fail-on: high      # critical | high | medium | low | none
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

### Action inputs

| Input          | Default            | Description |
|----------------|--------------------|-------------|
| `url`          | — (required)       | Page to audit |
| `mode`         | `quick`            | `quick` (3 employees) or `full` (6 employees) |
| `fail-on`      | `high`             | Fail the job at or above this severity; `none` never fails |
| `engine-url`   | `""`               | Skip installation and use an already-running engine |
| `llm-provider` | `openai`           | Provider for the locally-started engine |
| `sarif-file`   | `jambu-audit.sarif`| Where to write SARIF |
| `upload-sarif` | `true`             | Upload to GitHub code scanning |

### Action outputs

| Output       | Description |
|--------------|-------------|
| `sarif-file` | Path of the SARIF file that was produced |
| `exit-code`  | `0` pass · `1` gate failed · `2` engine error |

---

## Using an existing engine

If you already run the engine (self-hosted runner, sidecar container, or a
remote host), skip installation entirely:

```yaml
- uses: pmaero-byte/jambubrowser@main
  with:
    url: https://staging.example.com
    engine-url: http://127.0.0.1:8001
```

> **Install weight:** starting a fresh engine in the Action installs the
> full stack (FastAPI, sentence-transformers → PyTorch, Chromium: several
> GB and a few minutes). For fast pipelines, run the engine once
> (self-hosted runner, container, or VM) and pass `engine-url`.

The engine itself is a FastAPI app: `python -m uvicorn
backend.engine:app --port 8001`. It needs an LLM provider — env vars are
documented in the [README](../README.md#llm-provider-configuration-v3).

---

## Raw CLI usage (any CI system)

The CLI is the same everywhere; only the exit codes matter for CI:

```bash
pip install -e .
python -m playwright install chromium

# Full audit with all three export formats
jambu audit https://example.com \
  --sarif jambu-audit.sarif \
  --json jambu-audit.json \
  --markdown jambu-report.md \
  --fail-on critical

echo "exit=$?"   # 0 = pass, 1 = gate failed, 2 = engine error
```

`--sarif -`, `--json -`, `--markdown -` write to stdout instead of a file,
so you can pipe results:

```bash
jambu quick https://example.com --json - | jq '.total_findings'
```

### Human-readable reports

After an audit is saved, generate a shareable HTML report (self-contained
and print-friendly — browser → Save as PDF):

```bash
jambu report <audit-id> --out report.html   # or '-' for stdout
jambu share <audit-id>                      # prints JSON + HTML share URLs
```

### Exit codes (stable contract)

| Code | Meaning |
|------|---------|
| `0`  | Audit completed; gate passed (or no `--fail-on`) |
| `1`  | Audit completed; findings at or above `--fail-on` |
| `2`  | Engine unreachable, engine error, or export failure |

### Dismissed findings

Findings dismissed in the app (or via `POST /audit/dismiss`) are filtered
**server-side** before the CLI sees them, and the `done` event reports
`dismissed_count`. CI gates therefore never re-fail on known
false-positives.

---

## Beyond CI: continuous monitoring

CI audits the deploy; **audit monitors** keep watching between deploys:

```bash
jambu monitor add https://app.example.com \
  --interval 60 --fail-on high \
  --webhook https://hooks.slack.com/services/... \
  --run-now
```

Each run diffs active findings against the previous run; alerts fire only
for *new* findings at or above `fail-on` (the first run is a baseline).
Run screenshots are also pixel-diffed, so a layout change past
`--visual-threshold` percent alerts with a separate `audit.visual_change`
webhook event. See the README's "Continuous auditing" section for the CLI
surface and the webhook payload shapes. The classifier pipeline is shared
with `/audit/run`, so dismissals apply to monitors too.

---

## SARIF in other systems

- **GitLab CI:** write `--json` output and convert, or use the SARIF file
  with a Code Quality bridge — the canonical JSON (`content_hash`,
  `by_severity`, `by_employee`) is designed for custom dashboards.
- **Azure DevOps:** `PublishBuildArtifacts` + the *Publish Code Analysis
  Results* task accepts SARIF 2.1.0 directly.
- **VS Code:** open the `.sarif` file with the
  `microsoft.sarif-viewer` extension.

## Cost and latency

- `quick` = 3 LLM calls · `full` = 6 LLM calls per audit (plus one
  product-context call). With `openai`/`anthropic` expect seconds to tens
  of seconds per audit; with a local Ollama/MLX engine it's free but
  slower.
- The engine collects real browser telemetry (Playwright + Chromium), so
  audit the deployed URL — not a local file — and make sure the runner can
  reach it.

## Security notes

- The engine enforces SSRF protection on every URL-accepting endpoint;
  private/loopback targets are rejected. CI URLs must be public or
  explicitly allowed.
- Never print API keys in logs. Pass provider keys via
  `env`/secrets only.
