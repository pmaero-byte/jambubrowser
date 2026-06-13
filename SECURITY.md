# Security Model

Jambubrowser v3.3.0 ships a defense-in-depth security model designed
for a localhost autonomous research agent. This document covers the
threat model, the mitigations, and how to verify them.

## Threat Model

Jambubrowser is a single-user tool that runs on `127.0.0.1`. The
primary attacker is **untrusted input** flowing into the engine —
URLs, code to sandbox, scraped HTML, file uploads, WebSocket
messages, and the response payloads themselves.

| Threat | Example | Mitigation |
|---|---|---|
| SSRF via user-supplied URLs | `scrape_url("http://169.254.169.254/...")` | `is_safe_url()` blocks private IPs, localhost, link-local |
| DNS rebinding | Public hostname resolves to private IP | Same as SSRF — IP is checked at validation time |
| Host header injection | `Host: evil.com` from upstream proxy | `TrustedHostMiddleware` rejects unknown hosts |
| Body-size DoS | POST 100 MB JSON to `/research` | `BodySizeLimitMiddleware` (2 MB cap) |
| Slow request DoS | Long-running endpoint holds connection | `RequestTimeoutMiddleware` (30 s default) |
| Request flood | 10 000 requests/sec from one IP | `RateLimitMiddleware` (token bucket) |
| XSS via stored content | `document.text = "<script>..."` | `sanitize_html()` + CSP response header |
| Clickjacking | Embed Jambubrowser in iframe | `X-Frame-Options: DENY` + `frame-ancestors 'none'` |
| MIME sniffing | Upload PNG that's actually HTML | `X-Content-Type-Options: nosniff` |
| WebSocket exhaustion | Open 1 000 000 WebSockets | Per-IP + global connection caps |
| Command injection | `osascript` with unescaped user text | Double-quote escape in `/computer/keyboard` |
| Code execution DoS | Sandbox code with `timeout=999999` | Pydantic validator clamps to [1, 120] |
| Mass-fetch DoS | `/research` with `top_n=1000000` | Pydantic validator clamps `top_n` to [1, 50] |
| PII leakage to logs | Audit entry contains user email | `PIIDetector` masks email/phone/SSN/etc. before persisting |
| Error stack trace leak | 500 response shows file paths | `JAMBU_DEBUG=false` hides `str(exc)` in production |
| PII leakage from secret keys | Audit entry shows `api_key=sk-...` | `SENSITIVE_KEYS` fully redacts known secret names |

## Architecture

The middleware stack is the outermost layer of defense. Every HTTP
request passes through these in order (Starlette executes
last-registered first, so the visual order matches the request flow):

```
Request → AccessLog → RequestID → TrustedHost → SecurityHeaders
       → RequestTimeout → GZip → BodySizeLimit → RateLimit
       → Route handler
```

| Middleware | File | Purpose |
|---|---|---|
| AccessLog | `backend/core/access_log.py` | One structured log line per request |
| RequestID | `backend/core/request_id.py` | 12-char hex correlation ID |
| TrustedHost | `backend/core/trusted_host.py` | Reject untrusted `Host` (HTTP 421) |
| SecurityHeaders | `backend/core/security_headers.py` | CSP, HSTS, X-Frame-Options, etc. |
| RequestTimeout | `backend/core/request_timeout.py` | 504 after 30 s |
| GZip | FastAPI built-in | Response compression |
| BodySizeLimit | `backend/core/body_size_limit.py` | 413 after 2 MB |
| RateLimit | `backend/core/rate_limiter.py` | Per-IP + per-endpoint token bucket |

## URL Validation (`is_safe_url`)

Every URL-accepting Pydantic model and query-param endpoint runs
through `is_safe_url()` (`backend/core/security.py`):

1. **Scheme check** — must be `http` or `https` (no `file://`, `data:`, `ftp://`).
2. **Hostname required** — empty host fails.
3. **Private IP blocking** — RFC1918, loopback, link-local, ULA are
   rejected by default. Set `allow_private=True` for dev.
4. **Length cap** — URLs over 8192 chars are rejected.

Blocked ranges:
- `127.0.0.0/8` (loopback)
- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC1918)
- `169.254.0.0/16` (link-local, incl. cloud metadata)
- `::1/128`, `fc00::/7`, `fe80::/10` (IPv6 equivalents)

