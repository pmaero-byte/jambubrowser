# Dynamic VPN

Jambubrowser's egress layer. Two layers behind one façade:

```
        ┌──────────────────────────────────────────────┐
        │                VPNManager                    │
        │        (backend/core/vpn/manager.py)         │
        └───────────────┬──────────────────┬───────────┘
                        │                  │
          ┌─────────────▼──────┐   ┌───────▼────────────────┐
          │   tunnel layer     │   │      pool layer        │
          │  WireGuard/OpenVPN │   │ rotating proxy endpoints│
          │  (base egress)     │   │ + health + failover     │
          └────────────────────┘   └────────────────────────┘
```

* **Tunnel** — a system-level VPN that forms the base egress for everything.
* **Pool** — decides *which* endpoint a given request uses, notices when one
  dies, and moves on without a human.

Callers never need to know which layers are active:

```python
from backend.core.vpn import get_vpn_manager

proxy = get_vpn_manager().resolve_proxy(session_key="session-1")
# None  -> direct connection (VPN not configured)
# "…"   -> tunnel through this endpoint
```

---

## 1. Off by default, and that is a feature

With no `JAMBU_VPN_*` variables set, **nothing changes**. `resolve_proxy()`
returns `None`, `make_async_client()` returns a plain `httpx.AsyncClient`, and
the browser launches with no `--proxy-server` flag. This is what lets the
subsystem ship as a default capability without breaking single-user setups.

## 2. Fail closed by default

If VPN is enabled but no usable path exists, Jambubrowser **refuses to connect**
rather than silently leaking your real IP. Opt out explicitly:

```bash
JAMBU_VPN_FAIL_OPEN=1   # fall back to a direct connection instead of erroring
```

The one deliberate exception is a **browser session launch**: `_resolve_proxy()`
degrades to direct rather than failing the launch, because a crashed session is
worse than an unproxied one. Callers that genuinely require a proxy
(`resolve_proxy()`) still get `VPNUnavailable`.

## 3. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `JAMBU_VPN_ENABLED` | off | Master switch |
| `JAMBU_VPN_FAIL_OPEN` | off | `1` allows direct fallback |
| `JAMBU_VPN_POOL` | — | Comma-separated proxy URLs |
| `JAMBU_VPN_ROTATION` | `failover` | `failover`/`round_robin`/`random`/`least_latency` |
| `JAMBU_VPN_REGIONS` | any | Only use endpoints tagged with these regions |
| `JAMBU_VPN_HEALTH_INTERVAL` | `30` | Seconds between health sweeps |
| `JAMBU_VPN_HEALTH_TIMEOUT` | `5` | Per-probe timeout (seconds) |
| `JAMBU_VPN_FAILURE_THRESHOLD` | `3` | Consecutive failures before quarantine |
| `JAMBU_VPN_HEALTH_PROBE_URL` | — | URL the probe fetches; unset = no active probing |
| `JAMBU_VPN_TUNNEL` | none | `wireguard` or `openvpn` |
| `JAMBU_VPN_TUNNEL_INTERFACE` | — | Interface to manage |
| `JAMBU_VPN_TUNNEL_ENDPOINT` | — | Peer `host:port` |
| `JAMBU_VPN_TUNNEL_CONFIG` | — | Path to a `.conf` |
| `JAMBU_VPN_TUNNEL_DNS` | — | Comma-separated DNS servers |
| `JAMBU_VPN_STICKY_TTL` | `300` | Seconds a session keeps its endpoint |
| `JAMBU_VPN_STATE_FILE` | — | JSON path to persist pool health across restarts |
| `JAMBU_VPN_STATE_MAX_AGE` | `86400` | Oldest state to restore, seconds |
| `JAMBU_VPN_PQ` | — | Post-quantum sidecar (`rosenpass`); status reports if requested-but-missing |

### Examples

Pool only, rotating:

```bash
export JAMBU_VPN_ENABLED=1
export JAMBU_VPN_POOL='socks5h://user:pass@eu1.example:1080,socks5h://user:pass@us1.example:1080'
export JAMBU_VPN_ROTATION=round_robin
```

Tunnel + pool, fail-closed, with active health probing:

```bash
export JAMBU_VPN_ENABLED=1
export JAMBU_VPN_TUNNEL=wireguard
export JAMBU_VPN_TUNNEL_INTERFACE=wg0
export JAMBU_VPN_TUNNEL_ENDPOINT=vpn.example.com:51820
export JAMBU_VPN_POOL='http://proxy-a:3128,http://proxy-b:3128'
export JAMBU_VPN_HEALTH_PROBE_URL=https://example.com/healthz
```

---

## 4. Rotation and stickiness

These are deliberately independent:

