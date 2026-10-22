# Jambubrowser — Capability Report

*What was built, what it does for agents, what it does for users, and what is
still unverified.*
Written 21 October 2026 against commit `b56d061`.

---

## 1. What Jambubrowser actually is

Jambubrowser is **a local agent platform whose centre of gravity is a
browser** — not a chat app with a browser bolted on, and not a hosted
scraping API with an agent attached.

Three things live on one machine and speak to each other:

1. **A browser** that agents drive (Playwright-backed, accessibility-tree
   addressed, telemetry-collecting, receipt-producing).
2. **A web-data layer** that fetches, maps, crawls, extracts and watches
   pages — the Firecrawl-shaped capability, running locally.
3. **A trust boundary** that decides what page content is allowed to do to
   the model that reads it, and what remote systems are allowed to ask for.

Everything runs on the operator's hardware. There is a local-only mode that
refuses to call a cloud model, and a one-way ingress that refuses to let a
remote system *define* work rather than *request* it.

The product thesis is narrow and falsifiable: **agents browsing the web is a
security problem and a cost problem before it is a capability problem.** A
hosted AI browser solves neither. Jambubrowser is an attempt to solve both,
locally, at zero marginal cost.

---

## 2. The codebase by the numbers

| Layer | Size | Notes |
|---|---|---|
| Backend (Python) | 74,096 lines / 266 files | `backend/` |
| Test suite | 36,949 lines / 131 files | `tests/` |
| Frontend (React 19 + TS) | 24,255 lines | `browser-app/src` |
| Desktop shell (Rust/Tauri) | 5,569 lines | `browser-app/src-tauri` |
| iOS companion (Swift) | 3,694 lines | `ios-app/` |
| **API surface** | **401 endpoints** | `backend/routes/` |
| Agent tools | 17 built-ins | `backend/agent/builtin_tools.py` |
| MCP tool families | 13 | `backend/mcp_tools/` |
| LLM providers | 7 + MoA router + Greenwich bridge | `backend/llm/providers/` |

**Test status (re-measured 21 Oct 2026):** 2,789 passing, 57 failed, 17
errors, 15 skipped. The failure set is **byte-identical to the recorded
baseline** — every failure and error traces to a declared dependency that is
not installed on this machine (`playwright`, `crawl4ai`, `markdownify`,
`sentence-transformers`, `torch`, `solders`, and the network-dependent e2e
and mesh tests). Nothing in the failure set is a behavioural regression.

---

## 3. The five subsystems

### 3.1 The browser (`backend/modules/browser_agent.py`, `browser_flow.py`)

An agent needs to act on a page, not just read it — and needs to be able to
prove what it did.

**Addressing by accessibility tree, not CSS.** `resolve_target()` maps a
target to a live element via the accessibility tree, so a target survives CSS
churn. `ax_role()` exposes the role. This is the difference between an agent
that works on a page and one that breaks when the class names change.

**One call, whole flow.** `browser_test_flow` takes a declarative step list
(navigate / click / type / press / wait / `assert_*`) and returns a compact
pass-fail report with console errors attached. The alternative — a
snapshot/act loop — needs one model round-trip per step *plus* a fresh
snapshot per mutating step, because a loop cannot know that refs changed.

**Token savings are a number, not a claim.** `estimate_primitive_cost()`
charges the primitive loop *generously* (open + close, one act per step, a
fresh snapshot before every step needing refs) and compares it against what
the flow actually cost. The delta is tallied engine-wide and exposed at
`GET /browser/savings`. If a flow's report were bigger than the estimate, the
ledger would show negative savings — it doesn't fake a win.

**Approval gates.** Sessions can require `approve=true`. Selector-addressed
actions are refused without it because they address *unclassified* elements.
Uploads always require it — the agent never sees file contents. Elements
classified as irreversible are refused unless explicitly approved, with the
reason returned rather than a silent block.

