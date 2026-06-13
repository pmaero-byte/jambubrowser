"""Tests: enhanced /health endpoint with dependency probes."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock


class TestHealthEndpoint:
    def test_health_returns_online_status(self):
        from backend.routes.system import health

        async def run():
            with patch("backend.routes.system.psutil") as mock_psutil:
                mock_mem = MagicMock()
                mock_mem.used = 4 * 1024 ** 3
                mock_mem.total = 16 * 1024 ** 3
                mock_psutil.virtual_memory.return_value = mock_mem
                mock_psutil.cpu_percent.return_value = 25.0

                with patch("backend.core.database.get_db_cursor") as mock_db:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = (1,)
                    mock_db.return_value.__enter__.return_value = mock_cursor

                    with patch("backend.core.audit.get_audit_logger") as mock_audit:
                        mock_audit.return_value.get_statistics.return_value = {
                            "total_entries": 42,
                        }

                        with patch("backend.core.vault.get_vault") as mock_vault:
                            type(mock_vault.return_value).is_locked = PropertyMock(return_value=True)

                            return await health()

        import asyncio
        result = asyncio.run(run())

        assert result["status"] == "online"
        assert result["ram_used_gb"] == 4.0
        assert result["ram_total_gb"] == 16.0
        assert result["cpu_percent"] == 25.0
        assert "checks" in result
        assert result["checks"]["database"] == "ok"
        assert result["checks"]["audit"] == "ok"
        assert result["checks"]["audit_entries"] == 42
        assert result["checks"]["vault"] == "locked"

    def test_health_marks_degraded_on_db_failure(self):
        from backend.routes.system import health

        async def run():
            with patch("backend.routes.system.psutil") as mock_psutil:
                mock_mem = MagicMock()
                mock_mem.used = 1 * 1024 ** 3
                mock_mem.total = 8 * 1024 ** 3
                mock_psutil.virtual_memory.return_value = mock_mem
                mock_psutil.cpu_percent.return_value = 10.0

                with patch("backend.core.database.get_db_cursor") as mock_db:
                    mock_db.return_value.__enter__.side_effect = Exception("connection refused")

                    with patch("backend.core.audit.get_audit_logger") as mock_audit:
                        mock_audit.return_value.get_statistics.return_value = {}

                        with patch("backend.core.vault.get_vault") as mock_vault:
                            type(mock_vault.return_value).is_locked = PropertyMock(return_value=True)

                            return await health()

        import asyncio
        result = asyncio.run(run())

        assert result["status"] == "degraded"
        assert "error:" in result["checks"]["database"]

    def test_health_handles_vault_unlocked(self):
        from backend.routes.system import health

        async def run():
            with patch("backend.routes.system.psutil") as mock_psutil:
                mock_mem = MagicMock()
                mock_mem.used = 2 * 1024 ** 3
                mock_mem.total = 8 * 1024 ** 3
                mock_psutil.virtual_memory.return_value = mock_mem
                mock_psutil.cpu_percent.return_value = 50.0

                with patch("backend.core.database.get_db_cursor") as mock_db:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = (1,)
                    mock_db.return_value.__enter__.return_value = mock_cursor

                    with patch("backend.core.audit.get_audit_logger") as mock_audit:
                        mock_audit.return_value.get_statistics.return_value = {"total_entries": 0}

                        with patch("backend.core.vault.get_vault") as mock_vault:
                            type(mock_vault.return_value).is_locked = PropertyMock(return_value=False)

                            return await health()

        import asyncio
        result = asyncio.run(run())

        assert result["checks"]["vault"] == "unlocked"

    def test_health_includes_all_three_checks(self):
        from backend.routes.system import health

        async def run():
            with patch("backend.routes.system.psutil") as mock_psutil:
                mock_mem = MagicMock()
                mock_mem.used = 1 * 1024 ** 3
                mock_mem.total = 2 * 1024 ** 3
                mock_psutil.virtual_memory.return_value = mock_mem
                mock_psutil.cpu_percent.return_value = 0.0

                with patch("backend.core.database.get_db_cursor") as mock_db:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = (1,)
                    mock_db.return_value.__enter__.return_value = mock_cursor

                    with patch("backend.core.audit.get_audit_logger") as mock_audit:
                        mock_audit.return_value.get_statistics.return_value = {"total_entries": 0}

                        with patch("backend.core.vault.get_vault") as mock_vault:
                            type(mock_vault.return_value).is_locked = PropertyMock(return_value=True)

                            return await health()

        import asyncio
        result = asyncio.run(run())

        assert "database" in result["checks"]
        assert "audit" in result["checks"]
        assert "vault" in result["checks"]
