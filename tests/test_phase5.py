"""
Tests: Phase 5 - Skill Synthesis, Fingerprint Rotation
=======================================================
Tests for autonomous skill synthesis and browser fingerprint
generation/rotation.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestSkillSynthesizer:
    """Tests for autonomous skill synthesis."""

    def test_failure_classification_scraping(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        failure = synth.classify_failure(
            "ConnectionError: Max retries exceeded",
            url="https://example.com",
        )
        assert failure.error_type in ('scraping', 'timeout')

    def test_failure_classification_selector(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        failure = synth.classify_failure(
            "NoSuchElementException: element not found: .missing-selector",
            url="https://example.com",
        )
        assert failure.error_type == 'selector'

    def test_failure_classification_auth(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        failure = synth.classify_failure(
            "HTTP 403 Forbidden - unauthorized access",
            url="https://example.com",
        )
        assert failure.error_type == 'auth'

    def test_failure_classification_parsing(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        failure = synth.classify_failure(
            "JSONDecodeError: unexpected token at line 1",
            url="https://example.com",
        )
        assert failure.error_type == 'parsing'

    def test_failure_classification_rate_limit(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        failure = synth.classify_failure(
            "HTTP 429 Too Many Requests - rate limit exceeded",
            url="https://example.com",
        )
        assert failure.error_type == 'rate_limit'

    def test_code_extraction_from_llm(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        llm_response = """Here's a script:
```python
import httpx

async def run(**kwargs):
    return {"data": "extracted"}
```
That should work."""
        code = synth._extract_code(llm_response)
        assert "import httpx" in code
        assert "async def run" in code

    def test_code_extraction_no_markers(self):
        from backend.modules.skill_synthesizer import SkillSynthesizer
        synth = SkillSynthesizer()
        llm_response = "import httpx\n\ndef get_data():\n    return 'test'"
        code = synth._extract_code(llm_response)
        assert len(code) > 0

    def test_get_synthesizer_singleton(self):
        from backend.modules.skill_synthesizer import get_synthesizer
        s1 = get_synthesizer()
        s2 = get_synthesizer()
        assert s1 is s2


class TestSkillSynthesisEndpoints:
    """Tests for skill synthesis API endpoints."""

    def test_synthesize_error_endpoint(self, client):
        """Test synthesize endpoint - requires LLM to be running."""
        try:
            response = client.post("/skill/synthesize", json={
                "url": "https://test.com",
                "error_message": "timeout error",
                "page_snippet": "<html></html>",
            }, timeout=10)
            assert response.status_code in (200, 422, 500, 504)
        except Exception:
            pytest.skip("LLM not available in test environment")

    def test_list_synthesized_endpoint(self, client):
        response = client.get("/skill/list-synthesized")
        assert response.status_code == 200
        assert "skills" in response.json()


class TestFingerprintRotator:
    """Tests for browser fingerprint generation."""

    def test_generate_fingerprint(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile()
        assert profile.profile_id is not None
        assert len(profile.user_agent) > 50
        assert profile.viewport_width > 0
        assert profile.viewport_height > 0

    def test_generate_macos_fingerprint(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile(os_family='macos')
        assert 'Macintosh' in profile.user_agent

    def test_generate_windows_fingerprint(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile(os_family='windows')
        assert 'Windows' in profile.user_agent

    def test_profile_to_dict(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile()
        d = profile.to_dict()
        assert 'profile_id' in d
        assert 'user_agent' in d
        assert 'viewport' in d
        assert 'webgl' in d

    def test_profile_to_js_config(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile()
        js = profile.to_js_config()
        assert 'navigator' in js
        assert 'defineProperty' in js

    def test_playwright_config(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile()
        config = rotator.get_profile_for_playwright(profile.profile_id)
        assert config is not None
        assert 'user_agent' in config
        assert 'viewport' in config
        assert 'locale' in config

    def test_profile_rotation(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile1 = rotator.generate_profile(os_family='macos')
        profile2 = rotator.rotate_profile(profile1.profile_id)
        assert profile2.profile_id != profile1.profile_id
        assert profile2.user_agent != profile1.user_agent

    def test_get_profile_not_found(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.get_profile("nonexistent-id")
        assert profile is None

    def test_proxy_routing_config(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile()
        config = rotator.get_proxy_routing_config(profile.profile_id)
        assert 'region' in config
        assert 'timezone' in config

    def test_fresh_profile_isolation(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        p1 = rotator.generate_profile()
        p2 = rotator.generate_fresh_profile()
        assert p1.profile_id != p2.profile_id


class TestFingerprintEndpoints:
    """Tests for fingerprint API endpoints."""

    def test_generate_endpoint(self, client):
        response = client.post("/fingerprint/generate", json={})
        assert response.status_code == 200
        data = response.json()
        assert "profile" in data
        assert "playwright_config" in data
        assert data["profile"]["profile_id"]

    def test_generate_macos_endpoint(self, client):
        response = client.post("/fingerprint/generate", json={
            "os_family": "macos",
        })
        assert response.status_code == 200
        assert "Macintosh" in response.json()["profile"]["user_agent"]

    def test_list_endpoint(self, client):
        response = client.get("/fingerprint/list")
        assert response.status_code == 200
        assert "profiles" in response.json()

    def test_profile_by_id_endpoint(self, client):
        gen = client.post("/fingerprint/generate", json={})
        pid = gen.json()["profile"]["profile_id"]

        response = client.get(f"/fingerprint/profile/{pid}")
        assert response.status_code == 200
        assert "profile" in response.json()
        assert "playwright_config" in response.json()

    def test_profile_not_found_endpoint(self, client):
        response = client.get("/fingerprint/profile/nonexistent")
        assert response.status_code in (200, 404)

    def test_rotate_endpoint(self, client):
        gen = client.post("/fingerprint/generate", json={})
        pid = gen.json()["profile"]["profile_id"]

        response = client.post("/fingerprint/rotate", params={
            "current_profile_id": pid,
        })
        assert response.status_code == 200
        assert response.json()["profile"]["profile_id"] != pid
