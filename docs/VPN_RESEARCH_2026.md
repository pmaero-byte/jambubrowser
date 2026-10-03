# VPN module — research-backed improvement report (Oct 2026)

Audience: this project's `backend/core/vpn/*` (1,468 LOC, 88 tests) and its
AI-agent egress use case: a browser/audit agent that must (a) egress
consistently for a test flow, (b) never leak the operator's real IP, DNS, or
WebRTC candidates, and (c) survive hostile egress environments (DPI,
GFW-style censorship, VPN fingerprinting).

---

## 1. What the module already is (verified in code)

* **Two layers behind one façade** (`VPNManager.resolve_proxy`):
  a tunnel layer (`WireGuard | OpenVPN`, `tunnel.py`) as base egress;
  a pool layer (`pool.py`) rotating proxy endpoints (`failover /
  round_robin / random / least_latency`), sticky session pinning,
  consecutive-failure quarantine, EWMA latency, optional background probe.
* **Fail-closed by default**; credentials redacted in all outputs;
  `JAMBU_VPN_DRY_RUN=1` exercises the tunnel path rootless.
* **Pool state persists** (`JAMBU_VPN_STATE_FILE`, age-bounded atomic JSON)
  so a restart does not wipe learned quarantines.
* **Surfaces**: `GET /vpn/status|config`, `POST /vpn/select|probe`,
  CLI `jambu vpn status|up|down`, MCP `vpn_status|vpn_select|vpn_probe`.
* Tunnelling is exec-based (`wg-quick`/`openvpn` binaries) — no protocol
  reimplementation, standard and small TCB.

---

## 2. What the research / ecosystem shipped since (2024–2026)

| Theme | State of the art |
|---|---|
| **Post-quantum WireGuard** | `Rosenpass` (formally-verified, Classic-McEliece + Kyber/ML-KEM) injects a PQ key-exchange alongside WireGuard. NetBird; adoption in shipped clients. The practical path for us is a sidecar or config knob, not reimplementation |
| **MASQUE / CONNECT-IP / CONNECT-UDP** (RFC 9484; IETF MASQUE) | Arbitrary IP and UDP-over-HTTP per RFC 9484. Cloudflare ships MASQUE proxy support; go-gost/gost implements CONNECT-UDP|IP. This is the **today** answer for networks that block UDP/VPN signatures entirely |
| **DPI fingerprinting of VPNs** | ACM Comm. (Jan 2025): OpenVPN is fingerprintable even with obfuscation; NDSS 2025 showed cross-layer RTT fingerprints identify obfuscated proxies. **Perfect obfuscation is a losing game; mimicry + variation (AmneziaWG) raises the bar, does not guarantee it** |
| **AmneziaWG / obfuscation** | WireGuard fork mimicking QUIC/DNS/SIP handshakes; AmneziaWG 3.1 targets AI-based (ML) blocking via randomized handshake sizes and protocol mimicry |
| **dVPN + micropayments** | DePIN VPNs pay per byte (state channels per Northwestern dVPN work; Sentinel token/GB). Synergy with our existing x402/DCT/MeshPay settlement |
| **AI-era client demands** | Bot/VPN detection now scores WebRTC leaks, DNS leaks, IP-vs-timezone mismatches, and TLS/HTTP fingerprints of headless browsers (Fingerprint, Bright Data agent tooling, Comet-era postmortems). For an *agent* egress, consistency > raw speed |
| **WebRTC/IP leaks** | Even on a VPN, a page's JS can discover the host's real IPs via STUN host candidates, or the host's DNS resolver can bypass the tunnel. Prevention is client-side: disable WebRTC or force it through the tunnel, and force DNS over the tunnel |

---

## 3. Gaps between our module and the state of the art

| # | Gap | Research tie-in |
|---|---|---|
| G1 | Only `wireguard|openvpn` tunnels — both fingerprintable/blockable in restrictive networks | MASQUE/CONNECT-IP (RFC 9484), AmneziaWG mimicry |
| G2 | Classical ECDH/Curve25519 key exchange only; no PQ posture | Rosenpass, ML-KEM-768 |
| G3 | No leak surface: we cannot *prove* to the user that the browser actually egresses through the tunnel | WebRTC STUN leaks, DNS bypass, IPv6 bypass — all documented in 2025 client-fingerprinting literature |
| G4 | `health()` learns latency but not *detection* cost — an endpoint that gets the user flagged as a bot gets kept | Bot/VPN-detection signals (WebRTC, DNS, Headless-Chrome tells) |
| G5 | Pool endpoints are just URLs; no provisioning story inside our own mesh | dVPN + per-byte metering — our DCT/x402/MeshPay stack can *host* the pool |
| G6 | Evidence bundles sign compute but not *network* provenance: which egress was used, whether leaks were checked | AI-era auditability (proxy-vs-agent attribution) |
| G7 | No handshake-mimicry/padding option where it matters | AmneziaWG 2.0/3.1 |

---

## 4. Proposed improvements, mapped to code

### P1 — `GET /vpn/leak-check` (new route + CLI `jambu vpn leak-check`)
Honest, directly testable self-check that the *browser* and *engine* do not
leak:
1. fetch the visible IP through the tunnel and compare with the pool's
   redacted egress IP (mismatch → warn);
2. STUN WebRTC host-candidate probe in the browser context (must not
   contain non-tunnel IPs);
