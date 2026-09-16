"""
x402 paywall — HTTP-native payments for agent access (protocol v2).

Implements the x402 v2 core flow for Jambubrowser's metered endpoints:

1. No payment → **402** with the canonical base64 ``PAYMENT-REQUIRED``
   response header carrying the ``PaymentRequired`` object (protocol
   version, ``resource`` info, ``accepts[]`` payment requirements).
2. Client pays and retries with a base64 ``PAYMENT-SIGNATURE`` header
   carrying ``PaymentPayload``.
3. We verify (``POST {facilitator}/verify``), run the resource, settle
   (``POST {facilitator}/settle``), and record a receipt. When the
   response is buffered, the ``PAYMENT-RESPONSE`` header carries the
   ``SettleResponse``.

This is the **authorization** flow from the spec (verify → resource →
settle → respond). Long-running SSE audits settle after the stream ends.

Callers presenting a valid engine API key bypass the paywall: the account
path stays metered by the existing key/quota system, while anonymous
agents pay per call. Disabled by default (``JAMBU_X402_ENABLED=1`` opts
in) so existing local installs are unaffected.

Facilitators: ``mock`` (offline, deterministic, clearly labelled in
config/receipts) or an HTTP URL (the real thing). Nothing here ever
fabricates a chain transaction in ``http`` mode.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import httpx
from fastapi import HTTPException, Request, Response

log = logging.getLogger("jambu.x402")

X402_VERSION = 2
USDC_DECIMALS = 6

# Defaults: Base Sepolia testnet + its USDC (the address from the spec example).
DEFAULT_NETWORK = "eip155:84532"
DEFAULT_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

PRICE_DEFAULTS = {
    "audit_quick": "20000",   # $0.02
    "audit_full": "100000",   # $0.10
    "dcm_infer": "1000",      # $0.001
}

PRICE_DESCRIPTIONS = {
    "audit_quick": "Quick scan: 3 AI employees (Security, Performance, UX)",
    "audit_full": "Full audit: 6 AI employees with SARIF/HTML report",
    "dcm_infer": "Prompt completion on the local DecentraCode mesh node",
}


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or "").strip() or default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class X402Config:
    enabled: bool = False
    pay_to: str = ""
    network: str = DEFAULT_NETWORK
    asset: str = DEFAULT_ASSET
    facilitator: str = "mock"          # "mock" | base URL
    max_timeout_seconds: int = 60
    prices: dict = field(default_factory=lambda: dict(PRICE_DEFAULTS))

    @classmethod
    def from_env(cls) -> "X402Config":
        prices = {
            key: _env(f"JAMBU_X402_PRICE_{key.upper()}", default)
            for key, default in PRICE_DEFAULTS.items()
        }
        return cls(
            enabled=_env_bool("JAMBU_X402_ENABLED"),
            pay_to=_env("JAMBU_X402_PAY_TO"),
            network=_env("JAMBU_X402_NETWORK", DEFAULT_NETWORK),
            asset=_env("JAMBU_X402_ASSET", DEFAULT_ASSET),
            facilitator=_env("JAMBU_X402_FACILITATOR", "mock"),
            max_timeout_seconds=int(_env("JAMBU_X402_MAX_TIMEOUT", "60") or 60),
            prices=prices,
        )

    def describe(self) -> dict:
        return {
            "enabled": self.enabled,
            "network": self.network,
            "asset": self.asset,
            "asset_symbol": "USDC",
            "pay_to": self.pay_to,
            "facilitator_mode": "mock" if self.is_mock else "http",
            "facilitator_url": "" if self.is_mock else self.facilitator,
            "prices": {
                key: {
                    "amount_atomic": amount,
                    "usdc": int(amount) / 10 ** USDC_DECIMALS,
                    "description": PRICE_DESCRIPTIONS.get(key, ""),
                }
                for key, amount in self.prices.items()
            },
            "note": (
                "mock facilitator never touches a chain — set "
                "JAMBU_X402_FACILITATOR to a URL for real settlement"
                if self.is_mock else ""
            ),
        }

    @property
    def is_mock(self) -> bool:
        return not self.facilitator.startswith("http")

    def price_for(self, key: str) -> Optional[str]:
        return self.prices.get(key)


# ---------------------------------------------------------------------------
# Wire objects (spec shapes)
# ---------------------------------------------------------------------------

def build_requirements(cfg: X402Config, price_key: str, resource_url: str) -> dict:
    """A ``PaymentRequirements`` object for one endpoint."""
    amount = cfg.prices.get(price_key)
    if amount is None:
        raise KeyError(f"unknown x402 price key: {price_key}")
    return {
        "scheme": "exact",
        "network": cfg.network,
        "amount": str(amount),
        "asset": cfg.asset,
        "payTo": cfg.pay_to,
        "maxTimeoutSeconds": cfg.max_timeout_seconds,
        "extra": {"name": "USDC", "version": "2"},
    }


def build_payment_required(
    cfg: X402Config, price_key: str, resource_url: str,
    *, error: Optional[str] = None,
) -> dict:
    """The ``PaymentRequired`` object (body + ``PAYMENT-REQUIRED`` header)."""
    body: dict[str, Any] = {
        "x402Version": X402_VERSION,
        "resource": {
            "url": resource_url,
            "description": PRICE_DESCRIPTIONS.get(price_key, "Paid resource"),
            "mimeType": "application/json",
        },
        "accepts": [build_requirements(cfg, price_key, resource_url)],
    }
    if error:
        body["error"] = error
    return body


def encode_header(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def decode_payment_signature(header_value: Optional[str]) -> Optional[dict]:
    """Decode the client's ``PAYMENT-SIGNATURE`` header (base64 JSON)."""
    if not header_value:
        return None
    try:
        raw = base64.b64decode(header_value.strip(), validate=True)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def settlement_header(settle: "SettleResult") -> str:
    body: dict[str, Any] = {
        "success": settle.success,
        "transaction": settle.transaction,
        "network": settle.network,
    }
    if settle.payer:
        body["payer"] = settle.payer
    if not settle.success and settle.error_reason:
        body["errorReason"] = settle.error_reason
    return encode_header(body)


