# A2A — Jambubrowser as a hireable agent

The Agent2Agent protocol (Linux Foundation, same governance as MCP) is how
agents hire other agents. Jambubrowser publishes an **agent card** and a
**JSON-RPC endpoint** so any A2A client can buy the work this engine
already does — audits, evaluation certification, mesh inference.

## Discovery

```
GET /.well-known/agent-card.json
```

The card is public (even when the RPC endpoint requires auth) and honest:
`capabilities.streaming` and `pushNotifications` are `false`, the bearer
scheme is declared, and every skill lists input/output modes and examples.

## Skills

| Skill | Input | Output |
|---|---|---|
| `audit_web_app` | text containing a URL (inferred) or `{"url": ...}` | summary text + data part: findings count, severity breakdown, top findings, `audit_id` |
| `agent_eval_certify` | `{"suite": "smoke", "provider": "mock", "pass_threshold": 0.4}` | certificate id, verdict, pass-rate summary, `spec_hash` |
| `mesh_inference` | text prompt | completion text + model/token usage |

Skills are selected via `message.metadata.skill`, a `data` part with
`"skill"`, or inferred when the text is a URL. Unknown/absent skills return
`-32602` with the available list.

## Protocol surface

- **Methods**: `SendMessage` (blocking by default; `configuration.blocking:
  false` returns immediately), `GetTask`, `CancelTask`.
- **Honest refusals**: streaming and push-notification methods return the
  spec's `UnsupportedOperation` / `PushNotificationNotSupported` errors
  rather than pretending.
- **States**: the `TASK_STATE_*` JSON enum (`SUBMITTED`, `WORKING`,
  `COMPLETED`, `FAILED`, `CANCELED`); tasks persist in the `a2a_tasks`
  table with full message history (`ROLE_USER` → `ROLE_AGENT`).
- **Orphan recovery**: a task left `WORKING` by an engine restart is marked
  `FAILED` with the reason on first read — never an eternal "working".
- **Errors**: JSON-RPC codes with A2A error-info payloads
  (`TASK_NOT_FOUND` -32001, `TASK_NOT_CANCELABLE` -32002, …).

## Access control (three doors)

1. **API key** — `Authorization: Bearer <engine key>` (the account path).
2. **x402 payment** — when the paywall is enabled, `POST /a2a` is a paid
   route (`a2a_task`, default $0.05). The middleware verifies payment, sets
   the `x402_paid` flag on the request scope, and the route admits it — so
   anonymous agents can hire us with USDC and no account.
3. **`JAMBU_A2A_OPEN=1`** — local development only.

Everything else gets `401` with a `WWW-Authenticate: Bearer` challenge.
(That challenge header was silently dropped by the engine's exception
handler until this work — now every challenge response keeps its headers.)

## Live example

```
agent card: 200 | Jambubrowser v3.3.0 | skills: [audit_web_app, agent_eval_certify, mesh_inference]
unauthenticated /a2a: 401 | WWW-Authenticate: Bearer realm="jambubrows…
audit hire: state=TASK_STATE_COMPLETED | findings=0 | audit_id=1      ← real Playwright audit
certify hire: state=TASK_STATE_COMPLETED | certificate #1 verdict=PASS ← real eval certificate
get task: state=TASK_STATE_COMPLETED | history roles=['ROLE_USER', 'ROLE_AGENT']
```

## Not done yet

1. **Streaming** (`SendStreamingMessage`) — audits stream internally over
   SSE; exposing that as A2A task-update events is the natural next step.
2. **Push notifications** for long tasks (currently clients poll `GetTask`).
3. **Per-skill pricing** — x402 charges a flat per-task price; the artifact
   classes differ wildly (a $0.10 audit vs a $0.001 inference).
4. **Context continuity** — `contextId` is stored but multi-turn
   conversations aren't interpreted yet (each message is one hire).