3. DNS resolution through the tunnel vs through the system resolver
   (must match / must use the tunnel's DNS);
4. IPv6 reachability check (must be blocked or routed through the tunnel).

Implemented as `backend/core/vpn/leakcheck.py` returning a structured
verdict; fail-closed callers can refuse to run flows on `leak: true`.
This closes **G3** and makes "fail-closed" *measurable*, not assumed.

### P2 — Tunnel-kind extensibility: `masque` and `amneziawg`
Add `TunnelKind.MASQUE` and `TunnelKind.AMNEZIAWG` to
`config.py::TunnelKind` and implement thin managers in `tunnel.py`:
* `masque` — shell out to a MASQUE client (`gost`-style: `CONNECT-UDP`
  fallback to `CONNECT-IP`), presenting one socks5h endpoint to the pool;
* `amneziawg` — wire to the `awg-quick` binary, identical UX to WireGuard.

Keeps 100% back-compat: `wireguard|openvpn` remains the default;
selecting the new kinds is explicit. Closes **G1**.

### P3 — PQ-readiness sidecar (Rosenpass)
Add `JAMBU_VPN_PQ=rosenpass` config knob that, when set, starts the
`rosenpass` binary alongside WireGuard and exposes its state in
`GET /vpn/status`. We don't reimplement PQ key exchange; we adopt it.
Emit a clear warning in `/vpn/config` when someone is on a
non-PQ-capable path in `fail_closed` mode: the audit trail a user
exports today may be considered weak in 10 years. Closes **G2**.

### P4 — Detection-aware endpoint scoring
Extend `EndpointHealth.to_dict()` with `detection_risk` (low/medium/high)
learned from actual flow outcomes: TLS/HTTP fingerprint mismatches observed
in console telemetry, WebRTC/DNS leak-check failures observed through P1,
and 403/CAPTCHA challenge rates. The pool's `least_latency` policy stays,
but a caller can ask for `JAMBU_VPN_POLICY=least_latency_low_risk` to
weight `latency × detection_risk`. This is advisory, never silent:
`GET /vpn/status` reports both signals separately. Closes **G4**.

### P5 — dVPN-on-MeshPay (pool endpoints become priced providers)
The pool's endpoints can already be trusted *if they are our own nodes*:
each DCM node that offers a SOCKS/WireGuard capability can advertise it in
its node record, and a browser flow through it is metered like
`dcm/infer`, settled via a `compute_network_egress` receipt kind through
the same MeshPay receipts + reconciliation window used for
`compute_simulation`. This turns G5 into a real feature rather than a
differentiator slide — our settlement stack is already the hard part.

### P6 — Network-provenance in evidence
Extend `evidence.build_bundle` with an optional `egress` block: redacted
proxy URL, leak-check verdict id, tunnel kind, `started_at`. A compute
simulation bundle signed through a tainted egress must be distinguishable.
Closes **G6**.

### P7 — Handshake mimicry per endpoint
Add `JAMBU_VPN_MIMICRY=quic|dns|sip|off` that routes AmneziaWG mimicry
(P2) per-endpoint; pool selection prefers mimicry matching the endpoint's
tagged threat profile (e.g. `eu-corp` vs `restricted-region`). Honest
caveat in docs: this raises fingerprinting cost, it does not eliminate it
(NDSS'25 RTT fingerprints). Closes **G7**.

---

## 5. What we will *not* do, and why

* **Not reimplement VPN protocols** — security surface of PQ key exchange,
  handshake mimicry, and MASQUE is too easy to get wrong; we integrate
  externally-built binaries (`rosenpass`, `awg-quick`, a gost-class
  MASQUE client) and validate with dry-run + leak-check.
* **Not claim undetectability** — academic work 2024–2026 (ACM OpenVPN
  fingerprinting, NDSS'25 cross-layer RTTs) shows obfuscation is an
  arms race; we document mimicry as a cost-raiser and keep leak checks
  fail-closed.
* **Not break back-compat** — every P1–P7 is opt-in via env flags;
  with no `JAMBU_VPN_*` set, behaviour must be byte-identical to today.

---

## 6. Suggested order of work (and where each landed)

Status 2026-10-03: P1, P2, P3, P4, P6, P7 implemented and tested; P5 has
its price key reserved in x402 without a route, per the explicit note in
the module — the dVPN marketplace remains the product bet it always was.

1. **P1 leak-check** (highest value-to-risk: pure diagnosis, no new
   protocol surface; becomes the gate for all P2/P3 claims).
2. **P6 network-provenance in evidence** (small; completes the
   AI-era “which egress signed this” story).
3. **P2 tunnel-kind extensibility** (`masque`, `amneziawg`) — unlocks
   real operation in restricted networks.
4. **P3 Rosenpass sidecar**.
5. **P4 detection-aware scoring** (needs end-to-end signal, so after P1).
6. **P5 dVPN-on-MeshPay** (biggest product bet; builds on working P1/P2).
7. **P7 mimicry config**.

References (consulted 2026-10-03): Rosenpass project + engrXiv PQ-WireGuard
gitHub; IETF MASQUE CONNECT-UDP/IP drafts and RFC 9484; AmneziaWG 3.1
release notes; ACM Comm. Jan 2025 "OpenVPN Is Open to VPN Fingerprinting";
NDSS 2025 cross-layer RTT fingerprinting paper; Northwestern dVPN
thesis; Sentinel dVPN pricing doc.