# ---------------------------------------------------------------------------
# Facilitators
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    is_valid: bool
    invalid_reason: Optional[str] = None
    payer: Optional[str] = None


@dataclass
class SettleResult:
    success: bool
    transaction: str = ""
    network: str = DEFAULT_NETWORK
    payer: Optional[str] = None
    error_reason: Optional[str] = None


class Facilitator(Protocol):
    mode: str

    async def verify(self, payload: dict, requirements: dict) -> VerifyResult: ...

    async def settle(self, payload: dict, requirements: dict) -> SettleResult: ...


def _authorization(payload: dict) -> dict:
    return ((payload or {}).get("payload") or {}).get("authorization") or {}


class MockFacilitator:
    """Offline, deterministic facilitator for tests/demos.

    Accepts payloads whose ``authorization.nonce`` starts with ``mock-`` and
    whose ``value`` matches the required amount. Never touches a chain; the
    transaction id is a ``mocktx:`` hash.
    """

    mode = "mock"

    async def verify(self, payload: dict, requirements: dict) -> VerifyResult:
        auth = _authorization(payload)
        nonce = str(auth.get("nonce") or "")
        if not nonce.startswith("mock-"):
            return VerifyResult(False, "invalid_payload", auth.get("from"))
        if str(auth.get("value")) != str(requirements.get("amount")):
            return VerifyResult(
                False, "invalid_exact_evm_payload_authorization_value_mismatch",
                auth.get("from"),
            )
        if str(auth.get("to", "")).lower() != str(requirements.get("payTo", "")).lower():
            return VerifyResult(
                False, "invalid_exact_evm_payload_recipient_mismatch", auth.get("from"),
            )
        return VerifyResult(True, None, auth.get("from"))

    async def settle(self, payload: dict, requirements: dict) -> SettleResult:
        auth = _authorization(payload)
        nonce = str(auth.get("nonce") or "")
        digest = hashlib.sha256(f"mock:{nonce}:{requirements.get('amount')}".encode())
        return SettleResult(
            success=True,
            transaction=f"mocktx:{digest.hexdigest()}",
            network=str(requirements.get("network") or DEFAULT_NETWORK),
            payer=auth.get("from"),
        )


