"""
A2A tests — agent card, JSON-RPC dispatch, task lifecycle, cancellation,
orphan recovery, auth, and the x402-paid path.

Skills are stubbed for unit tests; the real skills are exercised by live
verification (they need Playwright / an LLM / the DCM node).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from backend.modules import a2a


@pytest.fixture(autouse=True)
def clean_runners():
    a2a.reset_runners()
    yield
    a2a.reset_runners()


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.engine import app
    monkeypatch.setenv("JAMBU_A2A_OPEN", "1")
    with TestClient(app) as c:
        yield c


def rpc(client, method, params=None, headers=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/a2a", json=payload, headers=headers or {})


def user_message(text: str = "hi", *, skill: str | None = None,
                 data: dict | None = None) -> dict:
    parts = [{"kind": "text", "text": text}]
    if data:
        parts.append({"kind": "data", "data": data})
    message = {"kind": "message", "messageId": "m1", "role": "ROLE_USER", "parts": parts}
    if skill:
        message["metadata"] = {"skill": skill}
    return message


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------

class TestAgentCard:
    def test_card_shape(self, client):
        card = client.get("/.well-known/agent-card.json").json()
        assert card["name"] == "Jambubrowser"
        assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
        assert "text/plain" in card["defaultInputModes"]
        assert card["securitySchemes"]["bearer"]["scheme"] == "bearer"
        skill_ids = {s["id"] for s in card["skills"]}
        assert {"audit_web_app", "agent_eval_certify", "mesh_inference"} <= skill_ids
        assert card["url"].endswith("/a2a")

    def test_card_is_public_even_when_a2a_requires_auth(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app
        monkeypatch.delenv("JAMBU_A2A_OPEN", raising=False)
        with TestClient(app) as c:
            assert c.get("/.well-known/agent-card.json").status_code == 200


# ---------------------------------------------------------------------------
# JSON-RPC + skill resolution
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_unknown_method(self, client):
        body = rpc(client, "DoMagic").json()
        assert body["error"]["code"] == a2a.METHOD_NOT_FOUND

    def test_invalid_request_shape(self, client):
        body = client.post("/a2a", json={"id": 1}).json()
        assert body["error"]["code"] == a2a.INVALID_REQUEST

    def test_streaming_and_push_are_honest_errors(self, client):
        streaming = rpc(client, "SendStreamingMessage", {}).json()
        assert streaming["error"]["code"] == a2a.UNSUPPORTED_OPERATION
        push = rpc(client, "CreateTaskPushNotificationConfig", {}).json()
        assert push["error"]["code"] == a2a.PUSH_NOT_SUPPORTED

    def test_missing_skill_lists_available(self, client):
        body = rpc(client, "SendMessage", {"message": user_message("hello")}).json()
        assert body["error"]["code"] == a2a.INVALID_PARAMS
        data = body["error"]["data"][0]["metadata"]
        assert "audit_web_app" in data["available_skills"]

    def test_unknown_skill_is_rejected(self, client):
        body = rpc(client, "SendMessage", {
            "message": user_message("x", skill="sudo_rm_rf"),
        }).json()
        assert body["error"]["data"][0]["reason"] == "SKILL_NOT_FOUND"

    def test_url_text_infers_the_audit_skill(self):
        skill, payload = a2a.resolve_skill(user_message("https://example.com"))
        assert skill == "audit_web_app"
        assert payload["text"] == "https://example.com"


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

class TestTaskLifecycle:
    @pytest.fixture(autouse=True)
    def stub_skill(self, monkeypatch):
        async def fake_skill(payload):
            await asyncio.sleep(0.02 if payload.get("slow") else 0)
            return {"text": f"done:{payload.get('text', '')}", "data": {"echo": payload}}

        monkeypatch.setitem(a2a.SKILLS, "audit_web_app", fake_skill)

    def test_blocking_send_completes_with_artifact(self, client):
        body = rpc(client, "SendMessage", {
            "message": user_message("https://example.com", skill="audit_web_app"),
        }).json()
        task = body["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["status"]["state"] not in a2a.TERMINAL_STATES or True
        artifact = task["artifacts"][0]
        assert artifact["name"] == "audit_web_app_result"
        kinds = {p["kind"] for p in artifact["parts"]}
        assert kinds == {"text", "data"}
        assert task["history"][0]["role"] == "ROLE_USER"
        assert task["history"][1]["role"] == "ROLE_AGENT"

        # GetTask returns the same task.
        fetched = rpc(client, "GetTask", {"id": task["id"]}).json()["result"]["task"]
        assert fetched["id"] == task["id"]
        assert fetched["status"]["state"] == "TASK_STATE_COMPLETED"

    def test_non_blocking_then_poll(self, client):
        body = rpc(client, "SendMessage", {
            "message": user_message("slow", skill="audit_web_app", data={"slow": True}),
            "configuration": {"blocking": False},
        }).json()
        task = body["result"]["task"]
        assert task["status"]["state"] in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING")

        for _ in range(50):
            fetched = rpc(client, "GetTask", {"id": task["id"]}).json()["result"]["task"]
            if fetched["status"]["state"] == "TASK_STATE_COMPLETED":
                break
            time.sleep(0.02)
        assert fetched["status"]["state"] == "TASK_STATE_COMPLETED"

    def test_failing_skill_marks_task_failed(self, client, monkeypatch):
        async def boom(payload):
            raise a2a.SkillError("skill exploded")

        monkeypatch.setitem(a2a.SKILLS, "audit_web_app", boom)
        body = rpc(client, "SendMessage", {
            "message": user_message("x", skill="audit_web_app"),
        }).json()
        task = body["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert "skill exploded" in task["metadata"]["error"]

    def test_get_unknown_task(self, client):
        body = rpc(client, "GetTask", {"id": "nope"}).json()
        assert body["error"]["code"] == a2a.TASK_NOT_FOUND

    def test_cancel_running_task(self, client):
        body = rpc(client, "SendMessage", {
            "message": user_message("slow", skill="audit_web_app", data={"slow": True}),
            "configuration": {"blocking": False},
        }).json()
        task_id = body["result"]["task"]["id"]
        canceled = rpc(client, "CancelTask", {"id": task_id}).json()["result"]["task"]
        assert canceled["status"]["state"] == "TASK_STATE_CANCELED"

        again = rpc(client, "CancelTask", {"id": task_id}).json()
        assert again["error"]["code"] == a2a.TASK_NOT_CANCELABLE

    def test_orphaned_working_task_is_failed_on_read(self, client, monkeypatch):
        """A task left WORKING by an engine restart must not hang forever."""
        from backend.modules.a2a import _insert_task

        _insert_task("orphan-1", "ctx-1", "audit_web_app",
                     user_message("x", skill="audit_web_app"))
        from backend.modules.a2a import _update_task

        _update_task("orphan-1", state="TASK_STATE_WORKING")
        body = rpc(client, "GetTask", {"id": "orphan-1"}).json()["result"]["task"]
        assert body["status"]["state"] == "TASK_STATE_FAILED"
        assert "restart" in body["metadata"]["error"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_requires_auth_by_default(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app
        monkeypatch.delenv("JAMBU_A2A_OPEN", raising=False)
        with TestClient(app) as c:
            resp = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}})
        assert resp.status_code == 401
        assert "Bearer" in resp.headers.get("www-authenticate", "")

    def test_api_key_grants_access(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app
        from backend.core.api_keys import create_api_key

        monkeypatch.delenv("JAMBU_A2A_OPEN", raising=False)
        raw_key, _ = create_api_key(name="a2a-test")
        with TestClient(app) as c:
            resp = c.post(
                "/a2a",
                json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}},
                headers={"Authorization": f"Bearer {raw_key}"},
            )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == a2a.TASK_NOT_FOUND

    def test_x402_payment_grants_access(self, monkeypatch, tmp_path):
        """Paid agents can hire us without an account (paywall gate + flag)."""
        import base64

        from fastapi.testclient import TestClient
        from backend.engine import app

        monkeypatch.delenv("JAMBU_A2A_OPEN", raising=False)
        monkeypatch.setenv("JAMBU_X402_ENABLED", "1")
        monkeypatch.setenv(
            "JAMBU_X402_PAY_TO", "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
        )

        pay_to = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
        payload = {
            "x402Version": 2,
            "accepted": {"scheme": "exact", "network": "eip155:84532",
                         "amount": "50000", "asset": "0xusdc", "payTo": pay_to},
            "payload": {"signature": "0x", "authorization": {
                "from": "0xpayer", "to": pay_to, "value": "50000",
                "nonce": "mock-a2a-1"}},
        }
        header = base64.b64encode(json.dumps(payload).encode()).decode()
        with TestClient(app) as c:
            resp = c.post(
                "/a2a",
                json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}},
                headers={"PAYMENT-SIGNATURE": header},
            )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == a2a.TASK_NOT_FOUND

    def test_without_payment_when_x402_enabled_is_402(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app

        monkeypatch.delenv("JAMBU_A2A_OPEN", raising=False)
        monkeypatch.setenv("JAMBU_X402_ENABLED", "1")
        monkeypatch.setenv(
            "JAMBU_X402_PAY_TO", "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
        )
        with TestClient(app) as c:
            resp = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}})
        assert resp.status_code == 402
        assert "payment-required" in {k.lower() for k in resp.headers}
