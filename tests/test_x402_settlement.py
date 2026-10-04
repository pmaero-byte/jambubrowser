"""The x402 settlement bridge: when money moves and when a nonce is reusable.

`X402Middleware.__call__` was 180 lines containing the paywall decision, the
settlement logic and an ASGI send-wrapper state machine. Settlement and the
wrapper now live in `_SettlementBridge`, which is where the money decisions are
made — so they can be tested without a route, an HTTP client or a facilitator
that talks to a network.

Three rules are pinned here, and they are the ones that decide whether a payer
can be double-charged or wrongly blocked from retrying:

* **one authorization, one execution** — a settled authorization keeps its nonce
  claim blocking, *including* when settlement failed;
* **an unsettled one is reusable** — a 4xx/5xx or a crashed resource releases
  the claim, so a legitimate retry is not punished;
* **settlement happens before the first byte** for a buffered response, so a
  client never sees success for work that was never paid for. Streaming
  responses cannot wait, so they settle on completion instead.
"""
from __future__ import annotations

import asyncio
import pytest

from backend.decentralized import x402
from backend.decentralized.x402 import (
    SettleResult,
    VerifyResult,
    _SettlementBridge,
)


class FakeFacilitator:
    mode = "mock"

    def __init__(self, *, settle_success: bool = True):
        self.settle_success = settle_success
        self.settled: list = []

    async def verify(self, payload, requirements):
        return VerifyResult(is_valid=True, payer="0xpayer")

    async def settle(self, payload, requirements):
        self.settled.append((payload, requirements))
        return SettleResult(
            success=self.settle_success, transaction="0xsettled",
            payer="0xpayer", error_reason=None if self.settle_success else "boom",
        )