class HttpFacilitator:
    """Real facilitator: ``POST {base}/verify`` then ``/settle``."""

    mode = "http"

    def __init__(self, base_url: str, timeout: float = 20.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    async def _post(self, path: str, payload: dict, requirements: dict) -> dict:
        body = {
            "x402Version": X402_VERSION,
            "paymentPayload": payload,
            "paymentRequirements": requirements,
        }
        async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}{path}", json=body)
        if resp.status_code != 200:
            raise RuntimeError(
                f"facilitator {path} HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"facilitator {path} returned non-object JSON")
        return data

    async def verify(self, payload: dict, requirements: dict) -> VerifyResult:
        try:
            data = await self._post("/verify", payload, requirements)
        except Exception as e:
            log.warning("facilitator verify failed: %s", e)
            return VerifyResult(False, "unexpected_verify_error")
        return VerifyResult(
            bool(data.get("isValid")),
            data.get("invalidReason"),
            data.get("payer"),
        )

    async def settle(self, payload: dict, requirements: dict) -> SettleResult:
        try:
            data = await self._post("/settle", payload, requirements)
        except Exception as e:
            log.warning("facilitator settle failed: %s", e)
            return SettleResult(
                False, "", str(requirements.get("network") or DEFAULT_NETWORK),
                error_reason="unexpected_settle_error",
            )
        return SettleResult(
            success=bool(data.get("success")),
            transaction=str(data.get("transaction") or ""),
            network=str(data.get("network") or requirements.get("network") or ""),
            payer=data.get("payer"),
            error_reason=data.get("errorReason"),
        )


def get_facilitator(cfg: X402Config) -> Facilitator:
    if cfg.is_mock:
        return MockFacilitator()
    return HttpFacilitator(cfg.facilitator)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

def _receipt_hash(record: dict) -> str:
    from backend.modules.meshpay import js_dumps

    canonical = js_dumps({
        "resource": record.get("resource"),
        "payer": record.get("payer"),
        "transaction": record.get("transaction"),
        "amount": record.get("amount"),
        "network": record.get("network"),
        "created_at": record.get("created_at"),
    })
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_receipt(
    *, resource: str, requirements: dict, payer: Optional[str],
    transaction: Optional[str], nonce: Optional[str], status: str,
    facilitator_mode: str,
) -> dict:
    from backend.core.database import get_db

    created = time.time()
    record = {
        "resource": resource,
        "amount": str(requirements.get("amount") or ""),
        "asset": str(requirements.get("asset") or ""),
        "network": str(requirements.get("network") or ""),
        "pay_to": str(requirements.get("payTo") or ""),
        "payer": payer,
        "transaction": transaction,
        "nonce": nonce,
        "status": status,
        "facilitator": facilitator_mode,
        "created_at": created,
    }
    record["receipt_hash"] = _receipt_hash(record)
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO x402_receipts
                (resource, amount, asset, network, pay_to, payer, tx,
                 nonce, status, facilitator, receipt_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["resource"], record["amount"], record["asset"],
                record["network"], record["pay_to"], record["payer"],
                record["transaction"], record["nonce"], record["status"],
                record["facilitator"], record["receipt_hash"], created,
            ),
        )
        conn.commit()
        record["id"] = cur.lastrowid
    return record


