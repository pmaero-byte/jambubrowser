# MeshPay — USDC settlement for DecentraCode mesh compute

MeshPay turns the DecentraCode Mesh's metered receipts into verifiable,
payable money. It is the bridge between DCM's billing ledger and on-chain
settlement: the mesh keeps metering in DCT; MeshPay audits that ledger
independently, summarises it as per-epoch Merkle roots, anchors those roots
on Solana, and computes USDC payouts.

## Why this shape (and not "a token")

DCM's own audit (`docs/dcm-moe/SYSTEM_AUDIT_AND_GAPS_2026-08-01.md`) leaves
the money path open: Stripe credits, or USDC on an L2. MeshPay takes the
second option but deliberately **does not tokenise DCT**:

- DCT stays an internal accounting ledger — no token sale, no liquidity
  games, no "activity disconnected from the token".
- Buyers pay in USDC; providers are paid in USDC computed from receipts.
- The chain is used for what it is good at: **tamper-evident anchoring**
  and, later, escrow — not for price discovery.

## Pipeline

```
DCM node                          Jambubrowser engine
────────                          ───────────────────
metering (Wasm / tokens / sims)
  └─ settlementLog (hash-chained receipts)
        │  GET /api/billing/settlement-log
        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ /meshpay/audit   independent chain replay + epoch grouping  │
   │ /meshpay/anchor  Merkle root → Solana memo (or mock)        │
   │ /meshpay/anchors anchor history, re-verified every load     │
   │ /meshpay/receipts/{i}  inclusion proof for one receipt      │
   └─────────────────────────────────────────────────────────────┘
        │
        ▼
   Browser app → MeshPay panel (verdicts, epochs, payouts, anchors)
   MCP clients → meshpay_audit / meshpay_anchor
```

### 1. Independent receipt verification (`backend/modules/meshpay/`)

DCM hashes each receipt with
`sha256(JSON.stringify(entry without invoiceHash))`. To verify that chain
*independently*, MeshPay re-implements JavaScript's canonical JSON:

- `jsjson.py` — ECMAScript number formatting (`0.00001`, not `1e-05` —
  DCM's inference rates are exactly `1e-5`/`5e-5`), key-order preservation,
  string escaping.
- `receipts.py` — chain replay + economic aggregation (minted, commission,
  burned, charged, per-account deltas).

The serializer is pinned against **real Node.js output** in
`tests/test_meshpay.py::TestJsNumber`, and the whole verifier is
cross-checked against a Node.js recomputation of live DCM receipts during
development. The audit response reports both our verdict and DCM's own, so
disagreement is visible rather than averaged away.

Windowed fetches (DCM caps `settlement-log` at 200 entries) verify links
*within* the window and set `window_truncated` + `first_prev_hash`.

### 2. Epochs and payouts (`plan.py`)

- An epoch is a contiguous window of receipts (`JAMBU_MESHPAY_EPOCH_SIZE`,
  default 50).
- Provider entitlement = `reward` on `usage` receipts per `nodeId`.
- `settlement` receipts are shown separately as *already settled on DCM*
  and are **not** double-counted.
- MeshPay applies `JAMBU_MESHPAY_FEE_PCT` (default 15%) and converts at
  `JAMBU_MESHPAY_DCT_USD` (default 0.01) — a **configured rate, not an
  oracle**. Every USD figure in the API/UI carries that note.

### 3. Anchoring (`anchor.py`)

MVP uses the **SPL Memo program** (`MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`):
a transaction whose memo is

```
meshpay:v1:<epoch>:<receipts>:<merkle_root>
```

No custom program, no deploy, no audit — and a memo is exactly as
tamper-evident as custom state. Merkle spec (canonical, recomputable by
third parties):

- `leaf = sha256(0x00 || invoiceHash)`
- `node = sha256(0x01 || left || right)`
- odd levels promote the last node

`cluster="mock"` (the default) writes a deterministic offline signature and
labels the record `transport="mock"`. Any real cluster requires `solders`,
an RPC URL, and a funded keypair; if any is missing, the call **fails
loudly** — a database must never claim a chain transaction that did not
happen. Anchor records store the `epoch_size` they were computed with, so
re-verification always regroups receipts the same way.

## Devnet runbook

```bash
# 1. One-time: toolchain + a funded devnet key (needs ~0.05 SOL for fees)
#    macOS: brew install solana   (or the installer from solana.com)
solana-keygen new --outfile ~/.config/solana/meshpay-devnet.json
solana airdrop 1 --url devnet                       # devnet SOL for fees
# (if the airdrop is rate-limited: https://faucet.solana.com)

# 2. Point the engine at devnet
export JAMBU_MESHPAY_CLUSTER=devnet
export JAMBU_MESHPAY_KEYPAIR=~/.config/solana/meshpay-devnet.json
export JAMBU_MESHPAY_RPC_URL=https://api.devnet.solana.com
export JAMBU_MESHPAY_DCT_USD=0.01        # configured rate
export JAMBU_MESHPAY_FEE_PCT=0.15

# 3. Run a DCM node and anchor the latest epoch
cd ../decentracode/backend && npm start &          # DCM on :3001
jambu  # engine on :8001

curl -s localhost:8001/meshpay/audit | jq .verification
curl -s -X POST localhost:8001/meshpay/anchor \
     -H 'Content-Type: application/json' -d '{}' | jq .
# → { transport: "solana", cluster: "devnet", signature: "...", explorer_url: "..." }

# 4. Anyone can verify: the explorer link shows the memo, and
#    GET /meshpay/anchors re-computes the root from the live receipts.
```

UI: browser app → **MeshPay** panel. MCP: `meshpay_audit`,
`meshpay_anchor`.

## What is NOT done yet (honest list)

1. **USDC transfer to providers is not implemented** — anchors prove *what*
   is owed; the actual SPL-token payout (and the treasury/escrow program)
   is the next milestone. Providers still withdraw via DCM's
   operator-approved flow.
2. **Receipts are not fetched in full** — DCM's API caps at 200 entries per
   fetch; long chains need paging or an export endpoint on the DCM side
   before anchoring very large epochs.
3. **Forward markets (Phase 2 of the MeshPay design)** — prepaying to
   reserve future compute — are not started.
4. **Provider identity binding** — MeshPay pays `nodeId`s from receipts; a
   node→wallet map (DCM has `wallet_address` on `compute_nodes`) needs to
   be threaded through before payouts execute.
