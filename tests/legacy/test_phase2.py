"""
Tests: Phase 2 Modules - Mission Scheduler & Risk Shield
========================================================
Tests for the autonomous mission scheduler, risk shield,
shadow browser, and notification system.
"""

import pytest
import time
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestMissionScheduler:
    """Tests for mission scheduler with cron support."""

    def test_schedule_mission_basic(self, client):
        response = client.post("/mission/schedule", json={
            "query": "monitor AI breakthroughs",
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert "mission_id" in data
        assert data["status"] == "active"

    def test_schedule_mission_with_cron(self, client):
        response = client.post("/mission/schedule", json={
            "query": "hourly security check",
            "schedule": "0 * * * *",
            "priority": 3,
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["schedule"] == "0 * * * *"
        assert data["priority"] == 3

    def test_list_missions(self, client):
        client.post("/mission/schedule", json={
            "query": "test mission for listing",
            "client_id": "test",
        })
        response = client.get("/mission/list")
        assert response.status_code == 200
        assert "missions" in response.json()

    def test_mission_scheduler_start_stop(self, client):
        start = client.post("/mission/start-scheduler")
        assert start.status_code == 200
        assert start.json()["status"] == "started"

        stop = client.post("/mission/stop-scheduler")
        assert stop.status_code == 200
        assert stop.json()["status"] == "stopped"

    def test_parse_cron_module(self):
        from backend.modules.missions import parse_cron, get_next_run

        parsed = parse_cron("0 */6 * * *")
        assert parsed is not None
        assert 0 in parsed["minute"]
        assert 0 in parsed["hour"]

        parsed = parse_cron("none")
        assert parsed is None

        parsed = parse_cron(None)
        assert parsed is None

    def test_next_run_calculation(self):
        from backend.modules.missions import get_next_run

        next_run = get_next_run("0 */6 * * *")
        assert next_run is not None
        assert next_run > time.time()


class TestRiskShield:
    """Tests for the multi-source risk assessment system."""

    def test_shield_check_safe_url(self, client):
        response = client.post("/shield/check", json={
            "url": "https://en.wikipedia.org/wiki/Security",
            "real_time": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert "risk_level" in data
        assert "consensus_score" in data
        assert "checks" in data
        assert "blocked" in data

    def test_shield_heuristic_detection(self, client):
        response = client.post("/shield/check", json={
            "url": "https://paypal.com.secure-login.tk/verify",
            "real_time": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["risk_level"] in ("high", "medium")

    def test_shield_blocked_url(self, client):
        response = client.post("/shield/check", json={
            "url": "data:text/html,<script>alert(1)</script>",
            "real_time": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["risk_level"] in ("high", "critical")

    def test_shield_batch(self, client):
        response = client.post("/shield/batch", json={
            "urls": [
                "https://example.com",
                "https://google.com",
                "https://login-secure.tk/verify",
            ],
            "real_time": False,
        })
        assert response.status_code == 200
        assert "results" in response.json()

    def test_shield_stats(self, client):
        client.post("/shield/check", json={
            "url": "https://test-cache.example.com",
            "real_time": False,
        })
        response = client.get("/shield/stats")
        assert response.status_code == 200
        assert "ttl" in response.json()

    def test_quick_check_module(self):
        from backend.modules.risk_shield import quick_url_check
        import asyncio

        result = asyncio.run(quick_url_check("https://example.com"))
        assert "risk_level" in result
        assert "blocked" in result
        assert "score" in result


class TestShadowBrowser:
    """Tests for the autonomous shadow browser."""

    def test_shadow_interests_list(self, client):
        response = client.get("/shadow/interests")
        assert response.status_code == 200
        assert "interests" in response.json()

    def test_add_interest(self, client):
        response = client.post("/shadow/interests", json={
            "name": "Blockchain",
            "keywords": ["ethereum", "solidity", "defi"],
            "seed_urls": ["https://ethereum.org"],
            "priority": 3,
        })
        assert response.status_code == 200
        assert response.json()["status"] == "added"

    def test_remove_interest(self, client):
        client.post("/shadow/interests", json={
            "name": "TemporaryTopic",
            "keywords": ["temp"],
        })
        response = client.delete("/shadow/interests/TemporaryTopic")
        assert response.status_code == 200
        assert response.json()["name"] == "TemporaryTopic"

    def test_shadow_stats(self, client):
        response = client.get("/shadow/stats")
        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "frontier_size" in data
        assert "pages_crawled" in data

    def test_interest_topic_creation(self):
        from backend.modules.shadow_browser import InterestTopic
        topic = InterestTopic(name="Test", keywords=["ai", "ml"],
                              seed_urls=["https://example.com"], priority=3)
        assert topic.name == "Test"
        assert topic.max_depth == 3
        assert len(topic.keywords) == 2


class TestNotifications:
    """Tests for the desktop notification system."""

    def test_send_notification(self, client):
        response = client.post("/notifications/send", params={
            "title": "Test Alert",
            "message": "This is a test notification",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "sent"

    def test_notification_with_urgency(self, client):
        response = client.post("/notifications/send", params={
            "title": "Critical Alert",
            "message": "Security breach detected",
            "urgency": "critical",
            "category": "security",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "sent"
        assert "id" in data

    def test_notification_history(self, client):
        client.post("/notifications/send", params={
            "title": "History Test",
            "message": "Testing history",
        })

        response = client.get("/notifications/history")
        assert response.status_code == 200
        assert "notifications" in response.json()

    def test_notification_history_filtered(self, client):
        client.post("/notifications/send", params={
            "title": "Security Test",
            "message": "Security category",
            "category": "security",
        })

        response = client.get("/notifications/history", params={"category": "security"})
        assert response.status_code == 200