**`human_takeover`.** A session can be handed to a person mid-run; agent
actions are then refused with a clear reason.

**Receipts and evidence.** Every step is hash-chained; `receipts()` returns
the step list plus a **Merkle root** and the chain head — a tamper-evident
proof of what the agent did, in a form a third party can verify. Evidence
bundles go further and are signed.

**Telemetry that makes failures diagnosable.** Console errors, failed
requests, bad responses, network events. `classify_step_failure()` splits a
failure three ways — `failed` (the step broke), `inconclusive` (timeout, page
closed, harness error — we do not know), and `blocked` (the security layer
refused). That third category matters: it separates "the app is broken" from
"the guardrail held".

**Determinism.** Clock mocking, network mocking, HAR capture, locale /
timezone / device matrices — so a flaky flow can be reproduced rather than
re-observed.

### 3.2 The untrusted-content boundary (`backend/core/untrusted.py`, `quarantine.py`, `schemas.py`, `observations.py`)

This is the part no competitor ships and the part I would defend hardest.

**Visibility parity.** A hostile page can hide instructions from a human
reviewer using zero-width characters, bidi overrides, `display:none`,
`visibility:hidden`, `opacity:0`, `font-size:0`, the `hidden` attribute,
`aria-hidden`, HTML comments, or `<script>`/`<style>`/`<template>` payloads.
All of it is stripped before the model sees the page. The module is careful
about a subtle case: the hide-test lives *inside* the match, so an outer
visible container cannot swallow a hidden child without that child ever being
inspected.

**Signal, not verdict.** Eight injection patterns (override-instructions,
system-prompt probe, role reassignment, secrecy demand, exfiltration, tool
invocation, and two more) are *reported*, not stripped — removing real content
on a false positive is worse than flagging. Callers can gate on the markers:
require approval, drop a source, raise a security event.

**The honest framing is in the code, not just the docs.** The module states
plainly that it does **not** claim to solve prompt injection: pattern matching
cannot, OWASP ranks it LLM01, the NCSC says it "may never be totally
mitigated", and Brave's own June 2026 research defeated both a cloud model and
a fully on-device one. What it claims is narrower and true: invisible content
is removed, markers are surfaced, and content is fenced.

**Dual-model typed extraction.** `quarantine.py` uses one model to *read*
hostile text and a second, privileged model to receive only the extracted
typed fields. The privileged model never sees the page prose.

**The schema is the enforcement point.** `schemas.py` distinguishes two
classes of field:
- **Control fields** — values that may drive an action. These are restricted
  to `int` / `float` / `bool` / enum. A control field cannot be free text; the
  catalogue raises `ValueError` at schema-definition time.
- **Content fields** — prose, capped.

This resolves the real tension in prompt-injection defence: you cannot have
both "the model may act on extracted values" and "the model may act on
arbitrary text". You can only choose which fields are allowed to be acted on,
and make the choice structural rather than advisory.

**Fail closed, with no raw-text fallback.** `observations.py` routes nine
untrusted tools (`web_search`, `search`, `scrape_url`, `scrape`, `research`,
`browse`, `browser_task`, `browser_session_run`, `browser_test_flow`, `act`,
`browser_act`) through the boundary. If extraction produces nothing usable,
the observation is replaced by an explicit "content quarantined" note — and
**never** by the original prose. Falling back to raw text would defeat the
boundary precisely when the model is behaving unexpectedly, which is exactly
when it matters most.

### 3.3 The instruction channel (`backend/core/ingress.py`)

Exactly one channel may author an instruction: the local operator.

```
may_instruct == (source is a local surface) AND (the peer is loopback)
```

The rule is stated as code, in one dependency-free module that HTTP routes,
MCP and A2A all import — so it is enforced uniformly rather than per-surface.
Authentication is explicitly **not** sufficient: a correctly authenticated
remote caller is still remote, so it still gets data-only. A2A and MCP are
data-only *by architecture*, regardless of credentials. An unknown peer is
treated as REMOTE — failing closed.

