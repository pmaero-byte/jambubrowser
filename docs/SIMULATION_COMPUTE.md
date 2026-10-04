# Simulation Compute — decentralised, verified job execution

Run simulation work across mesh nodes, **prove** the replicas agree, and pay
only for verified results.

The mesh already meters simulation work — DCM emits `simulation-charge`
receipts and MeshPay audits them. What was missing was everything in
between: a way to submit a job, freeze its definition, dispatch it, check
that the nodes actually computed the same thing, and settle. This document
covers that path (`backend/decentralized/simulation/`).

---

## The three defects this fixed

### 1. Simulation providers were paid $0

`meshpay/plan.py` derived provider entitlement from receipts where
`kind == "usage"`. But the mesh meters simulation work under its own receipt
kinds (`simulation-charge`, and whatever the Wasm lane emits). A node that
only ever ran simulations therefore produced no `usage` receipt, never
appeared in the payout plan, and was never paid — while its work was billed
to the customer.

Entitlement is now derived from the receipt **shape**, not its name:

```python
def is_provider_reward(entry) -> bool:
    if str(entry.get("kind", "")) in NON_PAYING_KINDS:   # settlement, dense-receipt
        return False
    if not entry.get("nodeId"):
        return False
    return _reward_of(entry) > 0
```

`settlement` still stays out of the plan (DCM already paid it — adding it
would double-count) and `dense-receipt` is a proof anchor, not a payment.
Each provider now also reports `rewardByKind`, so simulation revenue is a
visible line rather than something that silently vanishes.

### 2. Simulation results could not be verified

The verification ladder only had `exact` and `similarity` (difflib over
strings). Simulation output is numbers, and difflib over their string form
is not a verification method:

| primary | replica | difflib | truth |
|---|---|---|---|
| `100.0` | `1000.0` | **0.909 → PASS** | 10× wrong |
| `1.0` | `10.0` | **0.857 → PASS** | 10× wrong |
| `1.00` | `1.0000` | 0.800 → fail | identical |

The first two would have been certified `MATCH` against the default 0.85
gate. That is false assurance on the tier that decides what gets paid for.

The `numeric` comparator parses the payload and compares values under
absolute and relative tolerances:

- JSON objects/arrays are walked; numeric leaves compared by JSON path.
- Non-numeric leaves (status strings, labels) are ignored — real payloads
  have them.
- Flat numeric text (`1.0 2.0 3.0`) is accepted; *mixed* token sets are
  rejected, because prose is not numerically verifiable.
- `bool`, `NaN` and `Inf` are never treated as numbers.
- A replica that **omits** a numeric field the primary produced is a
  `MISMATCH` even when every shared value agrees — truncation is exactly the
  failure redundancy exists to catch.
- Anything unparseable is `ERROR`, never a silent pass.

The verdict reports where the disagreement was:

```json
{
  "verdict": "MISMATCH",
  "max_rel_deviation": 0.000999,
  "max_deviation_path": "$.trace[5]",
  "only_in_primary": [], "only_in_replica": []
}
```

Use it for solver output: `POST /verification/redundant` with
`comparator="numeric"`.

### 3. There was no job surface at all

No submit, no spec freeze, no idempotency, no failover, no persistence. And
`required_tier()` selected verification tiers by **USDC** while the mesh
meters in **DCT**, so a simulation's value never reached the policy deciding
whether it got replicated.

---

## The pipeline

```
  POST /simulation/quote          price in DCT, pick a tier, dispatch nothing
        │
  POST /simulation/submit
        │
        ├─ freeze spec  ─────────────► spec_hash  (before any node sees it)
        ├─ quote  ──────────────────► dct / usdc / tier
        ├─ dispatch ────────────────► N nodes run concurrently
        ├─ verify   ────────────────► numeric_report() on every replica
        ├─ settle   ────────────────► SETTLED (charged) | QUARANTINED (free)
        └─ persist  ────────────────► simulation_jobs row
        │
  GET /simulation/jobs/{id}/evidence ──► Ed25519 bundle (compute_simulation)
```

