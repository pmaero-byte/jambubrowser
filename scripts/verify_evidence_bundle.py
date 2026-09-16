#!/usr/bin/env python3
"""
Standalone evidence-bundle verifier.

Verifies a Jambubrowser evidence bundle **without importing anything from
this project**: only the standard library and ``cryptography`` (Ed25519).
That is the point — a recipient can check an audit, a receipt window, or a
mesh-settlement verdict without running, installing, or trusting the engine
that produced it.

Usage:
    python3 scripts/verify_evidence_bundle.py bundle.json
    python3 scripts/verify_evidence_bundle.py --key-fingerprint bundle.json
    echo '<bundle json>' | python3 scripts/verify_evidence_bundle.py -

Exit codes:
    0  bundle is valid
    1  bundle is invalid (tampered, malformed, or wrong algorithm)
    2  usage / IO error

What is checked:
    1. Required fields and supported version/algorithm.
    2. sha256(payload_canonical) == payload_hash        (evidence intact)
    3. json.loads(payload_canonical) == payload         (no swapped payload)
    4. sha256(statement) == statement_hash              (metadata intact)
    5. Ed25519 signature over the signing statement     (authenticity)

The signing statement is rebuilt exactly as the spec below describes; every
field is read from the bundle, so no knowledge of the producer is required.
"""
from __future__ import annotations

import hashlib
import json
import sys

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2

SUPPORTED_VERSION = 1
SUPPORTED_ALGORITHM = "ed25519"


def js_number(value) -> str:
    """ECMAScript Number::toString — the producer serializes with JSON.stringify."""
    import math
    from decimal import Decimal

    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return "null"
    if value == 0:
        return "0"
    d = Decimal(repr(value))
    sign = "-" if d < 0 else ""
    d = abs(d)
    exponent = d.adjusted()
    digits = "".join(str(x) for x in d.as_tuple().digits).rstrip("0") or "0"
    if -7 < exponent < 21:
        if exponent >= len(digits) - 1:
            return sign + digits + "0" * (exponent - len(digits) + 1)
        if exponent >= 0:
            return sign + digits[: exponent + 1] + "." + digits[exponent + 1:]
        return sign + "0." + "0" * (-exponent - 1) + digits
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def js_dumps(value) -> str:
    """JSON.stringify-compatible canonical serialization (key order preserved)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(js_dumps(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{json.dumps(str(k), ensure_ascii=False)}:{js_dumps(v)}"
            for k, v in value.items()
        ) + "}"
    raise TypeError(f"unsupported type: {type(value).__name__}")


def signing_statement(bundle: dict) -> str:
    """Metadata + payload hash — exactly what the producer signs."""
    return js_dumps({
        "version": bundle["version"],
        "kind": bundle["kind"],
        "created_at": bundle["created_at"],
        "subject": bundle["subject"],
        "payload_hash": bundle["payload_hash"],
    })


REQUIRED = (
    "version", "kind", "created_at", "subject", "payload",
    "payload_canonical", "payload_hash", "algorithm", "public_key", "signature",
    "statement_hash",
)


def verify(bundle: dict) -> tuple[bool, list[tuple[str, bool, str]]]:
    checks: list[tuple[str, bool, str]] = []

    missing = [f for f in REQUIRED if f not in bundle]
    checks.append((
        "fields",
        not missing,
        "all required fields present" if not missing else f"missing: {', '.join(missing)}",
    ))
    if missing:
        return False, checks

    checks.append((
        "version", bundle["version"] == SUPPORTED_VERSION,
        f"version={bundle['version']} (supported: {SUPPORTED_VERSION})",
    ))
    checks.append((
        "algorithm", bundle["algorithm"] == SUPPORTED_ALGORITHM,
        f"algorithm={bundle['algorithm']}",
    ))

    canonical = bundle["payload_canonical"]
    computed_payload = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    checks.append((
        "payload_hash", computed_payload == bundle["payload_hash"],
        "sha256(payload_canonical) matches" if computed_payload == bundle["payload_hash"]
        else f"expected {bundle['payload_hash'][:16]}…, computed {computed_payload[:16]}…",
    ))

    try:
        same_payload = json.loads(canonical) == bundle["payload"]
    except Exception:
        same_payload = False
    checks.append((
        "payload_matches", same_payload,
        "canonical payload parses to the embedded payload" if same_payload
        else "payload does not match canonical bytes",
    ))

    statement = signing_statement(bundle)
    computed_statement = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    checks.append((
        "statement_hash", computed_statement == bundle["statement_hash"],
        "sha256(statement) matches" if computed_statement == bundle["statement_hash"]
        else "statement metadata was modified",
    ))

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(bundle["public_key"]))
        public.verify(bytes.fromhex(bundle["signature"]), statement.encode("utf-8"))
        signature_ok, note = True, "ed25519 signature valid"
    except ImportError:
        signature_ok, note = False, "cryptography not installed (pip install cryptography)"
    except Exception as e:
        signature_ok, note = False, f"signature invalid: {type(e).__name__}"
    checks.append(("signature", signature_ok, note))

    return all(ok for _, ok, _ in checks), checks


def fingerprint(public_key_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: verify_evidence_bundle.py <bundle.json|->")
        return EXIT_USAGE

    source = argv[1]
    try:
        if source == "-":
            raw = sys.stdin.read()
        else:
            with open(source, "r", encoding="utf-8") as fh:
                raw = fh.read()
        bundle = json.loads(raw)
    except Exception as e:
        print(f"error: cannot read bundle: {e}")
        return EXIT_USAGE

    valid, checks = verify(bundle)
    print(f"kind: {bundle.get('kind', '?')} | version: {bundle.get('version', '?')}")
    if "public_key" in bundle:
        print(f"signer fingerprint: {fingerprint(bundle['public_key'])}")
    for name, ok, note in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {note}")
    print("VALID" if valid else "INVALID")
    return EXIT_OK if valid else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main(sys.argv))