### 3.4 The web-data layer (`backend/routes/webdata.py` and modules)

The Firecrawl-shaped capability, local, MIT.

| Endpoint | What it does |
|---|---|
| `POST /v1/scrape` | One URL → markdown / html / links, plus a **receipt** and the transport used |
| `POST /v1/map` | Discover a site's URLs (sitemaps + seed links) |
| `POST /v1/crawl` | Bounded crawl → job id; poll, cancel, list; `sync=true` for small jobs |
| `POST /v1/batch` | Fixed URL list, concurrent, same job shape |
| `POST /v1/extract` | Caller-supplied field schema → typed JSON, via the quarantine boundary |
| `POST /v1/changes/check` | Has this URL changed? Hash + bounded diff |
| `GET /v1/changes`, `DELETE` | Watched URLs / stop watching |
| `GET /v1/cache/stats`, `DELETE /v1/cache` | Cache visibility and eviction |

Supporting machinery: **robots.txt per RFC 9309** (including wildcard
handling), sitemap discovery, a **render policy** (`never` / `auto` / `always`)
where `auto` escalates to the headless browser only when the response looks
like a JavaScript shell, durable **crawl jobs** that survive restarts, **PDF**
text extraction via pypdf, and **change tracking** with hash-plus-diff.

Auth policy is `auto` by default: a key is required from non-loopback peers
only — local development stays frictionless, an exposed port does not. An
unrecognised policy value falls back to `auto`.

### 3.5 Retrieval (`backend/modules/retrieval.py`, `semantic.py`)

Query expansion, dedup, blended ranking. Three backends — lexical (BM25),
dense (LSA or neural embedder), and hybrid via **weighted RRF** with a dense
weight of 0.5.

The weight is not a guess. Equal-weight fusion of a strong lexical ranker
with a weak dense one *hurt* (0.5417 vs 0.7292), so the dense side is
down-weighted until it is actually good enough to earn half the vote.

**Measured on the 16-task local suite (same policy, same grader):**

| Retriever | Mean | 95% CI |
|---|---|---|
| lexical | **0.7292** | [0.5000, 0.9375] |
| dense (LSA) | 0.6153 | [0.3875, 0.8375] |
| hybrid | **0.7292** | [0.5000, 0.9375] |

LSA loses on small corpora — consistent across two corpora, so this is a
property of the method, not a fluke. A neural encoder is the intended fix and
is **not yet measured** (see §7).

### 3.6 The LLM layer (`backend/llm/`)

One `Provider` protocol; nine implementations behind it (Anthropic, OpenAI,
Ollama, MLX, MiniMax, DecentraCode Mesh, Greenwich, Mock, and a
mixture-of-agents router). Routing modes: `auto`, `cheapest`, `fastest`,
`quality`, `fallback`, `local_only`. Per-request cost tracking.

**Local-only is enforced at one choke point.** `registry._resolve_provider()`
raises `LocalOnlyViolation` when `JAMBU_LLM_LOCAL_ONLY` is set and the chosen
provider is not local — it does **not** silently substitute a local model.
That matters for falsifiability: a claim that can be checked by reading one
function is a claim; a claim enforced by scattering `if` statements is a hope.

### 3.7 Memory and learning (`backend/memory/`, `backend/agent/plan_library.py`)

Four memory sub-stores — user profile, session, semantic (embeddings),
procedural (what worked, with success rates). Hybrid retrieval: 60% vector +
30% recency/importance + 10% full-text, with a profile-interest boost.
Per-user scoping, with full forget support.

**The plan library** caches successful plans as templates keyed by a
normalised goal; a near-identical goal (Jaccard overlap ≥ 0.5) gets the
proven template as *advisory* planner context. It is advice, not a script:
the run still verifies every step against the live page, and only fully
successful runs are stored. Procedural memory hints feed the planner the same
way — "this approach failed for you last time" so the planner does not repeat it.