## Specs are frozen before dispatch

`spec_hash` is `sha256` over the canonical spec, using the same `js_dumps`
form MeshPay and `eval_cert` use. It is computed **before** any node is
contacted and never recomputed from a result.

That matters because redundancy is only meaningful if you can prove both
nodes were asked the same question. Fields that cannot affect the answer
(node id, wall-clock, which peer replied) are deliberately excluded.

`module_digest` pins **what code ran**. Supply the sha256 of the Wasm module
or model artefact and a swapped binary changes the spec hash — so replicas
can no longer be compared like-for-like against a different artefact.

Each result also gets an `execution_hash` binding *spec + node + output*, so
a receipt proves which artefact ran where.

## Pricing bridges DCT to the verification tier

```
dct  = base_dct + steps × per_step_dct + max(0, replicas-1) × per_replica_dct
usdc = dct × dct_usd_rate
tier = required_tier(usdc)
```

This is the bridge that was missing. `REDUNDANT` is not advisory: if a job's
value demands two nodes and only one is registered, the job is
**quarantined**, not quietly run single-node with a `MATCH` it did not earn.

Extra replicas cost extra — redundancy is real work, not free.

All rates are Jambubrowser **configuration, not an oracle**, and every
payload that shows USD says so (`rate_note`). DCM's receipts remain the
settlement source of truth.

## Disagreement is never paid for

| Outcome | Meaning | Charged |
|---|---|---|
| `SETTLED` | tier satisfied, all replicas agreed | quoted `dct` |
| `QUARANTINED` | replicas disagreed, or too few nodes completed | **0** |
| `FAILED` | no node completed the job | **0** |

`GET /simulation/jobs` returns `unpaidJobs` — dispatched work that earned
nothing. A rising number means nodes are drifting, and it is the signal the
operator actually wants.

## Retries cannot double-charge

Pass `idempotency_key`. A retry short-circuits **before dispatch** and
returns the stored job:

```bash
jambu sim run heat --steps 3000 --idempotency-key order-7   # dispatched, charged
jambu sim run heat --steps 3000 --idempotency-key order-7   # replay, charged 0
```

A client that times out mid-flight and retries cannot be billed twice.

## MeshPay reconciliation

`reconcile_window()` splits consumer charges per kind and compares them
against the provider rewards they paid for:

```json
{
  "by_kind": {"simulation-charge": {"jobs": 1, "chargedDct": 0.2}},
  "providerRewardDct": 0.15,
  "correlated": true,
  "unmatchedChargeIds": ["job-1"],
  "note": "1 charge(s) in this window have no matching provider reward receipt — work that was billed but not paid."
}
```

Correlation uses `executionHash` / `jobId` / `execHash` / `simulationId`
when DCM supplies one. When it does not, the result reports
`correlated: false` and only the aggregate totals are trustworthy — it never
claims a per-job match it cannot actually make.

## Consensus: the mesh decides, not the first responder

Replicas are compared against the **median** of their own outputs, and each
one is classified `AGREEING` or `DIVERGING`:

| Replicas | Outcome | Verdict | Settles? |
|---|---|---|---|
| n=1 | nothing to cross-check | `MATCH` | yes (flagged) |
| n=3, all agree | — | `MATCH` | yes |
| n=3, 2 agree + 1 drifts | strict majority | `MATCH_CONSENSUS` | **yes**, outlier named |
| n=2, they disagree | even split | `MISMATCH` (disputed) | no |
| any, <tier required> | too few completed | — | no |

This is what makes paying for a third replica worth it: one bad node no
longer blocks a correct answer. An earlier version compared every replica to
the *first* responder, which meant a correct answer got quarantined whenever
an honest node happened to answer first.

