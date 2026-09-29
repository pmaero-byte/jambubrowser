# Browser sessions for agents — the loop, with rails

The category's bake-offs agree on the winning perception pattern:
**accessibility/DOM snapshot → typed element catalog with refs → deterministic
dispatch by ref** (not pixel clicking, not selector guessing). Jambubrowser
already had the pieces — Playwright sessions, DevTools telemetry, the
anti-framing proxy, privacy modes, PII detection — this module turns them
into a service an agent (or MCP client) can use safely.

## The loop

```
POST /browser/sessions            {allow_domains: ["example.com"], require_approval: true}
  → session_id, allowlist, approval policy

POST /browser/sessions/{id}/navigate   {url}          # allowlist-checked
GET  /browser/sessions/{id}/snapshot                  # catalog: @e1… + scrubbed text
GET  /browser/sessions/{id}/snapshot?compact=true     # token-budgeted projection
POST /browser/sessions/{id}/act        {action, ref, text?, approve?}
GET  /browser/sessions/{id}/receipts                  # hash chain + Merkle root
POST /browser/sessions/{id}/evidence                  # signed bundle (E3)
DELETE /browser/sessions/{id}
```

MCP tools mirror it: `browser_session_open`, `browser_session_snapshot`,
`browser_session_act`, `browser_session_receipts`, `browser_session_close`.

## Spending fewer tokens on perception

A full snapshot ships every interactive element as a dict plus the scrubbed
body text, on every read. On a real page that is the single largest line item
in an agent's context, and nothing in the pipeline budgets for it. The
snapshot endpoint (and `browser_session_snapshot`) therefore takes an opt-in
projection:

```
GET /browser/sessions/{id}/snapshot?compact=true&query=save&roles=button
```

| Param | Effect |
|---|---|
| `compact` | `columns`/`rows` instead of one dict per element, with default-valued cells dropped |
| `query` | Keep only elements matching these words (name/value/href/role/type); `match=any` gives a candidate set |
| `roles` | Comma-separated role/tag allowlist, e.g. `button,input` |
| `fields` | Columns to keep; `ref` is always included since the agent needs a handle |
| `text` | Return only the page-text lines matching this phrase, instead of 4000 characters |
| `max_tokens` | Halve the view until it fits the budget |
| `delta` | Report only what moved **since the previous snapshot** |

`delta` is the cheapest way to answer "did that click do anything?" after a
mutating step: it returns added/changed/removed rows keyed on element
*content*, not on refs (which renumber on every read and would otherwise
report the whole page as churn). On a first snapshot there is nothing to
compare against, so it degrades to a plain row listing rather than claiming
every element is new.

The projection never widens what the agent may see — it is applied to the
same scrubbed state, after the same allowlist and approval rails — and it
reports what it dropped (`truncated`, `omitted`, `hint`, `tokens_estimate`)
rather than trimming silently.

## The rails (what prompt injection cannot do)

| Rail | Behaviour |
|---|---|
| **Domain allowlist** | Sessions fail closed (non-empty list required). Navigations, redirects, *and link clicks* are checked; a disallowed landing page is reverted to `about:blank` and recorded as a violation. |
| **Request-level network policy** | Every page request is intercepted through Playwright routing: subresources, `fetch`/XHR, redirects, API steps, and WebSockets are checked against the same allowlist. Unsafe protocols, disallowed hosts, and private-IP DNS rebinding are aborted. The session info and flow report include a bounded policy report. |
| **Approval gates** | Sessions can require `approve=true` for input actions; a risk classifier (buy/pay/delete/send/transfer/subscribe/confirm…) **always** requires approval, even when the session doesn't — "Delete account" cannot be clicked by an injected instruction. |
| **PII scrubbing** | Snapshot text, element names and hrefs pass through the shared `PIIDetector` before an agent sees them (`scrub_pii: false` opts out). |
| **Receipts** | Every step — including refusals — is appended with `outcome: ok/blocked/reverted`, hash-chained via MeshPay's JS-faithful serializer. The log has a Merkle root and can be signed into a verifiable evidence bundle. |
| **Resource bounds** | Max sessions, 15-minute TTL, max steps and elements per snapshot. |

Refusals are explicit HTTP 403s with machine-readable reasons:
`blocked_domain`, `approval_required`, `unsafe_url`, `unknown_ref`,
`session_limit` (429), `not_found` (404).

## Live verification (real Playwright, real example.com)

```
open: 200 | bs-03611f4fb1 | allowlist: ['example.com']
navigate example.com: 200
snapshot: 200 | elements: 1
   first link: @e1 | Learn more | href: https://iana.org/domains/example
navigate iana.org: 403 → blocked_domain
click external link: 403 → blocked_domain      ← refused *before* the click
receipts: 3 steps | outcomes: ['ok', 'ok', 'blocked']
session evidence → VALID (exit 0)              ← standalone verifier
close: 200
```

## What is not done yet

- **Persistent auth profiles.** Sessions are ephemeral by design; "log in
  once, reuse later" is not implemented (vault + form-filler exist to build
  it on).
- **Cost metering.** Sessions are not x402-priced yet; the paywall covers
  audits and mesh inference.
