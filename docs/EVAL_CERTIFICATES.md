# Agent-evaluation certificates

Benchmarks are theater unless you can show **what was committed, what ran,
and what the score means**. This wraps the existing eval harness
(`backend/eval`: smoke, gaia, webarena_mini, webshop, swebench, memory,
privacy, alfworld — 9 suites) in the ClaimReceipt discipline: a frozen
spec, coverage as a first-class check, recomputable verdicts, and an
Ed25519-signed certificate anyone can verify.

## The certificate lifecycle

```
POST /eval/certificates  {suite: "smoke", provider: "mock", pass_threshold: 0.4}

1. FREEZE   the spec (suite, sorted task ids, scoring rule, provider/model,
            threshold, timestamp) is canonicalized and hashed  → spec_hash
2. RUN      the suite through the harness
3. JUDGE    coverage + score (see verdicts below)
4. SIGN     an evidence bundle (kind: agent_eval) via E3
```

```json
{
  "kind": "agent_eval",
  "payload": {
    "spec": { "suite": "smoke", "task_ids": [...], "scoring": {...}, "subject": {...} },
    "spec_hash": "e16d14e1…",
    "results": [ { "task_id", "status", "score", "duration_ms", "error" }, ... ],
    "verdict": { "verdict": "PASS", "reasons": [], "summary": {...} },
    "run": { "provider", "model", "run_id", "duration_seconds" }
  },
  "signature": "<ed25519>"
}
```

## Verdicts — and why

| Verdict | When | Why it matters |
|---|---|---|
| `PASS` | coverage complete **and** pass rate ≥ threshold | The claim the certificate makes |
| `FAIL` | coverage complete, rate below threshold, no harness errors | An honest negative result is still evidence |
| `INCONCLUSIVE` | rate below threshold **and** tasks errored (provider 500s, timeouts) | A broken run must not read as a bad model |
| `INVALID` | missing / extra / duplicate / malformed results vs. the committed spec | Dropping the tasks you failed is the oldest benchmark trick; coverage makes it detectable |

`GET /eval/certificates/{id}` verifies **and recomputes**: signature,
`spec_hash` recomputed from the spec, and the verdict re-derived from the
embedded results. A correctly signed certificate that lies about its own
verdict is rejected. `scripts/verify_evidence_bundle.py` checks the
signature and payload integrity with no project imports at all.

## Live example (real harness, mock provider)

```
suites: 9 | smoke tasks: 5
certificate #1: verdict=PASS | summary={'committed': 5, 'passed': 2, 'failed': 3, 'error': 0, ...}
   per-task: hello=passed, math=failed, capital=failed, list=failed, json=passed
verification checks: {'signature': True, 'spec_hash': True, 'verdict_recomputes': True, 'kind': True} → VALID
standalone verifier: kind: agent_eval → VALID (exit 0)
strict threshold 0.99 → verdict=FAIL        (the same results, judged strictly)
tampered (dropped task) → INVALID (exit 1)  (coverage loss detected)
```

## Anchoring

A certificate is an evidence bundle, so `POST /evidence/anchor
{"bundle_id": N}` writes its `payload_hash` to Solana (memo program) or the
labelled mock transport — a timestamped, tamper-evident record of the claim.

## MCP

- `agent_eval_certify(suite, provider, pass_threshold)` — run + certify
- `agent_eval_verify(certificate_id)` — per-check verification report

## Honest limitations

1. **Scoring is the harness's default** (expected-match). Certificates bind
   that rule; they do not vouch for its quality.
2. **One run, one certificate.** No repeated-run variance analysis yet
   (a suite run three times could expose flakiness; the spec format leaves
   room for a `repeat` field).
3. **Harness-mode coverage only.** Tasks that need external services
   (GAIA live web, SWE-bench repos) fail or skip on a bare machine — the
   verdict will say `FAIL`/`INCONCLUSIVE`, which is correct behaviour, not
   a certificate bug.
4. **No cross-signer registry.** Fingerprints identify signers; there's no
   revocation list.
