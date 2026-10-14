"""
Tests for Anti-Bot Shield Hardening, Residential Proxy Rotation,
Autonomous Multi-Tab Swarms, and SSM Memory Checkpointing.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from backend.modules.antibot_detector import (
    AntiBotDetector,
    STEALTH_JS,
    DETECT_CHALLENGE_JS,
)
from backend.modules.proxy_rotator import (
    ProxyEntry,
    ProxyRotator,
    get_proxy_rotator,
    redact_proxy,
)
from backend.modules.swarm_orchestrator import (
    SwarmOrchestrator,
    SwarmJob,
    SwarmWorkerResult,
    SIDDHA_KEYWORDS,
    get_swarm_orchestrator,
)
from backend.modules.memory_checkpoint_scheduler import (
    MemoryCheckpointScheduler,
    get_checkpoint_scheduler,
)
from backend.engine import app


# ---------------------------------------------------------------------------
# 1. Anti-Bot Shield & Stealth JS Tests
# ---------------------------------------------------------------------------

def test_stealth_js_contents():
    assert "navigator" in STEALTH_JS
    assert "webdriver" in STEALTH_JS
    assert "chrome" in STEALTH_JS
    assert "runtime" in STEALTH_JS
    assert "Permissions" in STEALTH_JS


@pytest.mark.asyncio
async def test_detect_challenge_cloudflare():
    mock_page = AsyncMock()
    mock_page.evaluate.return_value = {
        "detected": True,
        "provider": "cloudflare_turnstile",
        "title": "Just a moment...",
        "message": "Cloudflare Turnstile verification required.",
        "has_element": True,
        "bbox": {"x": 100, "y": 200, "width": 300, "height": 65},
    }

    res = await AntiBotDetector.detect(mock_page)
    assert res["detected"] is True
    assert res["provider"] == "cloudflare_turnstile"
    assert "Turnstile" in res["message"]
    assert res["bbox"]["width"] == 300


@pytest.mark.asyncio
async def test_detect_challenge_clean_page():
    mock_page = AsyncMock()
    mock_page.evaluate.return_value = {"detected": False}

    res = await AntiBotDetector.detect(mock_page)
    assert res["detected"] is False

    is_solved = await AntiBotDetector.is_challenge_solved(mock_page)
    assert is_solved is True


# ---------------------------------------------------------------------------
# 2. Residential Proxy Rotator Tests
# ---------------------------------------------------------------------------

def test_proxy_rotator_pool_and_sticky_sessions():
    custom_proxies = [
        "http://user1:pass1@residential-node1.example.com:8080",
        "http://user2:pass2@residential-node2.example.com:8080",
    ]
    rotator = ProxyRotator(proxy_urls=custom_proxies)
    status = rotator.status()
    assert status["total"] == 2
    assert status["healthy"] == 2

    # Session 1 gets assigned sticky proxy
    p1 = rotator.get_proxy("session_tamil_1")
    assert p1 is not None
    assert p1["server"] in [
        "http://residential-node1.example.com:8080",
        "http://residential-node2.example.com:8080",
    ]
    assert p1["username"] in ["user1", "user2"]

    # Session 1 repeated call returns identical sticky proxy
    p1_repeat = rotator.get_proxy("session_tamil_1")
    assert p1["server"] == p1_repeat["server"]

    # Force rotate session 1
    p1_rotated = rotator.get_proxy("session_tamil_1", rotate=True)
    assert p1_rotated["server"] != p1["server"]


def test_proxy_rotator_strike_quarantine():
    custom_proxies = [
        "http://bad-proxy.example.com:8080",
        "http://good-proxy.example.com:8080",
    ]
    rotator = ProxyRotator(proxy_urls=custom_proxies)
    
    # Record strike against bad proxy
    rotator.record_detection("bad-proxy.example.com:8080")
    status = rotator.status()
    
    bad_entry = next(p for p in status["proxies"] if "bad-proxy" in p["url"])
    assert bad_entry["detections"] == 1
    assert bad_entry["available"] is False

    # Next assignment will avoid quarantined proxy and choose the good one
    assigned = rotator.get_proxy("session_test_strike")
    assert assigned["server"] == "http://good-proxy.example.com:8080"


# ---------------------------------------------------------------------------
# 3. Autonomous Multi-Tab Swarm Orchestrator Tests
# ---------------------------------------------------------------------------

def test_siddha_entity_keywords_presence():
    assert "அகத்தியர்" in SIDDHA_KEYWORDS
    assert "ஞானவெட்டியான்" in SIDDHA_KEYWORDS
    assert "ரசவாதம்" in SIDDHA_KEYWORDS
    assert "Agathiyar" in SIDDHA_KEYWORDS
    assert "Bogar" in SIDDHA_KEYWORDS


def test_swarm_cross_reference_synthesis():
    orchestrator = SwarmOrchestrator()
    job = SwarmJob(
        swarm_id="test-swarm-001",
        name="Tamil Siddha Cross Reference Test",
        targets=["http://site1.org", "http://site2.org"],
    )
    
    w1 = SwarmWorkerResult(
        worker_id="w-01",
        url="http://site1.org",
        status="completed",
        title="Agathiyar Gnanavettiyan & Rasavatham",
        siddha_entities=["Agathiyar", "Rasavatham", "Bogar"],
        citations=["http://site1.org/chapter1"],
    )
    w2 = SwarmWorkerResult(
        worker_id="w-02",
        url="http://site2.org",
        status="completed",
        title="Bogar 7000 Navabashanam",
        siddha_entities=["Bogar", "Rasavatham", "அகத்தியர்"],
        citations=["http://site2.org/sutra"],
    )
    job.workers = [w1, w2]

    dossier = orchestrator._synthesize_cross_references(job)
    assert dossier["successful_extractions"] == 2
    assert dossier["cross_verified_entities_count"] >= 3
    
    # Check top frequencies
    ent_map = {item["entity"]: item["frequency"] for item in dossier["cross_verified_entities"]}
    assert ent_map["Bogar"] == 2
    assert ent_map["Rasavatham"] == 2
    assert ent_map["Agathiyar"] == 1


# ---------------------------------------------------------------------------
# 4. Automated SSM Memory Checkpointing Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_threshold_trigger():
    scheduler = MemoryCheckpointScheduler(
        bridge_base_url="http://127.0.0.1:8095",
        token_threshold=50000,
    )
    scheduler._last_checkpoint_tokens = 100000

    # Under threshold (+20,000)
    with patch.object(scheduler, "get_current_bridge_tokens", new_callable=AsyncMock) as mock_tok:
        mock_tok.return_value = 120000
        triggered = await scheduler.check_threshold_and_trigger()
        assert triggered is False

    # Exceeding threshold (+60,000)
    with patch.object(scheduler, "get_current_bridge_tokens", new_callable=AsyncMock) as mock_tok:
        mock_tok.return_value = 160000
        with patch.object(scheduler, "trigger_checkpoint", new_callable=AsyncMock) as mock_trigger:
            mock_trigger.return_value = {"ok": True, "snapshot_name": "threshold_ckpt_test"}
            triggered = await scheduler.check_threshold_and_trigger()
            assert triggered is True
            assert mock_trigger.called


@pytest.mark.asyncio
async def test_manual_checkpoint_trigger():
    scheduler = MemoryCheckpointScheduler(bridge_base_url="http://127.0.0.1:8095")
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "ok": True,
                "snapshot_name": "test_snap_001",
                "ssm_hash": "abc123sha256",
                "duration_ms": 12.5,
            },
        )
        with patch.object(scheduler, "get_current_bridge_tokens", new_callable=AsyncMock) as mock_tok:
            mock_tok.return_value = 5000
            res = await scheduler.trigger_checkpoint(
                name="test_snap_001",
                description="Testing SSM auto-checkpointing",
                trigger="manual",
            )
            assert res["ok"] is True
            assert res["snapshot_name"] == "test_snap_001"
            assert res["trigger"] == "manual"
            assert res["tokens_at_save"] == 5000


# ---------------------------------------------------------------------------
# 5. FastAPI Desktop, Swarm, and Memory Route Integration Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


def test_proxy_endpoints(client):
    res = client.get("/browser/desktop/proxy/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "proxy_rotator" in data

    res2 = client.post("/browser/desktop/proxy/rotate", json={"session_id": "sess_unit_1"})
    assert res2.status_code == 200
    assert res2.json()["ok"] is True


def test_hitl_status_404_on_unknown_session(client):
    res = client.get("/browser/desktop/hitl/status?session_id=nonexistent-session")
    assert res.status_code == 404


def test_swarm_routes(client):
    # Listing swarms
    res = client.get("/browser/desktop/swarm")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "swarms" in data

    # Creating a swarm
    payload = {
        "name": "API Swarm Test",
        "targets": [
            "https://en.wikipedia.org/wiki/Agathiyar",
            "https://en.wikipedia.org/wiki/Bogar",
        ],
        "concurrency": 2,
        "auto_sync_memory": False,
        "auto_checkpoint": False,
    }
    with patch.object(get_swarm_orchestrator(), "_execute_swarm", new_callable=AsyncMock):
        create_res = client.post("/browser/desktop/swarm/start", json=payload)
        assert create_res.status_code == 200
        swarm_info = create_res.json()["swarm"]
        swarm_id = swarm_info["swarm_id"]

        # Fetching the created swarm
        status_res = client.get(f"/browser/desktop/swarm/{swarm_id}")
        assert status_res.status_code == 200
        assert status_res.json()["swarm"]["swarm_id"] == swarm_id


def test_memory_checkpoint_routes(client):
    # Memory scheduler status endpoint
    res = client.get("/browser/desktop/memory/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "scheduler" in data
    assert data["scheduler"]["token_threshold"] == 50000

    # Memory checkpoints list endpoint
    list_res = client.get("/browser/desktop/memory/checkpoints")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["ok"] is True
    assert isinstance(list_data["checkpoints"], list)