class RecordingApp:
    """An ASGI app that emits whatever messages it is given."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        for message in self.messages:
            await send(message)


def make_bridge(app, facilitator=None, *, buffer_limit=1024, nonce="n-1"):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    bridge = _SettlementBridge(
        app, {"type": "http", "state": {}}, receive, send,
        facilitator=facilitator or FakeFacilitator(),
        payload={"sig": "x"}, requirements={"maxAmount": "1"},
        verify=VerifyResult(is_valid=True, payer="0xpayer"),
        nonce=nonce, resource_url="https://host/paid", buffer_limit=buffer_limit,
    )
    return bridge, sent


def start(status=200, content_type="application/json", extra=()):
    headers = [(b"content-type", content_type.encode()), *extra]
    return {"type": "http.response.start", "status": status, "headers": headers}


def body(chunk=b"hi", more=False):
    return {"type": "http.response.body", "body": chunk, "more_body": more}


@pytest.fixture(autouse=True)
def stub_nonce_store(monkeypatch):
    """Nonce bookkeeping is module state; record the calls instead."""
    calls: list[tuple] = []
    monkeypatch.setattr(x402, "_release_nonce",
                        lambda nonce, status: calls.append((nonce, status)))
    monkeypatch.setattr(x402, "record_receipt",
                        lambda **kw: calls.append(("receipt", kw["status"])))
    return calls


# ---------------------------------------------------------------------------
# Buffered responses: settle before the first byte
# ---------------------------------------------------------------------------

class TestBufferedSettlement:
    def test_settlement_precedes_the_response(self):
        """A client must never see success for work that was not paid for."""
        app = RecordingApp([start(), body()])
        bridge, sent = make_bridge(app)
        asyncio.run(bridge.run())

        assert bridge.facilitator.settled, "nothing was settled"
        assert sent[0]["type"] == "http.response.start"
        # ASGI header keys arrive lower-cased.
        assert "payment-response" in {k.decode().lower()
                                      for k, _v in sent[0]["headers"]}

    def test_the_body_is_held_until_settlement(self):
        app = RecordingApp([start(), body(b"payload", more=True), body(b"!")])
        bridge, sent = make_bridge(app)
        asyncio.run(bridge.run())

        bodies = [m for m in sent if m["type"] == "http.response.body"]
        assert len(bodies) == 1
        assert bodies[0]["body"] == b"payload!"

    def test_settlement_runs_once_across_many_chunks(self):
        app = RecordingApp([
            start(),
            body(b"a", more=True), body(b"b", more=True), body(b"c"),
        ])
        bridge, sent = make_bridge(app)
        asyncio.run(bridge.run())
        assert len(bridge.facilitator.settled) == 1

    def test_a_failed_settlement_still_keeps_the_nonce_blocked(self, stub_nonce_store):
        """A nonce replayable after a failed settle is a double-charge."""
        app = RecordingApp([start(), body()])
        bridge, sent = make_bridge(
            app, FakeFacilitator(settle_success=False), nonce="n-2")
        asyncio.run(bridge.run())

        assert ("n-2", "settle_failed") in stub_nonce_store
        statuses = [s for kind, s in stub_nonce_store if kind == "receipt"]
        assert "settle_failed" in statuses

    def test_a_failed_settlement_sends_no_payment_response_header(self):
        app = RecordingApp([start(), body()])
        bridge, sent = make_bridge(app, FakeFacilitator(settle_success=False))
        asyncio.run(bridge.run())

        header_names = {k.decode().lower() for k, _v in sent[0]["headers"]}
        assert "payment-response" not in header_names


# ---------------------------------------------------------------------------
# Error responses: release the claim
# ---------------------------------------------------------------------------

class TestErrorResponses:
    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_a_failed_resource_does_not_charge_and_frees_the_nonce(
            self, status, stub_nonce_store):
        app = RecordingApp([start(status=status), body()])
        bridge, sent = make_bridge(app, nonce=f"n-{status}")
        asyncio.run(bridge.run())

        assert not bridge.facilitator.settled, "a 4xx/5xx must not charge"
        assert (f"n-{status}", "abandoned") in stub_nonce_store

    def test_the_receipt_is_still_written_for_an_error(self, stub_nonce_store):
        app = RecordingApp([start(status=500), body()])
        bridge, sent = make_bridge(app)
        asyncio.run(bridge.run())

        statuses = [s for kind, s in stub_nonce_store if kind == "receipt"]
        assert "error_skipped" in statuses


# ---------------------------------------------------------------------------
# A crashed resource: release the claim in `finally`
# ---------------------------------------------------------------------------

class TestCrashedResource:
    def test_a_crash_releases_the_nonce_and_records_it(self, stub_nonce_store):
        class CrashingApp:
            async def __call__(self, scope, receive, send):
                await send(start())
                raise RuntimeError("resource exploded")

        bridge, sent = make_bridge(CrashingApp(), nonce="n-crash")
        with pytest.raises(RuntimeError):
            asyncio.run(bridge.run())

        assert ("n-crash", "abandoned") in stub_nonce_store
        statuses = [s for kind, s in stub_nonce_store if kind == "receipt"]
        assert "error_skipped" in statuses

    def test_a_crash_after_settlement_does_not_release_the_nonce(
            self, stub_nonce_store):
        """Settlement already happened; releasing would allow a replay."""
        class CrashingAfterBody:
            async def __call__(self, scope, receive, send):
                await send(start())
                await send(body())
                raise RuntimeError("exploded after the body")

        bridge, sent = make_bridge(CrashingAfterBody(), nonce="n-done")
        with pytest.raises(RuntimeError):
            asyncio.run(bridge.run())

        releases = [c for c in stub_nonce_store
                    if isinstance(c, tuple) and c and c[0] == "n-done"]
        assert ("n-done", "settled") in releases
        assert ("n-done", "abandoned") not in releases


# ---------------------------------------------------------------------------
# Streaming: cannot wait, so it settles on completion
# ---------------------------------------------------------------------------

class TestStreaming:
    def test_sse_forwards_immediately_and_settles_at_the_end(self):
        app = RecordingApp([
            start(content_type="text/event-stream"),
            body(b"data: 1\n\n", more=True),
            body(b"data: 2\n\n"),
        ])
        bridge, sent = make_bridge(app)
        asyncio.run(bridge.run())

        # The first message went out before settlement.
        assert sent[0]["type"] == "http.response.start"
        assert bridge.facilitator.settled, "a stream must still settle"

    def test_a_stream_settles_only_once_the_body_ends(self, stub_nonce_store):
        app = RecordingApp([
            start(content_type="text/event-stream"),
            body(b"chunk", more=True),
        ])
        bridge, sent = make_bridge(app, nonce="n-stream")
        asyncio.run(bridge.run())

        # more_body=True and no terminal message: nothing settled, nothing freed.
        assert not bridge.facilitator.settled
        assert ("n-stream", "settled") not in stub_nonce_store


# ---------------------------------------------------------------------------
# Buffer overflow: degrade to streaming mid-flight
# ---------------------------------------------------------------------------

class TestBufferOverflow:
    def test_an_oversized_body_is_flushed_and_streamed(self):
        app = RecordingApp([
            start(),
            body(b"x" * 10, more=True),
            body(b"y" * 10, more=True),
        ])
        bridge, sent = make_bridge(app, buffer_limit=4)   # overflow on the first chunk
        asyncio.run(bridge.run())

        # The response start was flushed, then both chunks, unmerged.
        assert sent[0]["type"] == "http.response.start"
        bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
        assert bodies == [b"x" * 10, b"y" * 10]

    def test_overflow_still_settles_at_the_end(self):
        app = RecordingApp([
            start(),
            body(b"x" * 10, more=True),
            body(b"y"),
        ])
        bridge, sent = make_bridge(app, buffer_limit=4)
        asyncio.run(bridge.run())
        assert len(bridge.facilitator.settled) == 1

    def test_a_body_within_the_limit_is_not_flushed_early(self):
        app = RecordingApp([start(), body(b"small", more=True), body(b"ok")])
        bridge, sent = make_bridge(app, buffer_limit=1024)
        asyncio.run(bridge.run())

        bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
        assert bodies == [b"smallok"]


class TestPassThroughMessages:
    def test_non_response_messages_are_forwarded_untouched(self):
        app = RecordingApp([start(), body()])
        bridge, sent = make_bridge(app)

        async def run():
            await bridge.run()

        asyncio.run(run())
        assert bridge.app.calls == 1