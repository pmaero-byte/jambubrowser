# Fully local · free · secure · token-efficient

**Positioning, competitor research, gap analysis and build plan**
Status: Slice 1 implemented · 18 October 2026 · repo `main` @ `cc5abc6` (v3.4.0)

This document answers one question: *can Jambubrowser honestly claim to be the
**fully local, free, secure** browser that **saves tokens with agents**, and if
not yet, what exactly is missing?*

It is split into: what the claim should mean, what the competition actually
does (with sources), what the code actually does today, what shipped in Slice 1,
and what remains. Where a claim is not yet true, it says so.

---

## 1. The four promises, defined precisely

Vague promises are unfalsifiable, so each is pinned to something testable:

| Promise | Testable definition |
|---|---|
| **Fully local** | With `JAMBU_LLM_LOCAL_ONLY=1`, no request containing user content can leave the machine. Verifiable by refusing (not silently rerouting) any cloud provider. |
| **Free** | MIT-licensed, no account, no API key required to run; all gated features default to open. |
| **Full security** | Untrusted page content cannot reach a prompt unexamined; security checks fail *closed*, not open. |
| **Saves tokens** | Agent loops spend measurably fewer input tokens than a naive perceive-act loop for the same task, and the saving is reported. |

The fourth is the one that makes the first viable: a local model has a small
context window and limited throughput, so **token efficiency is what makes
fully-local possible**, not a nice-to-have.

---

## 2. Competitor research

### 2.1 Prompt injection is unsolved — by everyone

This is the single most important fact for positioning. It is not a Jambu
weakness; it is the industry's open wound.

- **OWASP** ranks prompt injection as **LLM01**, the top LLM risk. — <https://genai.owasp.org/llm-top-10>
- The **UK NCSC** warned (Dec 2025) that prompt injection "may never be totally mitigated". — <https://techcrunch.com/2025/12/22/openai-says-ai-browsers-may-always-be-vulnerable-to-prompt-injection-attacks>
- **OpenAI's own** hardening post (22 Dec 2025) concedes agent mode "expands the security threat surface". — <https://openai.com/index/hardening-atlas-against-prompt-injection>
- At **Black Hat USA 2026**, Brave's security engineer tested multiple AI browsers and found **every one vulnerable**. — <https://www.darkreading.com/application-security/no-perfect-fix-ai-browser-prompt-injection-flaws>
- **Indirect prompt injection is now observed in the wild**, not just in PoCs: Palo Alto Unit 42 documented 12 cases (Mar 2026) and Google reported a ~32% rise in malicious content Nov 2025 – Feb 2026. — <https://unit42.paloaltonetworks.com/ai-agent-prompt-injection>, <https://blog.google/security/prompt-injections-web>

### 2.2 Per-product security record

| Product | Notable documented incidents |
|---|---|
| **Perplexity Comet** | Hidden-text injection via Reddit spoiler tag (Brave, 20 Aug 2025); **"CometJacking"** — injection via URL query parameters → Gmail/Calendar exfiltration (LayerX, 4 Oct 2025), initially dismissed as "no security impact"; screenshot/OCR injection (Brave, 21 Oct 2025); Trail of Bits audit found four exfiltration techniques (Feb 2026); Zenity agent-hijack + credential theft (3 Mar 2026); **BioShocking** (Jun 2026) — Perplexity reportedly closed the report without a fix. |
| **ChatGPT Atlas** | Omnibox injection treating a crafted string as trusted user intent (NeuralTrust, 24 Oct 2025); persistent "tainted memory" injection (LayerX, Oct 2025). **Retired 9 Aug 2026.** Patched the BioShocking issue before shutting down. |
| **Dia** | Two **vendor-confirmed, patched** spoofing CVEs (CVE-2025-15032 CVSS 7.4, CVE-2025-13132 CVSS 7.5). Also self-disclosed and fixed a `fetch_web_content` exfiltration flaw before shipping. Its URL-provenance architecture is the most concrete "assume the content is hostile" design found. |
| **Gemini in Chrome** | Calendar-invite semantic injection exposing private meetings (Miggo, 19 Jan 2026, fixed); "Invitation Is All You Need" — 14 promptware attacks incl. smart-home control and data exfiltration (SafeBreach/Black Hat USA 2025). Google now pays up to **$20,000** for agentic-browser boundary bugs. |
| **Edge Copilot** | CVE-2026-33111 (Copilot Chat command injection); adjacent M365 Copilot flaws incl. **EchoLeak CVE-2025-32711, CVSS 9.3** — the first real-world zero-click prompt-injection exfiltration in production. |
| **Opera Neon** | Hidden HTML-element injection (`opacity:0`) — Opera first closed the report as "Not Applicable", then fixed it (Brave, 31 Oct 2025). |
| **Brave** | No Leo CVE found. Notably **open source (MPL-2.0)** and supports **local models** ("bring your own model"). But Brave's own research (8 Jun 2026) defeated **both** a cloud model (Tabstack) **and** a fully on-device one (Cotypist), concluding local deployment *"doesn't eliminate the risk"*. |

