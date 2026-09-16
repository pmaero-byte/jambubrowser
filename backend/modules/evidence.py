"""
Evidence bundles — signed, verifiable claims about what the engine observed.

A bundle is a self-contained JSON document a third party can verify **without
trusting or running this codebase** (see ``scripts/verify_evidence_bundle.py``)::

    {
      "version": 1,
      "kind": "audit_report" | "x402_receipts" | "dcm_settlement",
      "created_at": 1760000000.0,
      "subject": { ... },              # what the claim is about
      "payload": { ... },              # the evidence (findings, receipts, verdict)
      "payload_canonical": "<string>", # exact bytes that were hashed + signed
      "payload_hash": "<sha256 hex>",
      "algorithm": "ed25519",
      "public_key": "<32-byte hex>",
      "signature": "<64-byte hex>"
    }

Design choices that make the claim checkable:

- The canonical serialization is produced by MeshPay's JS-faithful
  ``js_dumps`` (pinned against Node.js output) and **embedded** in the
  bundle, so a verifier needs no serializer to recompute the hash — it
  hashes the bytes that were signed.
- The signature covers a signing statement (version, kind, created_at,
  subject, payload_hash), so metadata cannot be swapped after signing.
- Keys: ``JAMBU_EVIDENCE_KEY`` (hex seed) or a 0600 key file (default
  ``~/.jambu/evidence_ed25519.key``); generated on first use. The public
  key is the identity — publish its fingerprint.
- Anchoring is optional and explicit: the bundle's ``payload_hash`` can be
  written to Solana (memo program) or the labelled mock transport through
  the same anchor module MeshPay uses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from backend.modules.meshpay import js_dumps

log = logging.getLogger("jambu.evidence")

BUNDLE_VERSION = 1
DEFAULT_KEY_PATH = "~/.jambu/evidence_ed25519.key"


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

@dataclass
class EvidenceKey:
    private_hex: str
    public_hex: str

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the raw public key, hex — the short identity."""
        return hashlib.sha256(bytes.fromhex(self.public_hex)).hexdigest()


def _private_key(seed: bytes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(seed)


def load_or_create_key(path: Optional[str] = None) -> EvidenceKey:
    """Load the signing key from env/file, generating a file key on first use."""
    from cryptography.hazmat.primitives import serialization

    env_seed = (os.environ.get("JAMBU_EVIDENCE_KEY") or "").strip()
    if env_seed:
        seed = bytes.fromhex(env_seed)
        key = _private_key(seed)
    else:
        key_path = Path(
            path or os.environ.get("JAMBU_EVIDENCE_KEY_PATH", DEFAULT_KEY_PATH)
        ).expanduser()
        if key_path.exists():
            seed = bytes.fromhex(key_path.read_text().strip())
            key = _private_key(seed)
        else:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = _private_key(os.urandom(32))
            key_path.write_text(
                key.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                ).hex()
            )
            try:
                key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
            except OSError:
                pass
            log.info("Generated evidence signing key at %s", key_path)
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return EvidenceKey(
        private_hex=key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ).hex(),
        public_hex=pub.hex(),
    )


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

def statement_payload(bundle: dict) -> dict:
    """The signed statement: metadata + payload hash (no signature fields)."""
    return {
        "version": bundle["version"],
        "kind": bundle["kind"],
        "created_at": bundle["created_at"],
        "subject": bundle["subject"],
        "payload_hash": bundle["payload_hash"],
    }


def build_bundle(
    kind: str, subject: dict, payload: Any, *,
    key: Optional[EvidenceKey] = None,
) -> dict:
    """Create a signed bundle. The canonical payload string is embedded."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = key or load_or_create_key()
    canonical = js_dumps(payload)
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    bundle = {
        "version": BUNDLE_VERSION,
        "kind": kind,
        "created_at": time.time(),
        "subject": subject,
        "payload": payload,
        "payload_canonical": canonical,
        "payload_hash": payload_hash,
        "algorithm": "ed25519",
        "public_key": key.public_hex,
    }
    statement = js_dumps(statement_payload(bundle))
    signature = _private_key(bytes.fromhex(key.private_hex)).sign(
        statement.encode("utf-8")
    )
    bundle["signature"] = signature.hex()
    bundle["statement_hash"] = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    return bundle


def verify_bundle(bundle: dict) -> dict:
    """Server-side verification (the standalone script is the authority).

    Returns ``{"valid": bool, "checks": {...}, "reason": str|None}``.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    checks = {
        "version": bundle.get("version") == BUNDLE_VERSION,
        "algorithm": bundle.get("algorithm") == "ed25519",
        "payload_hash": False,
        "statement_hash": False,
        "signature": False,
    }
    if not checks["version"] or not checks["algorithm"]:
        return {"valid": False, "checks": checks,
                "reason": "unsupported bundle version/algorithm"}

    canonical = bundle.get("payload_canonical")
    if not isinstance(canonical, str):
        return {"valid": False, "checks": checks, "reason": "missing canonical payload"}
    computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    checks["payload_hash"] = computed == bundle.get("payload_hash")

    # The canonical payload must parse to the same JSON value (order-blind).
    try:
        checks["payload_matches"] = json.loads(canonical) == bundle.get("payload")
    except Exception:
        checks["payload_matches"] = False

    statement = js_dumps(statement_payload(bundle))
    statement_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    checks["statement_hash"] = statement_hash == bundle.get("statement_hash")

    try:
        public = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(bundle["public_key"])
        )
        public.verify(bytes.fromhex(bundle["signature"]), statement.encode("utf-8"))
        checks["signature"] = True
    except (InvalidSignature, KeyError, ValueError):
        checks["signature"] = False

    valid = all(checks.values())
    reason = None if valid else next(
        (name for name, ok in checks.items() if not ok), "invalid"
    )
    return {"valid": valid, "checks": checks, "reason": reason}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def save_bundle(bundle: dict) -> dict:
    from backend.core.database import get_db

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO evidence_bundles
                (version, algorithm, kind, subject_json, payload_canonical,
                 payload_hash, statement_hash, signature, public_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(bundle["version"]),
                bundle["algorithm"],
                bundle["kind"],
                js_dumps(bundle["subject"]),
                bundle["payload_canonical"],
                bundle["payload_hash"],
                bundle["statement_hash"],
                bundle["signature"],
                bundle["public_key"],
                float(bundle["created_at"]),
            ),
        )
        conn.commit()
        bundle_id = cur.lastrowid
    out = dict(bundle)
    out["id"] = bundle_id
    return out


