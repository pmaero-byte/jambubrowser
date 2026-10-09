"""Two-node mesh smoke test — the depth fix for BREADTH_DEPTH row 23.

Boots two real engine subprocesses (separate DBs, separate mesh state
dirs, one shared ``JAMBU_FEDERATION_KEY``) and drives the cross-node
flows over real HTTP:

1. identity + deterministic peer registration (``/p2p/register``),
2. a plain query crossing from node A to node B,
3. an *encrypted* federated query that returns node B's vault content —
   proof that both the transport and the shared-key envelope work,
4. consensus: proposal broadcast, vote forwarding, and the same
   decided outcome tallied independently on both nodes.

Why subprocesses and not TestClient: every defect this test pins
(``cast_vote`` 500, missing ``federated.query()``, missing
``/consensus/receive`` receiver, per-node Fernet keys, shared
``consensus.json`` across nodes) is invisible to a single-process test —
they are all *cross-node* or *cross-process* failures.

The fixture does all prerequisites (boot, peer registration both ways,
federation registration both ways, vault seeding, one warm-up query that
loads node B's embedding model — a cold SentenceTransformer load can
exceed the 10s timeout the federated query uses), so individual tests
can run alone.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent

# Seeded into node B's vault; the federated query must find it.
SEED_TEXT = (
    "Quantum entanglement lets particles share quantum state across distances."
)
SEED_QUERY = "quantum entanglement"
SEED_URL = "https://seed.example/qe"


# ---------------------------------------------------------------------------
# In-process unit tests (no boot needed)
# ---------------------------------------------------------------------------

class TestMeshState:
    def test_state_dir_env_override(self, tmp_path, monkeypatch):
        from backend.decentralized import mesh_state
        monkeypatch.setenv("JAMBU_MESH_STATE_DIR", str(tmp_path))
        assert mesh_state.state_dir() == tmp_path
        assert mesh_state.consensus_store_path() == tmp_path / "consensus.json"
        assert mesh_state.federation_path() == tmp_path / "federation.json"

    def test_federation_round_trip(self, tmp_path, monkeypatch):
        from backend.decentralized import mesh_state
        monkeypatch.setenv("JAMBU_MESH_STATE_DIR", str(tmp_path))
        assert mesh_state.read_federation() == {}
        mesh_state.write_federation({"node-b": {"node_url": "http://127.0.0.1:9"}})
        assert mesh_state.read_federation() == {
            "node-b": {"node_url": "http://127.0.0.1:9"}
        }

    def test_corrupt_registry_reads_as_empty_not_crash(self, tmp_path, monkeypatch):
        from backend.decentralized import mesh_state
        monkeypatch.setenv("JAMBU_MESH_STATE_DIR", str(tmp_path))
        (tmp_path / "federation.json").write_text("{not json", encoding="utf-8")
        assert mesh_state.read_federation() == {}

    def test_atomic_write_leaves_no_temp_file(self, tmp_path, monkeypatch):
        from backend.decentralized import mesh_state
        monkeypatch.setenv("JAMBU_MESH_STATE_DIR", str(tmp_path))
        mesh_state.write_federation({"n": {"node_url": "http://x"}})
        assert (tmp_path / "federation.json").exists()
        assert not (tmp_path / "federation.json.tmp").exists()



# ---------------------------------------------------------------------------
# Two-node fixture
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(path: str, payload: dict | None = None, timeout: float = 30.0,
          params: dict | None = None) -> dict:
    if params:
        path = f"{path}?{urlencode(params)}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(path, data=data, headers=headers,
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            raise AssertionError(
                f"HTTP {exc.code} from {path} (no JSON body)") from exc


def _seed_vault(db_path: Path) -> None:
    """Insert the seed document + embedding into a node DB file.

    Must run *before* the node boots so its own init_db migrations see a
    normal pre-existing database.
    """
    import numpy as np
    from sentence_transformers import SentenceTransformer

    from backend.core.database import init_db

    conn = init_db(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO documents (url, text) VALUES (?, ?)",
                    (SEED_URL, SEED_TEXT))
        doc_id = cur.lastrowid
        conn.commit()
        try:
            import sqlite_vec  # noqa: F401 — load() registers vec0
            sqlite_vec.load(conn)
            emb = SentenceTransformer("all-MiniLM-L6-v2").encode(SEED_TEXT)
            cur.execute("INSERT INTO vec_documents (id, embedding) VALUES (?, ?)",
                        (doc_id, emb.astype(np.float32).tobytes()))
            conn.commit()
        except Exception:
            pass  # text-only seed; vector tests self-skip via _vault_has_vectors
    finally:
        conn.close()


def _vault_has_vectors(db_path: Path) -> bool:
    """True if the seeded DB actually carries a usable vec0 row."""
    try:
        import sqlite3 as _sql

        import sqlite_vec as _vec
        conn = _sql.connect(str(db_path))
        try:
            _vec.load(conn)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM vec_documents")
            return cur.fetchone()[0] > 0
        finally:
            conn.close()
    except Exception:
        return False


def _boot_node(base: Path, name: str, federation_key: str,
               seed_db: bool = False) -> SimpleNamespace:
    port = _free_port()
    home = base / name
    home.mkdir(parents=True, exist_ok=True)
    db_path = home / "node.db"
    if seed_db:
        _seed_vault(db_path)
    state_dir = home / "mesh_state"
    state_dir.mkdir(exist_ok=True)
    log_path = home / "engine.log"

    env = {**os.environ,
           "JAMBU_ENABLE_DECENTRALIZED": "1",
           "JAMBU_DB_PATH": str(db_path),
           "JAMBU_MESH_STATE_DIR": str(state_dir),
           "JAMBU_FEDERATION_KEY": federation_key,
           "PYTHONUNBUFFERED": "1"}
    log_file = open(log_path, "w")  # noqa: SIM115 — closed in teardown
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.engine:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=log_file, stderr=subprocess.STDOUT,
    )

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    last = "no attempt"
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(errors="replace")[-800:]
            log_file.close()
            raise RuntimeError(f"node {name} exited early:\n{tail}")
        try:
            got = _http(f"{url}/health", timeout=2)
            if got.get("status") in ("ok", "online"):
                break
        except Exception as exc:  # not up yet
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.3)
    else:
        proc.terminate()
        log_file.close()
        raise RuntimeError(f"node {name} not healthy in 60s ({last})")

    return SimpleNamespace(name=name, url=url, port=port, proc=proc,
                           db_path=db_path, state_dir=state_dir,
                           log_path=log_path, log_file=log_file)


def _stop_node(node: SimpleNamespace) -> None:
    if node.proc.poll() is None:
        node.proc.send_signal(signal.SIGTERM)
        try:
            node.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node.proc.kill()
            node.proc.wait(timeout=2)
    node.log_file.close()


def _node_id(node: SimpleNamespace) -> str:
    return _http(f"{node.url}/p2p/info")["node_id"]


@pytest.fixture(scope="module")
def mesh(tmp_path_factory):
    """Two healthy engine processes with peers + federation wired both ways."""
    base = tmp_path_factory.mktemp("mesh")
    key = Fernet.generate_key().decode()
    node_a = _boot_node(base, "a", key)
    node_b = _boot_node(base, "b", key, seed_db=True)
    try:
        # Deterministic registration both ways (UDP broadcast is not used
        # here on purpose: it finds nothing in a sandboxed CI runner).
        _http(f"{node_a.url}/p2p/register", {"node_url": node_b.url})
        _http(f"{node_b.url}/p2p/register", {"node_url": node_a.url})
        _http(f"{node_a.url}/federation/register",
              {"node_id": _node_id(node_b), "node_url": node_b.url})
        _http(f"{node_b.url}/federation/register",
              {"node_id": _node_id(node_a), "node_url": node_a.url})
        # Warm node B's embedding model inside its 15s plain-query timeout
        # so the later federated query fits its tighter 10s peer timeout.
        _http(f"{node_a.url}/p2p/query",
              {"query": SEED_QUERY, "peer_id": _node_id(node_b)}, timeout=60.0)
    except Exception:
        _stop_node(node_a)
        _stop_node(node_b)
        raise
    yield SimpleNamespace(a=node_a, b=node_b)
    _stop_node(node_a)
    _stop_node(node_b)


# ---------------------------------------------------------------------------
# Integration tests: cross-node flows over real HTTP
# ---------------------------------------------------------------------------


class TestCrossNodeQuery:
    def test_plain_query_crosses_from_a_to_b(self, mesh):
        """A's ``/p2p/query`` reaches B's vault and returns the seed row."""
        if not _vault_has_vectors(mesh.b.db_path):
            pytest.skip("sqlite-vec unavailable: vault has no vector index")
        node_b_id = _node_id(mesh.b)
        out = _http(f"{mesh.a.url}/p2p/query",
                    {"query": SEED_QUERY, "peer_id": node_b_id},
                    timeout=60.0)
        assert out, f"empty result from peer query (B={node_b_id})"
        blob = json.dumps(out)
        assert SEED_URL in blob or "quantum" in blob.lower(), (
            f"seed document missing from cross-node result: {blob[:400]}"
        )

    def test_federated_query_returns_seeded_vault_content(self, mesh):
        """Encrypted federated query returns node B's seeded vault content.

        This is the proof that both the transport *and* the shared
        Fernet envelope work across processes: B can only answer the
        encrypted context if it holds the same key as A. Results are
        anonymized (hashes + encrypted snippets), so we assert on the
        responder id and source domain, not plaintext.
        """
        if not _vault_has_vectors(mesh.b.db_path):
            pytest.skip("sqlite-vec unavailable: vault has no vector index")
        out = _http(f"{mesh.a.url}/federated/query", {},
                    params={"query": SEED_QUERY, "max_results": 5},
                    timeout=60.0)
        results = out.get("results", [])
        assert results, f"federated query returned no results: {out!r:.400}"
        first = results[0]
        assert first.get("source_domain") == "seed.example", (
            f"seed domain missing from federated results: {first!r:.300}"
        )
        assert first.get("relevance_score", 0) >= 0.5
        assert first.get("document_hash"), "anonymized hash missing"


class TestCrossNodeConsensus:
    def test_proposal_broadcast_lands_on_peer(self, mesh):
        """A proposal created on A appears in B's proposal list.

        Broadcast now runs as a background task (the route answers
        immediately so a busy peer can't wedge the proposer), so poll
        B for up to ~30s instead of asserting on the first read.
        """
        created = _http(f"{mesh.a.url}/consensus/propose", {
            "title": "two-node smoke proposal",
            "description": "broadcast must land via /consensus/receive",
            "options": ["Yes", "No"],
            "required_nodes": 2,
        }, timeout=30.0)
        proposal_id = created.get("proposal_id")
        assert proposal_id, f"no proposal_id in {created!r:.300}"
        assert created.get("broadcast", {}).get("queued"), (
            f"broadcast not queued: {created!r:.300}")
        deadline = time.time() + 30
        ids: list = []
        while time.time() < deadline:
            listed = _http(f"{mesh.b.url}/consensus/list", timeout=15.0)
            ids = [p.get("id") for p in listed.get("proposals", [])]
            if proposal_id in ids:
                break
            time.sleep(1.0)
        assert proposal_id in ids, (
            f"proposal {proposal_id} never reached B in 30s (B has {ids})"
        )

    def test_votes_tally_on_both_nodes(self, mesh):
        """One vote on each node → both tallies agree (2 distinct voters).

        Vote forwarding is also queued in the background now, so poll
        until both tallies converge (or 30s elapses) instead of
        asserting on the first read.
        """
        proposal_id = _http(f"{mesh.a.url}/consensus/propose", {
            "title": "two-node vote tally",
            "description": "same outcome tallied independently",
            "options": ["Yes", "No"],
            "required_nodes": 2,
        }, timeout=30.0)["proposal_id"]
        assert proposal_id

        vote_a = _http(f"{mesh.a.url}/consensus/vote", {
            "proposal_id": proposal_id, "vote": "Yes", "voter": "node-a",
        }, timeout=30.0)
        assert vote_a.get("success"), f"A vote rejected: {vote_a!r:.300}"
        assert vote_a.get("broadcast", {}).get("queued"), (
            f"A forward not queued: {vote_a!r:.300}")
        # Wait for A's forwarded vote to land on B before B votes —
        # otherwise B tallies its local vote before the forward arrives
        # and the first read races.
        deadline = time.time() + 30
        while time.time() < deadline:
            tally = _http(f"{mesh.b.url}/consensus/tally/{proposal_id}",
                          timeout=15.0)
            if sum(tally.get("vote_counts", {}).values()) >= 1:
                break
            time.sleep(1.0)
        vote_b = _http(f"{mesh.b.url}/consensus/vote", {
            "proposal_id": proposal_id, "vote": "Yes", "voter": "node-b",
        }, timeout=30.0)
        assert vote_b.get("success"), f"B vote rejected: {vote_b!r:.300}"

        deadline = time.time() + 30
        counts_a: dict = {}
        counts_b: dict = {}
        while time.time() < deadline:
            tally_a = _http(f"{mesh.a.url}/consensus/tally/{proposal_id}",
                            timeout=15.0)
            tally_b = _http(f"{mesh.b.url}/consensus/tally/{proposal_id}",
                            timeout=15.0)
            assert tally_a.get("success") and tally_b.get("success")
            counts_a = tally_a.get("vote_counts", {})
            counts_b = tally_b.get("vote_counts", {})
            if (sum(counts_a.values()) == 2
                    and sum(counts_b.values()) == 2):
                break
            time.sleep(1.0)
        assert sum(counts_a.values()) == 2, f"A tally {counts_a}"
        assert sum(counts_b.values()) == 2, f"B tally {counts_b}"
        assert counts_a.get("Yes") == 2 and counts_b.get("Yes") == 2, (
            f"tallies diverged: A={counts_a} B={counts_b}"
        )