### 3.8 The audit engine (`backend/employees/`)

Six specialist auditors — Security, Performance, UX/UI, Accessibility, SEO,
Code-Quality — each an autonomous "employee" analysing a live page with real
browser telemetry (network requests, console errors, DOM, accessibility tree,
performance metrics). Parallel dispatch with SSE streaming. A
`ProductContextExtractor` infers what the app *does* so findings can be
scored by business impact. Findings route to teams, get assigned, tracked and
resolved.

### 3.9 Privacy, security, and the rest

- **Four privacy modes**: `standard`, `enhanced`, `maximum` (zero external
  calls), `local_only` (no network at all).
- **PII redaction** (email, phone, SSN, card, IP, MAC, passport) and
  **tracking protection** (blocks GA, Facebook Pixel, Mixpanel, …).
- **Credential vault**: AES-256-GCM, PBKDF2 at 480,000 iterations.
- **Tamper-evident audit log**: SHA-256 hash chain over all actions.
- **SSRF protection**: `is_safe_url()` on every URL-accepting endpoint —
  private IPs, DNS rebinding, unsafe schemes.
- **Middleware stack** on every request: AccessLog → RequestID → TrustedHost
  → SecurityHeaders (CSP/HSTS) → RequestTimeout (30 s) → GZip →
  BodySizeLimit (2 MB) → RateLimit.
- **WebSocket hardening**: client id validation, per-IP and global caps, clean
  socket replacement.
- **Supply-chain verification** of Python dependency hashes.
- **Decentralised compute** (`backend/decentralized/`): specs frozen and
  sha256-hashed *before* any node sees them; replicated execution compared
  **numerically** against a per-path median (difflib over solver output is
  not verification); consensus rather than first-responder; failing nodes
  quarantined; `idempotency_key` short-circuits before dispatch so a retry
  cannot be billed twice; signed evidence bundles.

---

## 4. How this helps **agents**

| Agent problem | Jambubrowser's mechanism | Where |
|---|---|---|
| "A hostile page hijacked my task" | Dual-model typed extraction; control fields structurally cannot be free text; fail-closed with no raw fallback | `core/quarantine.py`, `core/schemas.py`, `core/observations.py` |
| "I can't see what the model actually read" | Invisible/bidi/hidden content stripped; injection markers reported, not silently dropped | `core/untrusted.py` |
| "Every step costs me a model round-trip" | One-call declarative flows; savings metered against a deliberately generous primitive-loop estimate | `modules/browser_flow.py` |
| "The page reflows and my selectors break" | Accessibility-tree addressing, not CSS | `modules/browser_agent.py` |
| "I need proof of what I did" | Hash-chained step receipts with a Merkle root; signed evidence bundles | `modules/browser_agent.py` |
| "A failure could be the app, the harness, or a guardrail" | Three-way classification: failed / inconclusive / blocked | `modules/browser_flow.py` |
| "I must not take irreversible actions silently" | `approve=true` gates; irreversible-element classification; uploads always gated; `human_takeover` | `modules/browser_agent.py` |
| "My test is flaky and I can't reproduce it" | Clock/network mocking, HAR capture, locale/timezone/device matrices | `routes/browser_sessions.py` |
| "Remote systems keep trying to make me do things" | One-way ingress: A2A and MCP are data-only by architecture, regardless of auth | `core/ingress.py` |
| "I re-derive the same plan every time" | Plan library (advice, verified per step) + procedural memory | `agent/plan_library.py`, `memory/` |
| "I re-fetch the same pages" | Response cache with TTL; crawl cache with `max_age` | `core/response_cache.py`, `routes/webdata.py` |
| "I need to know my retrieval is any good" | Three backends, weighted RRF, judge-free metrics (recall@k, gold-hit@k, nDCG@k), real BrowseComp-Plus dataset | `bench/`, `modules/semantic.py` |
| "I need to know whether my cloud model is the weak link" | Local-only enforcement at one choke point; per-request cost tracking | `llm/registry.py` |