def list_bundles(limit: int = 50) -> list[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, kind, subject_json, payload_hash, statement_hash, signature,"
            " public_key, created_at, anchor_signature, anchor_cluster,"
            " anchor_transport FROM evidence_bundles"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        try:
            record["subject"] = json.loads(record.pop("subject_json") or "{}")
        except ValueError:
            record["subject"] = {}
        out.append(record)
    return out


def get_bundle(bundle_id: int) -> Optional[dict]:
    """Reconstruct the full, verifiable bundle (payload from canonical bytes)."""
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM evidence_bundles WHERE id = ?", (bundle_id,),
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    try:
        record["subject"] = json.loads(record.get("subject_json") or "{}")
        record["payload"] = json.loads(record.get("payload_canonical") or "null")
    except ValueError:
        record["subject"], record["payload"] = {}, None
    record.pop("subject_json", None)
    record["anchored"] = bool(record.get("anchor_signature"))
    return record


def record_anchor(bundle_id: int, record) -> None:
    """Persist an anchor record (MeshPay ``AnchorRecord``) on a bundle row."""
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            "UPDATE evidence_bundles SET anchor_signature = ?, anchor_cluster = ?,"
            " anchor_transport = ?, anchored_at = ? WHERE id = ?",
            (
                record.signature, record.cluster, record.transport,
                record.created_at, bundle_id,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Bundle sources
# ---------------------------------------------------------------------------

def audit_report_bundle(audit_id: int, key: Optional[EvidenceKey] = None) -> dict:
    """Sign a saved audit's findings + envelope."""
    from backend.routes.audit import _load_findings_for_audit

    url, findings, summary = _load_findings_for_audit(audit_id)
    payload = {
        "audited_url": url,
        "summary": summary,
        "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in findings],
    }
    return build_bundle(
        "audit_report",
        {"audit_id": audit_id, "url": url, "source": "audit_history"},
        payload, key=key,
    )


def x402_receipts_bundle(limit: int = 200, key: Optional[EvidenceKey] = None) -> dict:
    """Sign the x402 receipt window + its Merkle root."""
    from backend.modules.x402 import list_receipts, receipts_root

    receipts = list_receipts(limit)
    root = receipts_root(limit)
    payload = {
        "window_limit": limit,
        "merkle_root": root["root"],
        "receipts": receipts,
    }
    return build_bundle(
        "x402_receipts",
        {"count": len(receipts), "merkle_root": root["root"]},
        payload, key=key,
    )


async def dcm_settlement_bundle(limit: int = 200, key: Optional[EvidenceKey] = None) -> dict:
    """Sign an independent verification of the DCM settlement chain."""
    from backend.llm.config import get_config as get_llm_config
    from backend.modules.dcm_client import DcmClient
    from backend.modules.meshpay import verify_chain

    cfg = get_llm_config()
    client = DcmClient(base_url=cfg.dcm_base_url, auth=cfg.dcm_auth)
    log_data = await client.settlement_log(limit=limit)
    entries = log_data.get("entries") or []
    verdict = verify_chain(entries)
    payload = {
        "base_url": cfg.dcm_base_url,
        "node_count": log_data.get("count"),
        "verdict": verdict,
        "dcm_verification": {
            k: (log_data.get("verification") or {}).get(k)
            for k in ("valid", "entries", "brokenAt")
        },
        "entries": entries,
    }
    return build_bundle(
        "dcm_settlement",
        {"node": cfg.dcm_base_url, "chain_valid": verdict["valid"]},
        payload, key=key,
    )