def list_receipts(limit: int = 50) -> list[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM x402_receipts ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        record["transaction"] = record.pop("tx", None)  # `transaction` is reserved in SQLite
        out.append(record)
    return out


def receipts_root(limit: int = 200) -> dict:
    """Merkle root over receipt hashes (feeds MeshPay-style anchoring)."""
    from backend.core.database import get_db
    from backend.modules.meshpay import merkle_root

    with get_db() as conn:
        rows = conn.execute(
            "SELECT receipt_hash FROM x402_receipts "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    hashes = [r["receipt_hash"] for r in rows]
    return {
        "receipts": len(hashes),
        "root": merkle_root(hashes),
        "window_limit": limit,
    }


# ---------------------------------------------------------------------------
# ASGI middleware (the paywall)
# ---------------------------------------------------------------------------
#
# A dependency cannot deliver the ``PAYMENT-RESPONSE`` header: FastAPI merges
# the injected Response *before* dependency teardown, and teardown after a
# streaming response is far too late. The middleware below is therefore the
# paywall: it authorizes (verify) before the resource, runs the resource,
# settles after the response body completes, and — for buffered responses —
# holds the start message back long enough to attach the settlement header.
# SSE responses stream through and settle at the end (receipts are the record).

DEFAULT_PAID_ROUTES = {
    ("POST", "/audit/run"): "audit_full",
    ("POST", "/audit/quick"): "audit_quick",
    ("POST", "/dcm/infer"): "dcm_infer",
}

_BUFFER_LIMIT = 262_144  # bytes; larger bodies switch to passthrough


def _asgi_headers(headers) -> list[tuple[bytes, bytes]]:
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]


async def _send_json(send, status: int, body: dict, headers: dict | None = None) -> None:
    payload = json.dumps(body).encode("utf-8")
    out = {
        "content-type": "application/json",
        "content-length": str(len(payload)),
    }
    out.update(headers or {})
    await send({
        "type": "http.response.start", "status": status,
        "headers": _asgi_headers(out),
    })
    await send({"type": "http.response.body", "body": payload, "more_body": False})


def _nonce_used(nonce: Optional[str]) -> bool:
    """Server-side replay guard (defense in depth; EIP-3009 also protects)."""
    if not nonce:
        return False
    from backend.core.database import get_db

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM x402_receipts WHERE nonce = ? AND status = 'settled' LIMIT 1",
                (nonce,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


class X402Middleware:
    """Charge configured routes per call, settling after the work completes."""

    def __init__(self, app, routes: Optional[dict] = None,
                 buffer_limit: int = _BUFFER_LIMIT):
        self.app = app
        self.routes = dict(routes or DEFAULT_PAID_ROUTES)
        self.buffer_limit = buffer_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        price_key = self.routes.get((scope.get("method", "GET"), scope.get("path", "")))
        if price_key is None:
            return await self.app(scope, receive, send)

        cfg = X402Config.from_env()
        header_map = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        if not cfg.enabled or _header_api_key(header_map):
            return await self.app(scope, receive, send)
        if not cfg.pay_to:
            return await _send_json(send, 503, {
                "detail": (
                    "x402 is enabled but JAMBU_X402_PAY_TO is not configured — "
                    "refusing to serve paid routes for free."
                ),
            })

        scheme = header_map.get("x-forwarded-proto", "http")
        host = header_map.get("host", "localhost")
        resource_url = f"{scheme}://{host}{scope.get('path', '')}"

        def payment_required(error: str):
            return _send_json(
                send, 402,
                build_payment_required(cfg, price_key, resource_url, error=error),
                headers={
                    "PAYMENT-REQUIRED": encode_header(build_payment_required(
                        cfg, price_key, resource_url, error=error,
                    )),
                },
            )

        payload = decode_payment_signature(header_map.get("payment-signature"))
        if payload is None:
            return await payment_required("PAYMENT-SIGNATURE header is required")

        requirements = build_requirements(cfg, price_key, resource_url)
        nonce = str(_authorization(payload).get("nonce") or "") or None
        if _nonce_used(nonce):
            return await payment_required("nonce_already_used")

        facilitator = get_facilitator(cfg)
        verify = await facilitator.verify(payload, requirements)
        if not verify.is_valid:
            return await payment_required(verify.invalid_reason or "payment_invalid")

        state = {"start": None, "chunks": [], "buffering": True}
        settled = {"done": False}

        async def settle_and_record(status_code: int) -> Optional["SettleResult"]:
            if settled["done"]:
                return None
            settled["done"] = True
            if status_code >= 400:
                try:
                    record_receipt(
                        resource=resource_url, requirements=requirements,
                        payer=verify.payer, transaction=None, nonce=nonce,
                        status="error_skipped", facilitator_mode=facilitator.mode,
                    )
                except Exception:
                    log.warning("x402 receipt write failed", exc_info=True)
                return None
            settle = await facilitator.settle(payload, requirements)
            try:
                record_receipt(
                    resource=resource_url, requirements=requirements,
                    payer=settle.payer or verify.payer,
                    transaction=settle.transaction, nonce=nonce,
                    status="settled" if settle.success else "settle_failed",
                    facilitator_mode=facilitator.mode,
                )
            except Exception:
                log.warning("x402 receipt write failed", exc_info=True)
            if not settle.success:
                log.warning(
                    "x402 settle failed for %s: %s", resource_url, settle.error_reason,
                )
            return settle

        async def flush_start(extra: Optional[dict] = None) -> None:
            message = state["start"]
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in message.get("headers", [])
            }
            headers.update(extra or {})
            await send({
                "type": "http.response.start",
                "status": message["status"],
                "headers": _asgi_headers(headers),
            })

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["start"] = message
                is_stream = any(
                    k.decode("latin-1").lower() == "content-type"
                    and v.decode("latin-1").startswith("text/event-stream")
                    for k, v in message.get("headers", [])
                )
                if is_stream:
                    # Streams cannot wait for settlement: forward immediately
                    # and settle when the body completes (receipts record it).
                    state["buffering"] = False
                    return await send(message)
                return  # buffered: hold the start until the body completes

            if message["type"] != "http.response.body":
                return await send(message)

            status = (state["start"] or {}).get("status", 200)
            if not state["buffering"]:
                await send(message)
                if not message.get("more_body"):
                    await settle_and_record(status)
                return

            state["chunks"].append(message.get("body", b""))
            total = sum(len(c) for c in state["chunks"])
            if message.get("more_body") and total > self.buffer_limit:
                # Too big to buffer: flush what we have and stream the rest.
                state["buffering"] = False
                await flush_start()
                for chunk in state["chunks"]:
                    await send({
                        "type": "http.response.body", "body": chunk, "more_body": True,
                    })
                state["chunks"] = []
                return
            if message.get("more_body"):
                return
            # Buffered body complete: settle, then send with settlement header.
            settle = await settle_and_record(status)
            extra = (
                {"PAYMENT-RESPONSE": settlement_header(settle)}
                if settle and settle.success else None
            )
            await flush_start(extra)
            await send({
                "type": "http.response.body",
                "body": b"".join(state["chunks"]),
                "more_body": False,
            })

        await self.app(scope, receive, send_wrapper)


def _header_api_key(header_map: dict) -> bool:
    """True when the request carries a valid engine API key (account path)."""
    key = header_map.get("x-api-key")
    if not key:
        auth = header_map.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        return False
    try:
        from backend.core.api_keys import validate_api_key

        return validate_api_key(key) is not None
    except Exception:
        return False