---

## 5. How this helps **users**

| User concern | What Jambubrowser does |
|---|---|
| "My browsing data shouldn't leave my machine" | Local-only mode is enforced, not aspirational. Maximum privacy mode makes zero external calls; local-only makes no network calls at all |
| "I don't want to pay per query" | Local inference (Ollama, MLX on Apple Silicon, DecentraCode Mesh) + a local crawl index — marginal cost is electricity |
| "I want to own the tool, not rent it" | MIT licence, on your hardware, no vendor lock-in, no telemetry |
| "I don't want a cloud browser reading my pages" | Every page fetched can stay on disk; the quarantine boundary means page text never reaches a privileged model |
| "I need to trust what an agent did on my behalf" | Tamper-evident audit log, step receipts with Merkle roots, signed evidence bundles, findings routed to teams with resolution state |
| "My agent did something irreversible" | Approval gates, irreversible-element classification, upload gating, `human_takeover` |
| "I want my agent to remember me" | Four memory sub-stores, per-user scoped, hybrid retrieval, full forget support |
| "I want to audit my own web app like a pro would" | Six specialist auditors with real browser telemetry, business-impact scoring, shareable read-only report links |
| "I want to reuse my tools from other agents" | 13 MCP tool families and an A2A endpoint — both data-only by design, so exposing them does not widen the instruction surface |

---

## 6. Where it stands against the competitive set

| | Comet / Dia / Gemini in Chrome / Edge Copilot / Atlas | Firecrawl | Exa / Perplexity / Brave | Jambubrowser |
|---|---|---|---|---|
| Runs on your machine | no | no | no | **yes** |
| Licence | proprietary | AGPL-3.0 | proprietary | **MIT** |
| Marginal cost | subscription | per-credit | subscription | **electricity** |
| Untrusted-content handling | unspecified | opt-in, +4 credits, JSON-only | n/a | **on by default, free** |
| Fetch receipts | no | no | no | **yes (Merkle-rooted)** |
| Local index over *your* sites | no | no | no | **yes (crawl + map + watch)** |
| Agent can be blocked from acting | unspecified | n/a | n/a | **yes, at the channel** |
| Retrieval benchmark | no | no | **yes (public)** | **harness + real dataset, not yet scored** |

**The claim we can make today:** Jambubrowser is a local, MIT-licensed agent
platform whose browser is cheaper per step than a primitive loop, whose
untrusted-content handling is on by default, and whose agents act under
approval gates and produce verifiable receipts.

**The claim we cannot make yet:** that it would score in the published
agentic-search table. It would not today — we have no index, and those
benchmarks reward recall over a corpus we do not have. That table is a
destination, and the harness for it now exists.

---

## 7. Measured vs. unverified — the honest ledger

### Measured, here, today

| Claim | Number | How measured |
|---|---|---|
| Lexical retrieval on the local suite | 0.7292 (CI 0.5000–0.9375) | `jambu bench --suite local` |
| Dense-LSA on the same suite | 0.6153 | `--retriever-compare` |
| Hybrid (weighted RRF) on the same suite | 0.7292 | `--retriever-compare` |
| Lexical on real BrowseComp-Plus (10 queries / 226 docs) | answer-in-text 0.30, recall@5 0.112, gold-hit@5 0.20, nDCG@5 0.135 | `jambu bench --suite browsecomp-plus` |
| Test suite health | 2,789 passing; failure set byte-identical to baseline | `pytest tests/` |
| Agent-loop behaviour, tool protocol, untrusted-content channel, failure modes | 16 tests, scripted model | `tests/test_bench_agent.py` |
| Quarantine, schema roles, ingress rules, one-way channel | covered | `tests/test_quarantine*.py`, `tests/test_local_first.py` |

### Unverified — and blocked by the environment, not by missing code