### 2.3 What this means for Jambu's positioning

Three conclusions that are defensible from the evidence:

1. **"We are secure" is not claimable by anyone.** The honest, credible claim is
   *"we assume hostile content, we remove what a human couldn't see, and our
   security checks fail closed — and we tell you when a check didn't run."*
2. **Local ≠ safe.** Anyone marketing local inference as a security fix is
   contradicted by Brave's own findings. Jambu should claim local for
   **sovereignty, cost and privacy** — not as injection immunity.
3. **Open source + local + self-hostable is a real, rare combination.** Of the
   major consumer AI browsers, only Brave is open source; the rest are
   proprietary and cloud-dependent. Jambu is MIT, self-hostable, and local.

---

## 3. Token economics — what the numbers say

Cost is dominated by **input tokens**, and input is dominated by **page
representation and replayed history** — not by model reasoning.

| Finding | Number | Source |
|---|---|---|
| Desktop agent cost per task | **$2.43/task**, planning = **87%** of cost | OSWorld-Human, arXiv:2506.16042 (May 2026) |
| Planning prompt tokens | ~**2.2M per task**; prompt grows every step | OSWorld-Human |
| Web observation tokens | ~**2,210–2,931/step**, 6.2–13.3 steps/task | AgentOccam, arXiv:2410.13825 |
| Browser agent tokens/task | **97k** (Playwright MCP) vs **32k** (Stagehand facade) | Stagehand/Browserbase benchmark (29 Sep 2026) |
| Tool-definition overhead | **25 tools = 17,857 characters** before any instruction | Stagehand |
| Computer-use toolset overhead | **~4,500 input tokens/request** (browser toolset ~6,600) | Anthropic docs |
| One screenshot (1092×1092) | **1,521 visual tokens** | Anthropic vision docs |
| Tool-result filtering via code execution | **150,000 → 2,000 tokens (98.7%)** | Anthropic engineering (4 Nov 2025) |
| Agentic vs single-round reasoning | ~**3,500×** more tokens; run-to-run variance up to **30×** | arXiv:2604.22750 (Apr 2026) |
| Prompt-cache read | **0.1×** base input (0.05× on newest models) | Anthropic pricing |

**The counter-intuitive finding that matters most:** a *bigger* observation is
**not** automatically cheaper. OSWorld-Human found that adding the accessibility
tree could add thousands of tokens per step **and increase step counts** (e.g.
LibreOffice Writer 21 → 50 steps). The levers with hard published numbers are
**history compaction, tool-result filtering, prompt caching, batch pricing and
action grouping** — not "swap vision for DOM".

### 3.1 What Jambu already ships (audited, file-level)

The token-efficiency story is Jambu's strongest existing asset — most of it was
already built, and it is unusually well-instrumented:

- **One-call flows** collapsing a perceive→act loop into a single call
  (`browser/sessions/run`), with a **measured savings ledger**
  (`GET /browser/sessions/savings`) — Jambu *quantifies* what it saved, which
  none of the competitors appear to do.
- **Compact and delta snapshots** (`compact=true`, `delta=true`) plus a hard
  `max_tokens` ceiling that halves the view until it fits.
- **Element refs (`@eN`)** instead of re-sending selectors.
- **MCP tool profiles** (`full`/`curated`/`developer`) — directly addresses the
  17,857-char tool-bloat problem.
- **Plan library** (warm starts), **budget-aware loop**, **cheapest-model
  routing**, per-result truncation caps, MoA reference trimming.