**An even split blames nobody.** With n=2 the median is exactly halfway
between two different answers, so both replicas "deviate" from it by
construction. Reporting that as two divergences would be a lie *and* would
poison both nodes' reputation, so a tie is reported as `disputed` with no node
penalised — the honest answer is "we cannot tell which side is right".

The median (not the mean) is used deliberately: one wildly wrong node cannot
drag the reference value toward itself.

Honest limit: majority consensus assumes a *minority* of nodes are faulty
(the usual `n > 3f` bound). It cannot detect a mesh that is uniformly wrong.

## Node health and reputation

Two separate signals, deliberately not conflated:

| Signal | Question | Effect |
|---|---|---|
| **health** | can this node run right now? | failing nodes are **excluded** |
| **reputation** | does it agree with the rest of the mesh? | disagreeing nodes are **deprioritised** |

**Health** mirrors the VPN pool's `EndpointHealth`: consecutive failures past
`JAMBU_SIM_FAILURE_THRESHOLD` quarantine a node for a bounded window. It is
keyed off the *window*, not a sticky `healthy` flag — otherwise an excluded
node would never run, never earn a success, and stay dead forever.

**Reputation** is derived from the scorecards that already existed in
`verification` (`worker_report()`), so `/verification/workers` and the
per-worker agreement rate stop being a report nobody acts on and become the
scheduling signal. Simulation jobs now publish their verdicts into
`worker_verdicts`, which is what closes that loop.

Dispatch order is: healthy → best reputation → node id (deterministic).
An unseen node scores `None` and ranks *below proven-good but above
proven-bad* — absence of evidence is not evidence of badness.

`GET /simulation/nodes` shows both side by side, in the order dispatch will
actually use.

Worked example — a node whose runtime crashes:

```
job 1: QUARANTINED  charged=0       nodes=[node-a]        flaky consec_fail=1
job 2: QUARANTINED  charged=0       nodes=[node-a]        flaky consec_fail=2 → quarantined
job 3: SETTLED      charged=0.00305 nodes=[node-a,node-b] flaky excluded
```

Two wasted jobs, then the mesh routes around the broken node for good.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/simulation/config` | pricing, tolerances, registered nodes |
| `GET` | `/simulation/nodes` | fleet health + reputation, in dispatch order |
| `POST` | `/simulation/quote` | price a job, name its tier, dispatch nothing |
| `POST` | `/simulation/submit` | dispatch, verify, settle or quarantine |
| `GET` | `/simulation/jobs` | history + spend totals |
| `GET` | `/simulation/jobs/{id}` | one job with attempts and verification |
| `GET` | `/simulation/jobs/{id}/evidence` | signed `compute_simulation` bundle |

```bash
curl -s localhost:8001/simulation/quote -H 'Content-Type: application/json' \
  -d '{"module":"heat","params":{"amplitude":1.0},"steps":5000,"replicates":2}'

curl -s -X POST localhost:8001/simulation/submit \
  -H 'Content-Type: application/json' \
  -d '{"module":"heat","seed":42,"steps":5000,"replicates":2,"idempotency_key":"order-42"}'
