# Verification tiers for paid compute

"Trust me, the GPU ran it" is not a business model. This module implements
the DePIN verification ladder where it applies to this engine, and is
explicit about what each rung does — and does not — prove.

| Tier | Mechanism | Catches | Status |
|---|---|---|---|
| `SIGNED` | hash-chained receipts + anchored Merkle roots (MeshPay) | tampering after the fact | shipped |
| `CANARY` | known-answer probes with must-contain checks | lazy, broken, or substituted workers | shipped |
| `REDUNDANT` | a second executor runs the same task; outputs compared under a tolerance | silent divergence (model substitution, truncation) | shipped |
| `ATTESTED` | TEE/hardware attestation | untrusted host execution | **not implemented** (no hardware lane) |

## Policy: value at risk → required tier

```bash
JAMBU_VERIFY_CANARY_ABOVE_USDC=0.01     # jobs >= $0.01 should be canaried
JAMBU_VERIFY_REDUNDANT_ABOVE_USDC=1.0   # jobs >= $1.00 must run redundant
```

`GET /verification/policy` returns the thresholds plus a note that the
policy *declares* requirements — it does not itself verify anything.

## Comparators: pick the right one for the payload

`POST /verification/redundant` takes `comparator`:

| Comparator | Use for | Semantics |
|---|---|---|
| `exact` | short, exact strings | byte equality |
| `similarity` | prose / free text | difflib ratio ≥ `tolerance` (default 0.85) |
| `numeric` | **solver / simulation output** | values compared under absolute + relative tolerances |

**Use `numeric` for anything numeric.** difflib over the *string* form of
numbers is not a verification method:

| primary | replica | difflib | reality |
|---|---|---|---|
| `100.0` | `1000.0` | 0.909 → **MATCH** | 10× wrong |
| `1.0` | `10.0` | 0.857 → **MATCH** | 10× wrong |
| `1.00` | `1.0000` | 0.800 → mismatch | identical |

Both of the first two would clear the default 0.85 gate — false assurance on
the tier that decides what gets paid for.

`numeric` parses the payload (JSON walked by path, or a flat run of numbers),
compares every numeric leaf, and reports where the worst disagreement was:

```json
{"verdict": "MISMATCH", "max_rel_deviation": 0.000999,
 "max_deviation_path": "$.trace[5]", "only_in_primary": []}
```

Rules it enforces: a replica that **omits** a numeric field the primary
produced is a `MISMATCH` (truncation is the failure redundancy should
catch); `bool`, `NaN` and `Inf` are never treated as numbers; prose is
`ERROR`, never a silent pass.

Tolerances: `JAMBU_SIM_ABS_TOL` / `JAMBU_SIM_REL_TOL` (see
`docs/SIMULATION_COMPUTE.md`), which override the module defaults.

## Executors are pluggable workers

A worker is anything with an async `run(text) -> str`. Three ship:
`echo` (deterministic), `mock-llm` (the LLM registry's mock provider), and
`faulty-echo` — a **fault-injection double**, clearly labelled, so the
detection path is demonstrable rather than asserted. `register_executor()`
adds real ones (a second DCM node, a remote replica) without touching the
verification logic.

Each run is sealed: `execution_hash = sha256(canonical {worker, output})`.

## Live example

```
policy: canary>=$0.01 → CANARY, redundant>=$1.0 → REDUNDANT | attested: not implemented…
canary echo: PASS (2/2) · mock-llm: PASS (2/2) · faulty-echo: FAIL (expected)
redundant mock-llm×2:  MATCH    agreement=1.0   primary_hash=46fe70d9eef0631f…
redundant echo×faulty: MISMATCH agreement=0.6087 (tolerance 0.85)
worker echo: canary=1.0 agreement=0.0 mismatches=1        ← scored by the mismatch
worker mock-llm: canary=1.0 agreement=1.0 mismatches=0
verification evidence: kind=compute_verification → VALID (standalone verifier)
```

## API

| Route | Purpose |
|---|---|
| `GET /verification/policy` | Tier model + thresholds |
| `POST /verification/redundant` | `{text, primary, replica, comparator, tolerance}` → verdict + both receipts |
| `POST /verification/canary` | `{worker_id}` → PASS/FAIL per known-answer task |
| `GET /verification/workers` | Scorecards: canary pass rate, redundancy agreement, mismatches |
| `GET /verification/verdicts` | Raw verdict log |
| `POST /verification/evidence` | Sign the verdict window (`compute_verification` bundle) |

Comparators: `exact` (deterministic work) and `similarity` (difflib ratio
with the tolerance) — an embedding-based comparator can be registered
without changing the flow.

## Honest limits

1. **Sampling is explicit work.** Redundancy doubles cost for the tasks it
   covers; the tier policy decides *when* that is worth it. No silent
   sampling happens today — callers choose.
2. **Similarity is lexical.** `difflib` catches truncation and gross
   substitution, not a plausible-but-wrong answer. A semantic comparator
   (embeddings) is the next step.
3. **No at-scale worker population.** Scorecards aggregate whatever ran;
   there is no reputation system or automatic suspension (by design — a
   false-positive suspension is worse than a missed mismatch).
4. **ATTESTED is marketing until it is hardware.** It is listed in the tier
   enum and explicitly marked not-implemented everywhere it appears.