### 3.2 Gaps identified

- **No prompt caching at all** (`cache_control` absent) — leaving the single
  cheapest documented win on the table for cloud users.
- **Full snapshot is opt-out**: the docs themselves call it "the single largest
  line item", yet a naive agent pays for it by default.
- **Raw HTML could reach the synthesis prompt** — fixed in Slice 1.
- No bounded conversation-history compaction for the chat agent.

---

## 4. Audit: claimed vs actually wired

Findings from a file-level audit (the "value" column is what the code does, not
what the docs say).

| Claim | Reality | Evidence |
|---|---|---|
| Fully local | **Partial — was bypassable** | `JAMBU_LLM_LOCAL_ONLY` was enforced only in `registry.get_default()`. Explicitly naming a provider, or setting `JAMBU_LLM_PROVIDER=anthropic`, skipped the check. Raw `httpx` calls in the research routes bypassed the LLM layer entirely. |
| Local search | **Broken wiring** | `backend/modules/search.py` hardcoded `localhost:8888` while `docker-compose.yml` set `SEARXNG_URL=http://searxng:8080/search`. A self-hosted SearXNG was never used; every search fell through to public DuckDuckGo/Bing/Google scrapers. |
| Free | **True** | MIT; x402 paywall disabled by default; Stripe is a stub; `check_audit_quota` is defined but never called. |
| Full security | **Weakest area** | No content-level prompt-injection defence of any kind. URL risk assessment **failed open** (error → "safe"). No telemetry, but URL-reputation lookups send URLs to URLhaus/PhishTank. |
| Saves tokens | **Strong, with gaps** | See §3.1 / §3.2. |

---

## 5. Slice 1 — shipped in this change

Scope: close the ways the two headline promises were *false*, without changing
behaviour for anyone who has not opted in.

### 5.1 Fully local, actually enforced

- `backend/llm/base.py` — added `LocalOnlyViolation` and a single shared
  `LOCAL_PROVIDERS` set (previously duplicated in three places, so a new local
  provider could be half-added).
- `backend/llm/registry.py` — enforcement moved into a `_resolve_provider()`
  choke point used by **both** `chat()` and `stream()`, and `get_default()` now
  refuses an explicitly-configured cloud provider too. It **raises** rather than
  silently substituting, so the guarantee is falsifiable rather than invisible.
- `backend/modules/search.py` — `searxng_url()` now honours `SEARXNG_URL`
  (re-read per call, `/search` appended if missing). Self-hosted search now
  actually gets used.

### 5.2 Full security

- **New** `backend/core/untrusted.py` — one place that normalises untrusted text
  before it can reach a prompt:
  - strips zero-width / bidi-override characters (the classic invisible-text
    smuggling channel) so the model and the human see the same page;
  - strips HTML comments, `<script>`/`<style>` bodies and visually-hidden
    elements (`display:none`, `opacity:0`, `aria-hidden`);
  - **reports** injection markers (`override_instructions`, `secrecy_demand`,
    `exfiltrate`, …) instead of deleting them, so an operator can see the attack;
  - `fence_untrusted()` marks data as data.
  It deliberately does **not** claim to solve prompt injection, and the module
  says so — consistent with §2.1.
- `backend/routes/research.py` — scraped text now passes through that guard; a
  suspected injection is logged as a warning.
- `_assess_url_risk` is now **tri-state** (`True`/`False`/`None`). It previously
  returned `False` ("safe") on any exception — a security check that failed
  **open**. Unassessed URLs are now explicit, and
  `JAMBU_RESEARCH_STRICT_URLS=1` opts into dropping them (fail-closed). Default
  keeps them, because otherwise a stopped local model would silently empty every
  research run — a footgun, not a defence.

### 5.3 Saves tokens

- `_scrape_source` no longer stringifies the scraper dict, which used to drag
  the **raw HTML document** into the synthesis prompt — the largest wasted-token
  item on that path. It now collects only scalar text fields and excludes
  `html`/`links`/`headers`/`screenshot`.

### 5.4 Tests

`tests/test_local_first.py` — **33 tests** covering SearXNG resolution, the
local-only enforcement paths (both bypasses), the sanitiser, tri-state
assessment, strict-mode policy, and raw-HTML exclusion.
**Result: 33 passed.** Existing suites re-run green: `test_llm_layer`,
`test_research_phases`, `test_research_integration`, `test_privacy`
(73 passed, 2 skipped).

