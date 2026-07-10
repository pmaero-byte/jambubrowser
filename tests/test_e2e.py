"""
Comprehensive E2E Tests
=======================
Tests all major product flows end-to-end.
Requires a running backend on localhost:8001.
"""
import pytest
import httpx
import os

BASE_URL = "http://127.0.0.1:8001"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15) as c:
        yield c


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert "ram_used_gb" in data
        assert "cpu_percent" in data

    def test_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "doc_count" in data
        assert "active_missions" in data
        assert "credentials" in data


class TestPrivacyEndpoints:
    def test_privacy_report(self, client):
        resp = client.get("/privacy/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "privacy" in data
        assert "audit" in data
        assert "vault_status" in data
        p = data["privacy"]
        assert "mode" in p
        assert "local_only" in p
        assert "pii_removal" in p

    def test_privacy_mode_switch(self, client):
        for mode in ["standard", "enhanced", "maximum"]:
            resp = client.post("/privacy/mode", json={"mode": mode})
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_privacy_invalid_mode(self, client):
        resp = client.post("/privacy/mode", json={"mode": "nope"})
        assert resp.status_code == 400

    def test_privacy_check_url(self, client):
        resp = client.get("/privacy/check", params={"url": "https://example.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data


class TestAuditEndpoints:
    def test_audit_stats(self, client):
        resp = client.get("/audit/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_entries" in data
        assert "by_category" in data

    def test_audit_log(self, client):
        resp = client.get("/audit/log", params={"limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_audit_verify_chain(self, client):
        resp = client.get("/audit/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert "valid" in data


class TestVaultEndpoints:
    def test_vault_status(self, client):
        resp = client.get("/vault/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "locked" in data

    def test_vault_unlock(self, client):
        resp = client.post("/vault/unlock", json={"master_password": ""})
        assert resp.status_code == 200
        assert "success" in resp.json()

    def test_vault_lock(self, client):
        resp = client.post("/vault/lock")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_vault_domains(self, client):
        client.post("/vault/unlock", json={"master_password": ""})
        resp = client.get("/vault/domains")
        assert resp.status_code == 200
        assert "domains" in resp.json()


class TestSearchEndpoints:
    def test_search_ddg_fallback(self, client):
        resp = client.get("/search", params={"q": "python programming"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "query" in data


class TestSecurityEndpoints:
    def test_security_verify(self, client):
        resp = client.get("/security/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert "packages" in data
        assert "system_components" in data

    def test_security_verify_package(self, client):
        resp = client.get("/security/verify/package", params={"package_name": "fastapi"})
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "verified" in data


class TestFingerprintEndpoints:
    def test_fingerprint_generate(self, client):
        resp = client.post("/fingerprint/generate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "profile" in data
        assert "playwright_config" in data

    def test_fingerprint_list(self, client):
        resp = client.get("/fingerprint/list")
        assert resp.status_code == 200
        assert "profiles" in resp.json()

    def test_fingerprint_rotate(self, client):
        resp = client.post("/fingerprint/rotate")
        assert resp.status_code == 200
        assert "profile" in resp.json()


class TestMissionEndpoints:
    def test_mission_list(self, client):
        resp = client.get("/mission/list")
        assert resp.status_code == 200
        assert "missions" in resp.json()


class TestKnowledgeGraph:
    def test_knowledge_stats(self, client):
        resp = client.get("/knowledge/stats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_knowledge_graph_data(self, client):
        resp = client.get("/knowledge/graph", params={"max_nodes": 10})
        assert resp.status_code == 200

    def test_knowledge_ingest(self, client):
        resp = client.post("/knowledge/ingest", json={"text": "Test entity"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)


class TestMultimodal:
    def test_multimodal_text(self, client):
        resp = client.post("/multimodal/text", json={"text": "Hello"})
        assert resp.status_code == 200
        assert "input_type" in resp.json()


class TestConsensus:
    def test_consensus_list(self, client):
        resp = client.get("/consensus/list")
        assert resp.status_code == 200
        assert "proposals" in resp.json()

    def test_consensus_propose(self, client):
        resp = client.post("/consensus/propose", json={
            "title": "Test", "description": "A test proposal",
            "options": ["Yes", "No"], "required_nodes": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "proposal_id" in data or "success" in data


class TestFrontendBuild:
    _DIST = "/Users/prabaharan/My_projects/browser_project/browser-app/dist"

    def test_build_exists(self):
        assert os.path.exists(self._DIST)

    def test_index_html(self):
        with open(os.path.join(self._DIST, "index.html")) as f:
            content = f.read()
            assert "Jambu Browser" in content or "Tauri + React + Typescript" in content

    def test_assets_exist(self):
        assets = os.path.join(self._DIST, "assets")
        files = os.listdir(assets)
        assert any(f.endswith(".js") for f in files)
        assert any(f.endswith(".css") for f in files)

    def test_bundle_size(self):
        assets = os.path.join(self._DIST, "assets")
        total = sum(os.path.getsize(os.path.join(assets, f)) for f in os.listdir(assets))
        assert total < 2_500_000, f"Bundle too large: {total} bytes"