| Gap | Blocker | How to close it |
|---|---|---|
| Browser rendering path (`render=always` has never rendered a page) | `playwright` not installed | `pip install playwright && playwright install chromium`, re-run `make verify` |
| Crawl4AI extraction path | `crawl4ai` not installed | `pip install crawl4ai` |
| Neural encoder path (`sentence-transformers`) | `sentence-transformers` / `torch` not installed | install, then `jambu bench --retriever dense --embedder sentence-transformers` |
| End-to-end agent numbers | configured provider key returns HTTP 402 (quota exhausted) | a working provider, then `jambu bench --policy agent` |
| Crawl **resume** of interrupted jobs | not implemented | — |
| ATLAS / People-FindAll / FinSearchComp suites | not publicly available | — |

Two of these are the ones that decide the competitive question: the browser
path (does the agent actually work on a real page?) and the neural encoder
(does dense retrieval close the gap that LSA opened?). Both are *one
`pip install` away*, and the verification script is written to check exactly
that: `make verify`.

### Known rough edges

- **Two evaluation systems coexist.** `backend/eval/` is a general agent-task
  harness (it already shipped a `webarena-mini` suite); `backend/bench/` is
  the retrieval harness (swap-able backends, corpora, relevance judgements,
  deterministic graders). They are complementary, not duplicates — but the
  duplication is real, and `backend/bench/` was written before `backend/eval/`
  was noticed. The bridge is small: `bench` produces graded results, `eval`
  knows how to run and report agent tasks.
- **Two providers of the same capability.** `backend/modules/browser.py` and
  `browser_agent.py` overlap; `browser_agent.py` is the better one.
- **`pip install` is the wrong instruction in this environment.** PEP 668
  blocks global installs; the workaround is a throwaway venv.

---

## 8. The sharpest weaknesses, stated plainly

1. **No index.** Exa's advantage is 2T URLs and 100B pages. A crawler cannot
   match that, and a crawl-on-demand design cannot either. Jambubrowser wins
   on a *private* corpus — the sites you care about — and loses on the open
   web. That is a real limitation, not a to-do item.
2. **The benchmark harness is not the benchmark result.** We can run
   BrowseComp-Plus; we have not yet produced a number comparable to the
   published 70.1% (local encoder) / 59.9% (live Google) / 3.86% (BM25).
   Until we do, the table has no Jambu row.
3. **The quarantine is defence-in-depth, not a solved problem.** It reduces
   the attack surface and makes injection attempts *visible*. It does not
   make a hostile page harmless. The module says so; so should any pitch.
4. **Install fragility.** The declared dependency set is large and several
   components need native wheels or a browser binary. A first-run experience
   that requires eight `pip install` steps is a real adoption barrier, and
   `make verify` exists precisely because this is known.
5. **The code is broad and unevenly hardened.** 74k lines across 266 files
   with 401 endpoints; the security-critical core (`core/`) is far better
   tested than the periphery. A reviewer's time is better spent on `core/` and
   `agent/` than on the long tail.

---

## 9. Summary

Jambubrowser is **not** "a browser with AI features". It is a local agent
platform with three unusual properties:

1. **Untrusted content is structurally contained.** A hostile page cannot
   become an instruction, because the thing that crosses the boundary is a
   typed field whose type was chosen when the schema was defined — and
   control fields are structurally barred from being free text.
2. **Only the operator gives orders.** One-way ingress, enforced in one
   dependency-free module that every surface imports. A2A and MCP can use the
   engine; they cannot define its work.
3. **Every action is cheap and every action is provable.** Flows cost one
   model round-trip instead of one per step, and each run leaves a
   Merkle-rooted receipt.

The competitive position that follows is narrow but real: **for a user who
needs their agent to browse the web without the web being able to browse
back, Jambubrowser is cheaper, more private and more auditable than any
hosted AI browser — and the benchmark harness to prove the retrieval claim is
built and waiting on one `pip install`.**