Three defects were found in the new guard while testing it, and fixed before
this landed:

- a hidden element nested inside a **visible** container was never inspected
  (the outer tag matched first and was returned unchanged);
- `aria-hidden="false"` — which means *visible* — was being deleted;
- hidden **void** elements (`<img style="opacity:0">`) were not matched at all.

---

## 6. Slice 2 — the quarantine boundary (implemented)

The architecture you specified: *one local browser, detached from external
injection, inputs given only locally and in one direction.* Two new modules
carry it, and the rule is now stated in code rather than assumed.

### 6.1 `backend/core/quarantine.py` — detached from external injection

A **two-model split**. The quarantined model sees raw untrusted text and holds
**no tools and no authority**; its only permitted output is a JSON object
matching a declared, *closed* schema. The privileged model never sees raw page
prose — only validated typed data.

The load-bearing invariant, stated in the module and pinned by a test:

> the output of `quarantine_extract()` is a dict of JSON primitives — never a
> string a caller could mistake for an instruction.

Enforcement details:

- **Undeclared fields are rejected**, not ignored. That is the specific
  anti-smuggling rule: a hostile page cannot append an extra `instructions`
  key to an otherwise-valid result.
- **Non-conforming output degrades to `ok=False` and no data** — it never
  raises, because an exception would tempt a caller to fall back to the raw
  text, which is the one thing the boundary exists to prevent.
- Everything is **re-sanitised on the way out** too (defence in depth: the
  quarantined model may echo hostile characters back).
- `render_for_privileged()` is the only sanctioned way to show extracted values
  to the deciding model, and it labels them "facts, not instructions".
- The system prompt states the no-authority rule explicitly, so it overrides
  anything the source text says.

### 6.2 `backend/core/ingress.py` — one channel, one direction

Exactly one channel may author an instruction: the **local operator**.

```
may_instruct == (source is a local surface) AND (peer is loopback)
```

- **Authentication is not sufficient.** A correctly-authenticated *remote*
  caller is still remote, so it is still data-only. The channel decides, not the
  credential — that is what makes "inputs only via local" a property rather than
  a promise.
- **A2A and remote MCP are data-only by construction**, even from loopback and
  even authenticated.
- **An unknown peer fails closed** (`classify_origin(None) → REMOTE`), so a
  request whose origin cannot be established can never instruct.

### 6.3 Wiring

- `backend/routes/decentralized/a2a.py` — the route now computes its ingress and
  asserts it is data-only, failing loudly if that ever stops being true. A2A was
  *already* structurally data-only (`resolve_skill` returns `SKILL_REQUIRED`
  unless a declared skill is named), so no behaviour changed for callers — the
  invariant is now explicit instead of accidental.
- `backend/mcp_http.py` — the auth middleware records data-only provenance on
  the ASGI scope so downstream tool handlers can read it.
- `backend/agent/loop.py` — tool observations are fenced and labelled
  `UNTRUSTED TOOL OBSERVATIONS` before entering the synthesis prompt. This is
  the "detached" rule applied at the one place untrusted text reaches the
  deciding model.

### 6.4 Tests

`tests/test_quarantine.py` — **37 tests**: schema closure, type coercion,
undeclared-field rejection, JSON tolerance, injection-in-source, model refusal,
model unavailability, the data-not-string invariant, and every ingress rule
(loopback, IPv6, authenticated-remote, A2A/MCP, unknown peer).
**Result: 37 passed.** Together with Slice 1: **70 new tests, all passing; no
regressions** (full-suite failure list still byte-identical to the environment
baseline).

### 6.5 The loop now quarantines (gap closed)

Last revision this section recorded an honest gap: the agent loop *labelled*
untrusted observations but still showed the privileged model the raw text. That
is now wired end to end.

- `backend/core/schemas.py` — the concrete catalogue (`page_facts`,
  `search_results`, `article_facts`, `listing`, `action_outcome`) with the
  approved caps. `validate_all()` runs in the test suite, so **a schema that
  breaks a design rule fails the build** rather than degrading at runtime.
