"""
x402 paywall tests — spec shapes, facilitators, dependency flow, receipts.

The dependency flow is exercised on a dummy paid route (no real audits), and
the wiring into /audit/* and /dcm/infer is asserted through the engine app.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.modules import x402


PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
PAYER = "0x857b06519E91e3A54538791bDbb0E22373e36b66"


@pytest.fixture
def x402_env(monkeypatch):
    monkeypatch.setenv("JAMBU_X402_ENABLED", "1")
    monkeypatch.setenv("JAMBU_X402_PAY_TO", PAY_TO)
    monkeypatch.delenv("JAMBU_X402_FACILITATOR", raising=False)  # default mock
    yield
    for key in ("JAMBU_X402_ENABLED", "JAMBU_X402_PAY_TO", "JAMBU_X402_FACILITATOR"):
        monkeypatch.delenv(key, raising=False)


def payment_header(
    *, nonce: str = "mock-nonce-1", amount: str = "20000",
    to: str = PAY_TO, sender: str = PAYER,
) -> dict:
    payload = {
        "x402Version": 2,
        "accepted": {
            "scheme": "exact", "network": "eip155:84532", "amount": amount,
            "asset": x402.DEFAULT_ASSET, "payTo": to, "maxTimeoutSeconds": 60,
            "extra": {"name": "USDC", "version": "2"},
        },
        "payload": {
            "signature": "0xdeadbeef",
            "authorization": {
                "from": sender, "to": to, "value": amount,
                "validAfter": "0", "validBefore": "9999999999", "nonce": nonce,
            },
        },
    }
    return {"PAYMENT-SIGNATURE": base64.b64encode(json.dumps(payload).encode()).decode()}


def decode_b64(value: str) -> dict:
    return json.loads(base64.b64decode(value))


# ---------------------------------------------------------------------------
# Config + wire shapes
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_are_disabled_and_testnet(self, monkeypatch):
        for key in ("JAMBU_X402_ENABLED", "JAMBU_X402_NETWORK", "JAMBU_X402_PAY_TO"):
            monkeypatch.delenv(key, raising=False)
        cfg = x402.X402Config.from_env()
        assert cfg.enabled is False
        assert cfg.network == "eip155:84532"          # Base Sepolia
        assert cfg.asset == x402.DEFAULT_ASSET
        assert cfg.is_mock is True                    # never a chain by default
        assert cfg.prices["audit_quick"] == "20000"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("JAMBU_X402_NETWORK", "eip155:8453")
        monkeypatch.setenv("JAMBU_X402_PRICE_AUDIT_FULL", "250000")
        monkeypatch.setenv("JAMBU_X402_FACILITATOR", "https://facilitator.example")
        cfg = x402.X402Config.from_env()
        assert cfg.network == "eip155:8453"
        assert cfg.prices["audit_full"] == "250000"
        assert cfg.is_mock is False
        described = cfg.describe()
        assert described["facilitator_mode"] == "http"
        assert "mock" not in described["note"]


class TestWireShapes:
    def test_requirements_shape(self):
        cfg = x402.X402Config(pay_to=PAY_TO)
        req = x402.build_requirements(cfg, "audit_quick", "http://t/audit/quick")
        assert req["scheme"] == "exact"
        assert req["network"] == "eip155:84532"
        assert req["amount"] == "20000"          # atomic units, string
        assert req["payTo"] == PAY_TO
        assert req["asset"] == x402.DEFAULT_ASSET
        assert req["extra"] == {"name": "USDC", "version": "2"}

    def test_payment_required_object(self):
        cfg = x402.X402Config(pay_to=PAY_TO)
        body = x402.build_payment_required(
            cfg, "audit_full", "http://t/audit/run", error="PAYMENT-SIGNATURE header is required",
        )
        assert body["x402Version"] == 2
        assert body["resource"]["url"] == "http://t/audit/run"
        assert body["error"].startswith("PAYMENT-SIGNATURE")
        assert body["accepts"][0]["amount"] == "100000"

    def test_decode_payment_signature_roundtrip_and_rejects_junk(self):
        header = payment_header()["PAYMENT-SIGNATURE"]
        assert x402.decode_payment_signature(header)["x402Version"] == 2
        assert x402.decode_payment_signature("not-base64!!") is None
        assert x402.decode_payment_signature(None) is None
        assert x402.decode_payment_signature(base64.b64encode(b"[1,2]").decode()) is None


# ---------------------------------------------------------------------------
# Facilitators
# ---------------------------------------------------------------------------

class TestMockFacilitator:
    def _verify(self, **kwargs):
        import asyncio
        cfg = x402.X402Config(pay_to=PAY_TO)
        req = x402.build_requirements(cfg, "audit_quick", "http://t")
        return asyncio.run(x402.MockFacilitator().verify(kwargs, req))

    def test_accepts_mock_nonce_with_matching_amount(self):
        payload = json.loads(base64.b64decode(payment_header()["PAYMENT-SIGNATURE"]))
        result = self._verify(**{
            "payload": payload["payload"], "x402Version": 2,
        })
        assert result.is_valid is True
        assert result.payer == PAYER

    def test_rejects_wrong_nonce_and_amount_and_recipient(self):
        payload = json.loads(base64.b64decode(
            payment_header(nonce="real-nonce")["PAYMENT-SIGNATURE"]
        ))
        assert self._verify(payload=payload["payload"]).invalid_reason == "invalid_payload"

        payload = json.loads(base64.b64decode(
            payment_header(amount="1")["PAYMENT-SIGNATURE"]
        ))
        assert "value_mismatch" in self._verify(payload=payload["payload"]).invalid_reason

        payload = json.loads(base64.b64decode(
            payment_header(to="0x" + "1" * 40)["PAYMENT-SIGNATURE"]
        ))
        assert "recipient_mismatch" in self._verify(payload=payload["payload"]).invalid_reason

    def test_settle_is_deterministic_and_labelled_mock(self):
        cfg = x402.X402Config(pay_to=PAY_TO)
        req = x402.build_requirements(cfg, "audit_quick", "http://t")
        payload = json.loads(base64.b64decode(payment_header()["PAYMENT-SIGNATURE"]))
        first = x402.MockFacilitator()
        import asyncio
        a = asyncio.run(first.settle(payload["payload"], req))
        b = asyncio.run(first.settle(payload["payload"], req))
        assert a.success and a.transaction == b.transaction
        assert a.transaction.startswith("mocktx:")
        assert a.network == "eip155:84532"


class TestHttpFacilitator:
    def _facilitator(self, handler):
        return x402.HttpFacilitator(
            "https://fac.example", transport=httpx.MockTransport(handler),
        )

    def test_verify_and_settle_wire_bodies(self):
        seen = {}

        def handler(request):
            body = json.loads(request.content)
            seen[request.url.path] = body
            if request.url.path == "/verify":
                return httpx.Response(200, json={"isValid": True, "payer": PAYER})
            return httpx.Response(200, json={
                "success": True, "transaction": "0xabc", "network": "eip155:8453",
                "payer": PAYER,
            })

        import asyncio
        f = self._facilitator(handler)
        req = x402.build_requirements(x402.X402Config(pay_to=PAY_TO), "audit_quick", "http://t")
        verify = asyncio.run(f.verify({"payload": {}}, req))
        settle = asyncio.run(f.settle({"payload": {}}, req))
        assert verify.is_valid and verify.payer == PAYER
        assert settle.success and settle.transaction == "0xabc"
        assert seen["/verify"]["x402Version"] == 2
        assert seen["/verify"]["paymentRequirements"]["payTo"] == PAY_TO

    def test_invalid_and_errors_map_to_reasons(self):
        def handler(request):
            if request.url.path == "/verify":
                return httpx.Response(200, json={
                    "isValid": False, "invalidReason": "insufficient_funds",
                })
            return httpx.Response(200, json={
                "success": False, "errorReason": "insufficient_funds",
                "transaction": "", "network": "eip155:8453",
            })

        import asyncio
        f = self._facilitator(handler)
        req = x402.build_requirements(x402.X402Config(pay_to=PAY_TO), "audit_quick", "http://t")
        assert asyncio.run(f.verify({"payload": {}}, req)).invalid_reason == "insufficient_funds"
        settle = asyncio.run(f.settle({"payload": {}}, req))
        assert settle.success is False and settle.error_reason == "insufficient_funds"

    def test_facilitator_http_error_is_safe(self):
        def handler(request):
            return httpx.Response(500, text="boom")

        import asyncio
        f = self._facilitator(handler)
        req = x402.build_requirements(x402.X402Config(pay_to=PAY_TO), "audit_quick", "http://t")
        assert asyncio.run(f.verify({"payload": {}}, req)).invalid_reason == "unexpected_verify_error"
        settle = asyncio.run(f.settle({"payload": {}}, req))
        assert settle.error_reason == "unexpected_settle_error"


# ---------------------------------------------------------------------------
# Dependency flow (dummy paid route — no real audits)
# ---------------------------------------------------------------------------

def dummy_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        x402.X402Middleware, routes={("POST", "/paid"): "audit_quick"},
    )

    @app.post("/paid")
    async def paid():
        return {"ok": True}

    @app.get("/free")
    async def free():
        return {"free": True}

    @app.post("/stream")
    async def stream():
        from fastapi.responses import StreamingResponse

        async def gen():
            yield b"data: one\n\n"
            yield b"data: two\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


class TestPaywallFlow:
    def test_disabled_passes_through(self, monkeypatch):
        monkeypatch.delenv("JAMBU_X402_ENABLED", raising=False)
        with TestClient(dummy_app()) as client:
            assert client.post("/paid").status_code == 200

    def test_concurrent_requests_cannot_double_spend_one_authorization(
        self, x402_env, monkeypatch,
    ):
        """Two simultaneous requests with the same PAYMENT-SIGNATURE: exactly
        one executes. Without the nonce claim both verify (the receipt guard
        only sees settled payments) and the work runs twice."""
        import threading
        import time as _time

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        calls: list[float] = []

        app = FastAPI()
        app.add_middleware(
            x402.X402Middleware, routes={("POST", "/slow"): "audit_quick"},
        )

        @app.post("/slow")
        async def slow():
            calls.append(_time.time())
            _time.sleep(0.4)  # hold the request open so the race is real
            return {"ok": True}

        headers = payment_header(nonce="mock-race-1")
        results: list[int] = []
        lock = threading.Lock()

        def hit():
            with TestClient(app) as c:
                status = c.post("/slow", headers=headers).status_code
            with lock:
                results.append(status)

        threads = [threading.Thread(target=hit) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results) == [200, 402], results
        assert len(calls) == 1                       # executed exactly once

    def test_claim_is_exclusive_and_reclaims_stale(self, x402_env):
        import time as _time

        from backend.core.database import get_db
        from backend.modules.x402 import _claim_nonce, _release_nonce

        assert _claim_nonce("mock-claim-1", "http://t/paid") is True
        assert _claim_nonce("mock-claim-1", "http://t/paid") is False

        # Released (abandoned) claims are reusable...
        _release_nonce("mock-claim-1", "abandoned")
        assert _claim_nonce("mock-claim-1", "http://t/paid") is True

        # ...and a crashed worker's stale pending claim expires.
        with get_db() as conn:
            conn.execute(
                "UPDATE x402_nonce_claims SET created_at = ? WHERE nonce = ?",
                (_time.time() - 3600, "mock-claim-1"),
            )
            conn.commit()
        assert _claim_nonce("mock-claim-1", "http://t/paid") is True

    def test_failed_work_does_not_burn_the_authorization(self, x402_env):
        """A 5xx records error_skipped and clears the claim, so a legitimate
        retry with the same authorization is possible."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(
            x402.X402Middleware, routes={("POST", "/boom"): "audit_quick"},
        )

        @app.post("/boom")
        async def boom():
            raise RuntimeError("upstream exploded")

        headers = payment_header(nonce="mock-retry-1")
        with TestClient(app, raise_server_exceptions=False) as c:
            first = c.post("/boom", headers=headers)
            second = c.post("/boom", headers=headers)
        assert first.status_code == 500
        assert second.status_code == 500      # retried, not blocked with 402
        receipts = x402.list_receipts(5)
        assert receipts[0]["status"] == "error_skipped"

    def test_settled_nonce_blocks_later_reuse(self, x402_env):
        with TestClient(dummy_app()) as client:
            assert client.post("/paid", headers=payment_header(nonce="mock-once")).status_code == 200
            again = client.post("/paid", headers=payment_header(nonce="mock-once"))
        assert again.status_code == 402
        assert again.json()["error"] == "nonce_already_used"

    def test_route_variants_do_not_bypass(self, x402_env):
        """Trailing slashes, case changes and methods must not reach the
        resource without payment."""
        executed: list[str] = []

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(
            x402.X402Middleware, routes={("POST", "/paid"): "audit_quick"},
        )

        @app.post("/paid")
        async def paid():
            executed.append("paid")
            return {"ok": True}

        with TestClient(app, follow_redirects=False) as c:
            assert c.post("/paid?utm=x").status_code == 402      # query still gated
            assert c.post("/paid/").status_code in (307, 308, 404)
            assert c.get("/paid").status_code == 405             # wrong method
            assert c.post("/Paid").status_code == 404            # case-sensitive
        assert executed == []                                    # nothing ran

    def test_unpriced_routes_are_never_charged(self, x402_env):
        with TestClient(dummy_app()) as client:
            assert client.get("/free").status_code == 200

    def test_missing_payment_is_spec_shaped_402(self, x402_env):
        with TestClient(dummy_app()) as client:
            resp = client.post("/paid")
        assert resp.status_code == 402
        body = resp.json()
        assert body["x402Version"] == 2
        assert body["error"] == "PAYMENT-SIGNATURE header is required"
        assert body["accepts"][0]["amount"] == "20000"
        assert body["accepts"][0]["payTo"] == PAY_TO
        header = decode_b64(resp.headers["PAYMENT-REQUIRED"])
        assert header["accepts"][0]["scheme"] == "exact"

    def test_invalid_payment_reports_reason(self, x402_env):
        with TestClient(dummy_app()) as client:
            resp = client.post("/paid", headers=payment_header(nonce="nope"))
        assert resp.status_code == 402
        assert resp.json()["error"] == "invalid_payload"

    def test_valid_payment_runs_and_settles_with_header(self, x402_env):
        with TestClient(dummy_app()) as client:
            resp = client.post("/paid", headers=payment_header())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert "PAYMENT-RESPONSE" in resp.headers, resp.headers
        settle = decode_b64(resp.headers["PAYMENT-RESPONSE"])
        assert settle["success"] is True
        assert settle["transaction"].startswith("mocktx:")
        assert settle["network"] == "eip155:84532"
        assert settle["payer"] == PAYER

    def test_streamed_response_settles_and_records_after_body(self, x402_env):
        app = dummy_app()
        app.middleware_stack = None  # rebuilt with the route map below
        app.add_middleware(
            x402.X402Middleware, routes={("POST", "/stream"): "audit_quick"},
        )
        with TestClient(app) as client:
            resp = client.post(
                "/stream", headers={**payment_header(nonce="mock-stream")},
            )
        assert resp.status_code == 200
        assert resp.text.count("data:") == 2
        receipts = x402.list_receipts(5)
        assert any(
            r["nonce"] == "mock-stream" and r["status"] == "settled"
            for r in receipts
        )

    def test_replayed_nonce_is_rejected(self, x402_env):
        with TestClient(dummy_app()) as client:
            assert client.post("/paid", headers=payment_header(nonce="mock-replay")).status_code == 200
            resp = client.post("/paid", headers=payment_header(nonce="mock-replay"))
        assert resp.status_code == 402
        assert resp.json()["error"] == "nonce_already_used"

    def test_receipt_is_recorded_and_root_matches_merkle(self, x402_env):
        with TestClient(dummy_app()) as client:
            client.post("/paid", headers=payment_header(nonce="mock-1"))
            client.post("/paid", headers=payment_header(nonce="mock-2"))
        receipts = x402.list_receipts(10)
        assert len(receipts) >= 2
        assert all(r["status"] == "settled" for r in receipts[:2])
        assert all(r["facilitator"] == "mock" for r in receipts[:2])

        root = x402.receipts_root()
        from backend.modules.meshpay import merkle_root
        hashes = [r["receipt_hash"] for r in reversed(receipts)]  # oldest first
        assert root["root"] == merkle_root(hashes)

    def test_api_key_bypasses_the_paywall(self, x402_env):
        from backend.core.api_keys import create_api_key

        raw_key, _ = create_api_key(name="x402-bypass")
        with TestClient(dummy_app()) as client:
            resp = client.post("/paid", headers={"X-API-Key": raw_key})
        assert resp.status_code == 200

    def test_bearer_api_key_also_bypasses(self, x402_env):
        from backend.core.api_keys import create_api_key

        raw_key, _ = create_api_key(name="x402-bypass-bearer")
        with TestClient(dummy_app()) as client:
            resp = client.post(
                "/paid", headers={"Authorization": f"Bearer {raw_key}"},
            )
        assert resp.status_code == 200

    def test_enabled_without_pay_to_fails_closed(self, monkeypatch):
        monkeypatch.setenv("JAMBU_X402_ENABLED", "1")
        monkeypatch.delenv("JAMBU_X402_PAY_TO", raising=False)
        with TestClient(dummy_app()) as client:
            resp = client.post("/paid", headers=payment_header())
        assert resp.status_code == 503
        assert "JAMBU_X402_PAY_TO" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Engine wiring + routes
# ---------------------------------------------------------------------------

class TestEngineWiring:
    @pytest.fixture
    def client(self):
        from backend.engine import app
        with TestClient(app) as c:
            yield c

    def test_paid_routes_require_payment_when_enabled(self, x402_env, client):
        # Payment gate runs before the audit pipeline / DCM call.
        assert client.post("/audit/quick", json={"url": "https://example.com"}).status_code == 402
        assert client.post("/dcm/infer", json={"prompt": "hi"}).status_code == 402

    def test_config_route_reports_env(self, x402_env, client):
        body = client.get("/x402/config").json()
        assert body["enabled"] is True
        assert body["network"] == "eip155:84532"
        assert body["pay_to"] == PAY_TO
        assert body["prices"]["dcm_infer"]["usdc"] == pytest.approx(0.001)

    def test_receipts_route(self, x402_env, client):
        body = client.get("/x402/receipts?limit=5").json()
        assert "receipts" in body and "settled" in body
        root = client.get("/x402/receipts/root").json()
        assert "root" in root and "receipts" in root