Hosts in `{localhost, 127.0.0.1, 0.0.0.0}` are also blocked by
default. The dev environment uses `allow_private=True` so
`http://localhost:8001` works from the same machine.

## WebSocket Hardening

The `ConnectionManager` in `backend/engine_runtime.py`:

- **Validates `client_id`** against `[A-Za-z0-9_\-:.]{1,64}`. Anything
  else returns `1008 Policy Violation` and the socket is closed.
- **Per-IP cap** of 8 concurrent connections
  (`WS_MAX_CONNECTIONS_PER_IP`).
- **Global cap** of 256 connections (`WS_MAX_TOTAL_CONNECTIONS`).
- **Replace-on-reconnect** — connecting with the same `client_id`
  cleanly closes the prior socket (no orphaned handlers).
- **Stats** via `manager.get_stats()` for monitoring.

## PII Redaction

Two modules detect and mask PII:

1. **`backend/core/privacy.PIIDetector`** — 10 PII types: email,
   phone (US/intl), SSN, credit card, IP, IPv6, MAC, passport,
   driver license. Plus 4 tracking patterns (Google Analytics,
   Facebook Pixel, Mixpanel, Segment).
2. **`backend/core/audit.AuditLogger._redact_pii`** — uses the shared
   detector. Recurses into nested dicts and lists. Fully redacts
   values for known secret keys (`password`, `api_key`, `token`,
   `secret`, `auth`, `authorization`, `credential`).

The audit redaction preserves surrounding text — only the matched
PII span is masked, not the whole field.

## Tamper-Evident Audit Log

Every audit entry chains to the previous one via SHA-256:

```
hash_n = SHA256(entry_data_n + hash_{n-1})
```

`GET /audit/verify` walks the chain and reports any break. The
chain is part of the security model: a tamper attempt would have to
recompute every subsequent hash.

## Error Sanitization

Production error responses never include `str(exc)`. To debug, set
`JAMBU_DEBUG=true` (development only). The `request_id` is always
included so an operator can correlate to server-side logs.

```json
{
  "detail": "Internal server error",
  "request_id": "a3f8c2d91b04"
}
```

## HSTS

`Strict-Transport-Security: max-age=31536000; includeSubDomains` is
set on responses that arrived over HTTPS (direct TLS or via
`X-Forwarded-Probe: https`). It is **not** set on plain HTTP —
adding it on HTTP would tell browsers to upgrade insecure requests
for 1 year, which is wrong for a dev server.

## Verification

### Manual checks

```bash
# Start the engine
python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001

# Verify response headers
curl -sI http://127.0.0.1:8001/health | grep -iE 'x-frame|content-security|strict-transport|x-content-type'

# Verify rate limiting
for i in {1..30}; do curl -s http://127.0.0.1:8001/search?q=test; done
# Should eventually return 429

# Verify body size limit
curl -s -X POST http://127.0.0.1:8001/research \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'print("x"*3000000)')"
# Should return 413

# Verify trusted host
curl -s -H 'Host: evil.com' http://127.0.0.1:8001/health
# Should return 421

# Verify SSRF protection
curl -s -X POST http://127.0.0.1:8001/scrape \
  -H 'Content-Type: application/json' \
  -d '{"url":"http://169.254.169.254/latest/meta-data/"}'
# Should return 422 (URL validation failed)
```

### Automated checks

```bash
# Run the full security test suite
python3 -m pytest tests/test_security_headers.py \
                   tests/test_body_size_limit.py \
                   tests/test_trusted_host.py \
                   tests/test_request_id.py \
                   tests/test_error_sanitization.py \
                   tests/test_request_timeout.py \
                   tests/test_security_events.py \
                   tests/test_access_log.py \
                   tests/test_core_security.py \
                   tests/test_audit_redaction.py \
                   tests/test_privacy.py \
                   tests/test_supply_chain.py \
                   tests/test_calculator.py \
                   tests/test_health_endpoint.py \
                   tests/test_exec_request.py -v
```

## Reporting a Vulnerability

If you find a security issue, please open a GitHub issue with
the `security` label or email the maintainers. We aim to acknowledge
reports within 48 hours and ship a fix within 7 days for critical
issues.