- `backend/core/observations.py` — the adapter. It pulls page-controlled prose
  out of each tool's result shape (never raw HTML), extracts it through the
  quarantined model, and renders typed fields. Trusted/internal tools
  (`memory_recall`, `final_answer`) pass through untouched — their output never
  came from a page.
- `backend/agent/loop.py` — `_synthesize` routes every web-tool observation
  through the boundary. The privileged model now receives *typed fields*, not
  prose.

**Fail-closed, with no raw-text fallback.** If extraction yields nothing usable,
the observation is replaced by an explicit note — never by the original prose.
That matters: a fallback would reopen the hole exactly when the model is
misbehaving. Disabling the boundary is possible only explicitly
(`JAMBU_QUARANTINE_OBSERVATIONS=0`, for debugging), and that path is
fenced-and-labelled rather than silently raw.

### 6.6 The browser snapshot guard

`backend/modules/browser_agent.py::_scrub` now strips invisible/bidi characters
and flags injection markers in **element names and hrefs**, which are
attacker-controlled text — `link "Ignore all previous instructions" [ref=e5]` is
a legal accessibility-tree node.

This runs **regardless of the PII policy**. Masking PII is a privacy control;
removing invisible characters is visibility parity, so it must not be
conditional on a privacy setting.

---

## 7. What remains (prioritised)

Ordered by value to the four promises per unit of risk.

### Slice 3 — make token savings measurable and on by default
1. **Compact snapshots by default** for agent-facing calls, with full snapshot
   behind an explicit flag. Highest-value token win and it is already built —
   only the default is wrong.
2. **Prompt caching** (`cache_control` for Anthropic, cache flags for OpenAI) —
   0.1× input on replayed history; the largest documented saving not yet used.
3. **Bounded history compaction** with a fixed window, per OSWorld-Human's own
   recommendation.
4. **Publish the savings ledger** as a headline number
   (`/browser/sessions/savings` already measures it) so "saves tokens" is a
   cited figure, not a claim.

### Slice 4 — finish the local story
5. Route the research routes' raw `httpx` LLM calls through the LLM layer so
   `local_only` covers them too.
6. **Local-only mode for URL reputation**: skip the URLhaus/PhishTank lookups
   (or make them opt-in) when `force_local_only` is set — today they leak the
   URLs being assessed.
7. Verify a **clean install from a fresh environment** — see the caveat below.

### Slice 5 — security depth
8. Route agent tool observations through `quarantine_extract()` with per-task
   schemas, so the privileged model receives typed fields rather than fenced
   raw text (see §6.5). Apply the guard at the **browser snapshot → agent**
   path too, where page-controlled text enters the loop.
9. Adopt an **"assume compromise"** posture modelled on Dia's URL-provenance
   design: untrusted content can never authorise a state-changing action.

---

## 8. Honest caveats

- **Local is not a security fix.** Brave defeated an on-device model. Jambu
  should sell local for sovereignty and cost, never as injection immunity.
- **The untrusted-content guard is hygiene, not a solution.** Stripping
  invisible text raises the cost of an attack; it does not prevent one.
- **`JAMBU_RESEARCH_STRICT_URLS` defaults to off.** Fail-closed on unassessable
  URLs is available but not default, because it would make research silently
  empty whenever the local model is not running. This is a deliberate trade-off,
  not an oversight.
- **Environment drift (found while working, unrelated to these changes).** The
  local checkout could not run *any* test: `sqlite-vec` (declared in
  `requirements.txt`) was missing, and `backend/core/__init__.py` imports the
  database layer, so nothing under `backend.core` was importable. Also missing:
  `psutil`, `pytest-asyncio`, `solders`, `markdownify`,
  `sentence-transformers` and `playwright` — **all declared** in
  `requirements.txt`, none installed. `pip install mcp` also installs 2.x while
  the project pins `mcp<2`, so a fresh resolve needs care.

  **Regression evidence.** Running the full suite before and after these changes
  produced a **byte-identical** list of failures:
  `57 failed, 2398 passed` → `57 failed, 2432 passed` (+34: the new tests),
  with the same 75 `FAILED`/`ERROR` lines. Every one of those 57 failures and 17
  errors traces to a missing optional dependency above — none to this change.
  A clean-install path should still be verified end to end, because "free and
  easy to run locally" depends on it.
