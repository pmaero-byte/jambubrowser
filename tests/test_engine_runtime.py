"""Unit tests for backend/engine_runtime.py."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock


class TestConnectionManager:
    def test_connect_disconnect(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        asyncio.run(mgr.connect("client1", ws))
        assert "client1" in mgr.active_connections
        ws.accept.assert_awaited_once()
        mgr.disconnect("client1")
        assert "client1" not in mgr.active_connections

    def test_broadcast_to_connected(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        asyncio.run(mgr.connect("c1", ws))
        asyncio.run(mgr.broadcast("c1", "hello"))
        ws.send_text.assert_awaited_once_with("hello")

    def test_broadcast_to_disconnected(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        asyncio.run(mgr.broadcast("nonexistent", "hello"))  # should not raise

    def test_broadcast_all(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        asyncio.run(mgr.connect("c1", ws1))
        asyncio.run(mgr.connect("c2", ws2))
        asyncio.run(mgr.broadcast_all("broadcast"))
        ws1.send_text.assert_awaited_once_with("broadcast")
        ws2.send_text.assert_awaited_once_with("broadcast")


class TestSafeTask:
    def test_safe_task_runs_successfully(self):
        from backend.engine_runtime import safe_task

        async def test():
            async def good():
                return 42
            task = safe_task(good(), "test_good")
            await task
            assert task.done()
            assert not task.cancelled()
        asyncio.run(test())

    def test_safe_task_handles_exception(self):
        from backend.engine_runtime import safe_task

        async def test():
            async def bad():
                raise ValueError("test error")
            task = safe_task(bad(), "test_bad")
            try:
                await task
            except ValueError:
                pass
            assert task.done()
        asyncio.run(test())


class TestResolveLLMConfig:
    def test_default_config_returned(self):
        from backend.engine_runtime import _resolve_llm_config
        result = _resolve_llm_config({})
        assert "provider" in result
        assert "baseUrl" in result

    def test_caller_config_overrides_defaults(self):
        from backend.engine_runtime import _resolve_llm_config
        result = _resolve_llm_config({"provider": "anthropic", "modelId": "claude-3"})
        assert result.get("modelId") == "claude-3"

    def test_cloud_provider_preset_applied(self, monkeypatch):
        # Neutralize env so .env's JAMBU_LLM_PROVIDER=minimax + matching
        # provider-specific model doesn't leak in and make mlx lookups
        # return "MiniMax-M2" instead of "gemma3:12b".
        for key in (
            "JAMBU_LLM_PROVIDER",
            "JAMBU_LLM_MINIMAX_MODEL",
            "JAMBU_LLM_MINIMAX_BASE_URL",
            "JAMBU_LLM_FALLBACK_CHAIN",
        ):
            monkeypatch.delenv(key, raising=False)
        from backend.llm import reload_config
        reload_config()
        from backend.engine_runtime import _resolve_llm_config
        result = _resolve_llm_config({"provider": "mlx"})
        assert result.get("provider") == "mlx"
        assert "gemma" in result.get("modelId", "")

    def test_empty_provider_falls_back(self):
        from backend.engine_runtime import _resolve_llm_config
        result = _resolve_llm_config({})
        assert result.get("provider") is not None

    def test_none_values_not_overwritten(self):
        from backend.engine_runtime import _resolve_llm_config
        result = _resolve_llm_config({"provider": None, "modelId": None})
        assert "modelId" in result


class TestStripThink:
    def test_think_tags_stripped(self):
        from backend.engine_runtime import _strip_think
        result = _strip_think("<think>some reasoning</think>answer")
        assert "think" not in result
        assert "answer" in result

    def test_no_think_tags(self):
        from backend.engine_runtime import _strip_think
        result = _strip_think("just an answer")
        assert result == "just an answer"

    def test_empty_text(self):
        from backend.engine_runtime import _strip_think
        assert _strip_think("") == ""


class TestNewTaskId:
    def test_generates_unique_ids(self):
        from backend.engine_runtime import _new_task_id
        ids = {_new_task_id() for _ in range(100)}
        assert len(ids) == 100

    def test_id_is_hex_string(self):
        from backend.engine_runtime import _new_task_id
        tid = _new_task_id()
        assert len(tid) == 8
        assert all(c in "0123456789abcdef" for c in tid)


class TestIsCancelled:
    def test_no_flag_returns_false(self):
        from backend.engine_runtime import is_cancelled
        assert is_cancelled(None) is False
        assert is_cancelled("nonexistent") is False

    def test_set_flag_returns_true(self):
        from backend.engine_runtime import is_cancelled, cancel_flags

        async def test():
            cancel_flags["task_1"] = asyncio.Event()
            cancel_flags["task_1"].set()
            assert is_cancelled("task_1") is True
        asyncio.run(test())

    def test_unset_flag_returns_false(self):
        from backend.engine_runtime import is_cancelled, cancel_flags

        async def test():
            cancel_flags["task_2"] = asyncio.Event()
            assert is_cancelled("task_2") is False
        asyncio.run(test())


class TestBroadcastFunctions:
    def test_broadcast_agent_state(self):
        from backend.engine_runtime import broadcast_agent_state, manager

        ws = AsyncMock()
        asyncio.run(manager.connect("c1", ws))

        asyncio.run(broadcast_agent_state("c1", "thinking", "research"))

        call_kwargs = ws.send_text.call_args[0][0]
        payload = json.loads(call_kwargs)
        assert payload["type"] == "agent.state"
        assert payload["state"] == "thinking"

    def test_broadcast_agent_telemetry(self):
        from backend.engine_runtime import broadcast_agent_telemetry, manager

        ws = AsyncMock()
        asyncio.run(manager.connect("c1", ws))

        asyncio.run(broadcast_agent_telemetry("c1", "scrape", tokens_generated=100))

        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "agent.telemetry"
        assert payload["action"] == "scrape"

    def test_broadcast_task_start_end(self):
        from backend.engine_runtime import (broadcast_task_start,
                                            broadcast_task_end, manager, active_tasks)

        ws = AsyncMock()
        asyncio.run(manager.connect("c1", ws))

        asyncio.run(broadcast_task_start("c1", "research quantum", "tid_1"))

        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "agent.task_start"
        assert payload["task_id"] == "tid_1"

        ws.send_text.reset_mock()
        asyncio.run(broadcast_task_end("c1", "tid_1", "completed"))

        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "agent.task_end"
        assert payload["status"] == "completed"
        assert "tid_1" not in active_tasks  # cleaned up


class TestClientIdValidation:
    def test_valid_client_id_accepted(self):
        from backend.engine_runtime import _is_valid_client_id
        assert _is_valid_client_id("client-1_abc")
        assert _is_valid_client_id("user:123")
        assert _is_valid_client_id("a" * 64)

    def test_empty_client_id_rejected(self):
        from backend.engine_runtime import _is_valid_client_id
        assert not _is_valid_client_id("")

    def test_too_long_client_id_rejected(self):
        from backend.engine_runtime import _is_valid_client_id
        assert not _is_valid_client_id("a" * 65)

    def test_special_chars_rejected(self):
        from backend.engine_runtime import _is_valid_client_id
        assert not _is_valid_client_id("client with spaces")
        assert not _is_valid_client_id("client/with/slashes")
        assert not _is_valid_client_id("client<script>")
        assert not _is_valid_client_id("client;DROP TABLE")

    def test_path_traversal_rejected(self):
        from backend.engine_runtime import _is_valid_client_id
        assert not _is_valid_client_id("../etc/passwd")
        assert not _is_valid_client_id("..%2F..%2F")


class TestConnectionManagerSecurity:
    def _make_ws(self, ip="127.0.0.1"):
        ws = AsyncMock()
        ws.client = (ip, 50000)
        return ws

    def test_connect_returns_true_for_valid_client_id(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = self._make_ws()
        result = asyncio.run(mgr.connect("valid_client_1", ws))
        assert result is True
        assert "valid_client_1" in mgr.active_connections

    def test_connect_returns_false_for_invalid_client_id(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = self._make_ws()
        result = asyncio.run(mgr.connect("../bad/path", ws))
        assert result is False
        assert "../bad/path" not in mgr.active_connections
        ws.accept.assert_not_awaited()

    def test_reconnect_replaces_old_connection(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        old_ws = self._make_ws()
        new_ws = self._make_ws()

        asyncio.run(mgr.connect("client1", old_ws))
        result = asyncio.run(mgr.connect("client1", new_ws))

        assert result is True
        old_ws.close.assert_awaited_once()
        assert mgr.active_connections["client1"] is new_ws

    def test_disconnect_releases_ip_count(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = self._make_ws(ip="10.0.0.1")

        asyncio.run(mgr.connect("c1", ws))
        mgr.disconnect("c1")

        ws2 = self._make_ws(ip="10.0.0.1")
        result = asyncio.run(mgr.connect("c2", ws2))
        assert result is True

    def test_per_ip_connection_limit_enforced(self):
        from backend.engine_runtime import ConnectionManager, _MAX_CONNECTIONS_PER_IP
        mgr = ConnectionManager()

        for i in range(_MAX_CONNECTIONS_PER_IP):
            ws = self._make_ws(ip="192.168.1.1")
            assert asyncio.run(mgr.connect(f"client_{i}", ws)) is True

        overflow = self._make_ws(ip="192.168.1.1")
        result = asyncio.run(mgr.connect("overflow", overflow))
        assert result is False
        assert "overflow" not in mgr.active_connections

    def test_total_connection_cap_enforced(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        mgr.active_connections = {f"x{i}": MagicMock() for i in range(256)}

        ws = self._make_ws(ip="10.0.0.99")
        result = asyncio.run(mgr.connect("new_client", ws))
        assert result is False
        assert "new_client" not in mgr.active_connections

    def test_get_stats_reports_current_state(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws1 = self._make_ws(ip="10.0.0.1")
        ws2 = self._make_ws(ip="10.0.0.2")

        asyncio.run(mgr.connect("c1", ws1))
        asyncio.run(mgr.connect("c2", ws2))

        stats = mgr.get_stats()
        assert stats["active_connections"] == 2
        assert stats["unique_ips"] == 2
        assert "10.0.0.1" in stats["ip_counts"]
        assert "10.0.0.2" in stats["ip_counts"]

    def test_has_capacity(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        assert mgr.has_capacity() is True

        mgr.active_connections = {f"x{i}": MagicMock() for i in range(256)}
        assert mgr.has_capacity() is False

    def test_unknown_ip_always_allowed(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.client = None

        result = asyncio.run(mgr.connect("c1", ws))
        assert result is True

    def test_broadcast_to_disconnected_handles_cleanup(self):
        from backend.engine_runtime import ConnectionManager
        mgr = ConnectionManager()
        ws = self._make_ws(ip="10.0.0.1")
        ws.send_text.side_effect = Exception("socket closed")

        asyncio.run(mgr.connect("c1", ws))
        asyncio.run(mgr.broadcast("c1", "msg"))

        assert "c1" not in mgr.active_connections