```

## CLI

```bash
jambu sim nodes                  # fleet health + reputation, in dispatch order
jambu sim quote heat --steps 5000 --replicas 2
jambu sim run   heat --steps 5000 --replicas 2 --idempotency-key order-42
jambu sim jobs --status QUARANTINED
```

## MCP

`simulation_quote`, `simulation_submit`, `simulation_jobs`,
`simulation_nodes` (in the `full` and `curated` profiles).

## Paywall

`POST /simulation/submit` is in `DEFAULT_PAID_ROUTES` as
`simulation_submit` (default $0.005/call), because dispatch is the metered,
verified-compute path — the same treatment `/dcm/infer` gets.
`POST /simulation/quote` stays free because it dispatches nothing.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `JAMBU_SIM_BASE_DCT` | `0.001` | per-job base cost |
| `JAMBU_SIM_PER_STEP_DCT` | `1e-5` | per simulation step |
| `JAMBU_SIM_REPLICATE_DCT` | `5e-5` | per extra replica |
| `JAMBU_SIM_DCT_USD` | `0.01` | configured rate, **not** an oracle |
| `JAMBU_SIM_DEFAULT_STEPS` | `1000` | default step count |
| `JAMBU_SIM_TIMEOUT_MS` | `30000` | per-node execution timeout |
| `JAMBU_SIM_MAX_REPLICAS` | `5` | cap on replicas |
| `JAMBU_SIM_FAILURE_THRESHOLD` | `2` | consecutive node failures before quarantine |
| `JAMBU_SIM_ABS_TOL` | `1e-12` | absolute comparison tolerance |
| `JAMBU_SIM_REL_TOL` | `1e-9` | relative comparison tolerance |
| `JAMBU_SIM_QUEUE` | off | set `1` to start the durable queue worker with the engine |
| `JAMBU_DB_PATH` | `rag_data.db` | SQLite path also used for the job queue |

## Adding a node

```python
from backend.decentralized import simulation

class MyNode(simulation.SimulationExecutor):
    node_id = "gpu-01"
    supports = ("wasm",)

    async def execute(self, spec: dict) -> dict:
        # spec is the FROZEN canonical spec — re-derive spec_hash to prove
        # you ran what was asked, and raise ExecutorError (never return a
        # partial result) on failure.
        return {"final_state": ..., "trace": [...]}

simulation.register_executor(MyNode())
```

`DcmWasmExecutor` does this against a real DCM node and rejects a response
whose echoed `specHash` does not match the job.

## Honest limits

1. **The local `DeterministicExecutor` is not a physics solver.** It is a
   real, seedable, bit-reproducible numeric kernel so that dispatch,
   replication, verification and settlement are exercisable end-to-end on a
   single node. Do not present it as CFD. Real workloads go through
   `DcmWasmExecutor`.
2. **`ATTESTED` is still unimplemented** — no TEE/hardware lane. Consensus
   catches a *minority* of faulty nodes (`n > 3f`); it cannot catch a mesh
   that is uniformly wrong, or two nodes colluding on the same answer.
   A 2-replica tie therefore settles nothing — use 3+ replicas if you need
   the mesh to keep working through one bad node.
3. **`charged_dct` is an internal ledger figure.** It records what verified
   work was owed. Actual money movement still runs through MeshPay against
   DCM's own receipts. There is no escrow and no cross-epoch netting here.
4. **Replicas are independent only in the sense of distinct node ids.** The
   in-process registry does not enforce independent machines; a real mesh
   must bind node identity to a host.
5. **Node health is in-memory.** `NODE_HEALTH` is a process-wide dict, so a
   restart forgets every quarantine and reputation record (the durable half
   is `worker_verdicts`, which does persist). The VPN pool has the same
   limitation and is called out in `docs/VPN.md`.
6. **Two dispatch modes** — the synchronous path keeps its request for the
   whole run (bounded by `JAMBU_SIM_TIMEOUT_MS`); the durable path
   (`POST /simulation/submit?queued=1` / `jambu sim run --queued` / MCP
   `simulation_submit(queued=true)`) writes a QUEUED row and a background
   `SimulationWorker` drains it FIFO, one job at a time. On startup the
   worker re-queues anything left in RUNNING, so a crash does not strand a
   job. Queue depth is whatever this one process can field — there is no
   multi-process fan-out yet, and a restart mid-job re-runs it (jobs are
   idempotent by `idempotency_key`, so a retried settle does not
   double-charge).
7. **DCM paging is still capped at 200 receipts per fetch** (see
   `docs/MESHPAY.md`), so very long chains still need an export endpoint
   before anchoring large epochs.

## Tests

`tests/test_simulation.py` — 108 tests, `tests/test_simulation_queue.py` — 7 tests across the numeric comparator, the
payout fix, spec freeze, quoting, dispatch, consensus, node health + quarantine,
reputation scheduling, the job store, idempotency, evidence, and the routes.