| Caller | Behaviour |
|---|---|
| `select()` (no key) | Honours the rotation policy — `round_robin` rotates |
| `select("s1")` | Sticky only when the policy is `failover` |
| `select("s1", sticky=True)` | **Always** pinned, whatever the policy |
| `select("s1", sticky=False)` | Always rotates |

**Browser sessions pass `sticky=True`.** A test flow that rotates IP
mid-run breaks cookies, logins, and CSRF tokens. Resolution happens once at
launch (`BrowserSession.start()`), not per request, so a session keeps one
egress IP for its whole lifetime.

## 5. Health and automatic failover

Endpoints are tracked per URL with a rolling health record:

* `failure_threshold` **consecutive** failures quarantine an endpoint. One
  blip never kills a good proxy.
* Quarantine is time-boxed and self-healing — a success restores the endpoint
  immediately, and an expired quarantine re-admits it.
* Latency is an EWMA (`0.7×old + 0.3×new`) so `least_latency` does not
  thrash on a single fast sample.
* `JAMBU_VPN_HEALTH_PROBE_URL` enables a background sweep that probes every
  endpoint concurrently. Without it the pool is passive — it learns only from
  real request outcomes.

Real request outcomes feed back through `report()`:

```python
manager = get_vpn_manager()
proxy = manager.resolve_proxy(session_key=session_id)
try:
    ...
    manager.report(proxy, ok=True, latency_ms=elapsed)
except Exception as exc:
    manager.report(proxy, ok=False, error=str(exc))
```

## 6. Credentials never leak

Proxy URLs may embed credentials. Everything user-facing is redacted:

```
socks5h://user:supersecret@host:1080  ->  socks5h://***:***@host:1080
```

`VPNConfig.redacted()`, `EndpointHealth.to_dict()`, and the
`NoHealthyEndpoint` message all pass through `redact_proxy_url()`. This is
covered by tests asserting the password never appears in output.

**One deliberate exception:** `POST /vpn/select` returns the raw `proxy` URL
because its caller needs the real endpoint to actually make the request. It
also returns a `redacted_proxy` field for display, and the endpoint already
sits behind whatever auth guards the engine. Do not log the raw field.

## 7. HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /vpn/status` | Config (redacted), tunnel state, pool health |
| `GET /vpn/config` | Resolved configuration only |
| `POST /vpn/select` | Resolve the endpoint for a session |
| `POST /vpn/probe` | Run one health sweep now |

```bash
curl -s localhost:8001/vpn/status | jq '.pool.endpoints[] | {url, available}'
```

Bringing a tunnel up/down is **not** on HTTP — it needs root, so it lives on
the CLI where privilege is explicit.

## 8. CLI

```bash
jambu vpn status      # tunnel + pool health (exit 2 on config problems)
jambu vpn up          # bring the tunnel up
jambu vpn down        # take it down
```

## 9. Privileges and dry-run

Managing a real tunnel requires **root** plus the vendor binary
(`wg-quick` + `wg`, or `openvpn` + `pkill`). When either is missing, the
subsystem reports it precisely rather than raising a traceback:

```
wg-quick, wg not installed; install the VPN client or set JAMBU_VPN_DRY_RUN=1 to simulate
```

Set `JAMBU_VPN_DRY_RUN=1` to exercise the whole code path — manager, pool, HTTP
API, and CLI — without touching the host network. Every test in
`tests/test_vpn.py` runs this way, so CI needs neither root nor a VPN.

## 10. How it hooks into the engine

* **`backend/core/socks.py`** — `make_async_client()` now accepts
  `proxy_url=` and `session_key=`. Precedence: explicit `proxy_url` → dynamic
  pool → static `JAMBU_TOR_SOCKS_URL` → direct. Every engine module already
  calls `make_async_client()`, so all outbound HTTP inherits VPN at once.
* **`backend/modules/browser.py`** — `BrowserSession._resolve_proxy()` picks an
  endpoint at launch; `get_privacy_report()` exposes a redacted `egress` block.
* **`backend/engine.py`** — the lifespan starts the tunnel and health sweeper,
  and tears both down on shutdown. VPN failures are logged, never fatal to the
  engine.

## 11. What is deliberately NOT here

* **No traffic interception or MITM.** Jambubrowser routes traffic; it does not
  rewrite or inspect TLS.
* **No per-request IP spoofing headers.** `X-Forwarded-For` is not injected.
* **No credential cycling.** Proxy credentials come from your environment.
* **OpenVPN status is reported from last known state.** The daemon is
  deliberately not adopted as a child process, so we do not probe for a
  process we do not own.

## See also

- `docs/FEATURE_MAP.md` — the DeepNet / browser pillar overview
- `docs/CHANGELOG.md` — release notes
- `tests/test_vpn.py` — 99 tests covering all three layers