# Evidence bundles — signed claims a third party can verify

An evidence bundle is a self-contained JSON document that says *"this is
what the engine observed, at this time, about this subject"* — signed with
Ed25519 so it cannot be altered afterwards, and verifiable by someone who
**does not run, install, or trust this codebase**.

```
scripts/verify_evidence_bundle.py bundle.json     # exit 0 = VALID, 1 = INVALID
```

The verifier imports the standard library plus `cryptography` (Ed25519) and
nothing else — no `backend.*`, no project code. That is the point.

## What a bundle contains

```json
{
  "version": 1,
  "kind": "audit_report | x402_receipts | dcm_settlement",
  "created_at": 1760000000.0,
  "subject": { "audit_id": 1, "url": "https://example.com" },
  "payload": { "findings": [ ... ] },
  "payload_canonical": "<exact bytes that were hashed and signed>",
  "payload_hash": "<sha256 hex>",
  "statement_hash": "<sha256 hex of the signed statement>",
  "algorithm": "ed25519",
  "public_key": "<32-byte hex>",
  "signature": "<64-byte hex>"
}
```

Three properties make it checkable:

1. **Canonical bytes are embedded.** The producer serializes with MeshPay's
   JS-faithful `js_dumps` (pinned against Node.js output) and signs the
   *bytes*, so verifiers need no serializer of their own — they hash what
   was signed. The payload must also parse back to the embedded JSON value.
2. **Metadata is signed too.** The signature covers a signing statement —
   `version`, `kind`, `created_at`, `subject`, `payload_hash` — so a
   timestamp or subject cannot be swapped after signing.
3. **Anchoring is optional and explicit.** A bundle's `payload_hash` can be
   written to Solana (memo program) or the labelled mock transport through
   the same anchor module MeshPay uses.

## What signing proves — and what it does not

- **Proves:** the payload has not changed since signing; it was signed by
  the holder of the public key whose fingerprint you see; the metadata is
  part of the same statement.
- **Does not prove:** that the audit's findings are correct, that the chain
  observation is true, or that the signer is honest. A signature makes a
  claim *accountable*, not *true* — verifiers must decide whether they
  trust the signer's fingerprint. That is the same trust model as any
  signed attestation.

## API

| Route | Purpose |
|---|---|
| `GET /evidence/key` | Signing identity: algorithm, public key, fingerprint |
| `POST /evidence/audit/{audit_id}` | Sign a saved audit's findings + envelope |
| `POST /evidence/x402-receipts?limit=N` | Sign the x402 receipt window + Merkle root |
| `POST /evidence/dcm-settlement?limit=N` | Sign an independent DCM chain-verification verdict |
| `GET /evidence/bundles?limit=N` | Bundle history + anchor status |
| `GET /evidence/bundles/{id}` | Full bundle, ready for the standalone verifier |
| `POST /evidence/verify` | Verify a posted bundle (convenience) |
| `POST /evidence/anchor` | Anchor a bundle's payload hash (`{"bundle_id": N}`) |

## Keys

- `JAMBU_EVIDENCE_KEY` — hex Ed25519 seed (environment-managed keys, CI).
- `JAMBU_EVIDENCE_KEY_PATH` — file path, default
  `~/.jambu/evidence_ed25519.key`, generated on first use with mode `0600`.

Publish the fingerprint (`GET /evidence/key`) so verifiers know which key to
expect. Rotating keys changes the identity: bundles stay verifiable with the
*old* public key, which is embedded in each bundle — keep old public keys
around if you rotate.

## Worked example (from the live verification run)

```bash
# 1. Produce a bundle for a saved audit
curl -s -X POST localhost:8001/evidence/audit/1 -o bundle.json

# 2. Verify it without the engine
python3 scripts/verify_evidence_bundle.py bundle.json
# kind: audit_report | version: 1
# signer fingerprint: 10ba682c8ad13513971e8b56881aab8bd702bb807796eca81932c735a94d6e6d
#   [PASS] fields / version / algorithm / payload_hash / payload_matches
#          / statement_hash / signature
# VALID

# 3. Tamper and watch it fail
python3 - <<'PY'
import json; b = json.load(open("bundle.json"))
b["payload"]["findings"].append({"severity": "critical", "title": "injected"})
json.dump(b, open("tampered.json", "w"))
PY
python3 scripts/verify_evidence_bundle.py tampered.json   # INVALID, exit 1

# 4. Anchor the x402 receipts bundle (mock by default, devnet via MeshPay env)
curl -s -X POST localhost:8001/evidence/x402-receipts -o rc.json
curl -s -X POST localhost:8001/evidence/anchor \
     -H 'Content-Type: application/json' \
     -d "{\"bundle_id\": $(jq .id rc.json)}" | jq .
# { "transport": "mock", "signature": "mock:…", "cluster": "mock", ... }
```

## Honest limitations

1. **Key custody is file-based** (0600 file or env var). Production should
   move it to the vault/KMS; revocation is not implemented.
2. **Bundles are not yet exposed over MCP** — routes and CLI-style curl for
   now (`evidence_*` MCP tools are a small follow-up).
3. **`x402_receipts` bundles capture settled *and* failed settlements** — a
   verifier sees exactly what the engine recorded, including
   `error_skipped` rows for work that was authorized but not charged.
4. **The canonical serializer is ours.** It is pinned against Node.js on
   representative values, but any change to it is a bundle-breaking change —
   treat `js_dumps` as frozen for bundle format v1.
