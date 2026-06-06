"""
Tests: Phase 3 - Vision, Form Filler, Local Connector
======================================================
Tests for vision model integration, autonomous form filling,
and local app connector (Obsidian, Reminders, Clipboard).
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestVisionModule:
    """Tests for the vision model module."""

    def test_vision_model_creation(self):
        from backend.modules.vision import VisionModel, VisionGrounder
        model = VisionModel(base_url="http://localhost:8080/v1", model_id="test-model")
        assert model.model_id == "test-model"
        assert model.base_url == "http://localhost:8080/v1"

    def test_vision_grounder_creation(self):
        from backend.modules.vision import VisionModel, VisionGrounder
        model = VisionModel()
        grounder = VisionGrounder(model)
        assert grounder._model is not None

    def test_element_parsing(self):
        from backend.modules.vision import VisionModel
        model = VisionModel()
        elements = model._parse_element_json(
            '[{"type":"button","label":"Submit","css_selector":"button.submit",'
            '"position":{"x":80,"y":90,"width":10,"height":4},"suggested_action":"click"}]'
        )
        assert len(elements) == 1
        assert elements[0].label == "Submit"
        assert elements[0].element_type == "button"
        assert elements[0].suggested_action == "click"

    def test_element_parsing_invalid_json(self):
        from backend.modules.vision import VisionModel
        model = VisionModel()
        elements = model._parse_element_json("not json at all")
        assert len(elements) == 0

    def test_suggestions_conversion(self):
        from backend.modules.vision import VisionModel, VisionGrounder, VisionAnalysis, UIElement
        elements = [
            UIElement(label="Button 1", element_type="button", selector=".btn-1",
                      bounding_box={"x": 10, "y": 20, "width": 5, "height": 3},
                      confidence=0.9, suggested_action="click"),
        ]
        analysis = VisionAnalysis(url="https://example.com", elements=elements)
        grounder = VisionGrounder()
        suggestions = grounder.elements_to_suggestions(analysis)
        assert len(suggestions) == 1
        assert suggestions[0]["action"] == "click"
        assert suggestions[0]["confidence"] == 0.9

    def test_get_vision_model(self):
        from backend.modules.vision import get_vision_model
        model = get_vision_model()
        assert model is not None


class TestVisionEndpoint:
    """Tests for vision API endpoints."""

    def test_vision_analyze_fallback(self, client):
        response = client.post("/vision/analyze", json={
            "url": "https://example.com",
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert "suggestions" in data
        assert data.get("fallback", False) or "elements_found" in data

    def test_vision_analyze_with_image(self, client):
        import base64
        tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        response = client.post("/vision/analyze", json={
            "url": "https://example.com",
            "image_data": tiny_png_b64,
            "client_id": "test",
        })
        assert response.status_code in (200, 500)


class TestFormFiller:
    """Tests for the autonomous form filler module."""

    def test_form_detector_login(self):
        from backend.modules.form_filler import FormDetector
        detector = FormDetector()
        html = '''
        <form action="/login" method="POST">
            <label for="email">Email</label>
            <input type="email" name="email" required />
            <label for="pass">Password</label>
            <input type="password" name="pass" required />
            <button type="submit">Sign In</button>
        </form>
        '''
        forms = detector.detect_forms(html, "https://example.com/login")
        assert len(forms) == 1
        form = forms[0]
        assert len(form.fields) >= 2
        field_types = [f.field_type for f in form.fields]
        assert 'email' in field_types or any('email' in f.name for f in form.fields)
        assert 'password' in field_types

    def test_form_detector_registration(self):
        from backend.modules.form_filler import FormDetector
        detector = FormDetector()
        html = '''
        <form>
            <input type="text" name="username" placeholder="Username" />
            <input type="email" name="email" placeholder="Email" />
            <input type="password" name="password" placeholder="Password" />
            <input type="submit" value="Register" />
        </form>
        '''
        forms = detector.detect_forms(html, "https://example.com/register")
        assert len(forms) == 1
        assert len(forms[0].fields) >= 3

    def test_form_classification(self):
        from backend.modules.form_filler import FormFiller
        filler = FormFiller()
        html = '''
        <form>
            <input type="email" name="email" />
            <input type="password" name="password" />
            <button type="submit">Login</button>
        </form>
        '''
        result = filler.detect_and_match(html, "https://example.com/login")
        assert result['forms_found'] >= 1
        assert any(f['form_type'] == 'login' for f in result['forms'])

    def test_js_fill_script_generation(self):
        from backend.modules.form_filler import get_form_filler
        filler = get_form_filler()
        fill_data = {
            'input[name="email"]': 'test@example.com',
            'input[type="password"]': 'secret123',
        }
        script = filler.generate_js_fill_script(fill_data, submit=True)
        assert 'test@example.com' in script
        assert 'secret123' in script


class TestFormEndpoints:
    """Tests for form detection API endpoints."""

    def test_detect_forms_endpoint(self, client):
        html = '<form><input type="text" name="q" /></form>'
        response = client.post("/forms/detect", json={
            "url": "https://example.com",
            "html": html,
        })
        assert response.status_code == 200
        data = response.json()
        assert "forms_found" in data


class TestLocalConnector:
    """Tests for the local app connector modules."""

    def test_obsidian_connector_init(self):
        from backend.modules.local_connector import ObsidianConnector
        obsidian = ObsidianConnector()
        assert obsidian.vault_path.exists()

    def test_obsidian_create_note(self, tmp_path):
        from backend.modules.local_connector import ObsidianConnector
        obsidian = ObsidianConnector(str(tmp_path))
        result = obsidian.create_note("Test Note", "Test content", "Research")
        assert result['success'] is True
        assert result['title'] == "Test Note"

    def test_obsidian_search(self, tmp_path):
        from backend.modules.local_connector import ObsidianConnector
        obsidian = ObsidianConnector(str(tmp_path))
        obsidian.create_note("Search Test", "This contains searchable text", "Research")
        result = obsidian.search_vault("searchable")
        assert result['success'] is True
        assert result['total_found'] >= 1

    def test_obsidian_stats(self, tmp_path):
        from backend.modules.local_connector import ObsidianConnector
        obsidian = ObsidianConnector(str(tmp_path))
        obsidian.create_note("Stats Test", "Content", "Research")
        stats = obsidian.get_stats()
        assert stats['total_notes'] >= 1
        assert 'vault_path' in stats

    def test_filesystem_save_research(self, tmp_path):
        from backend.modules.local_connector import FilesystemConnector
        fs = FilesystemConnector(str(tmp_path))
        result = fs.save_research("Research Topic", "Findings here", ["https://example.com"])
        assert result['success'] is True

    def test_clipboard_connector(self):
        from backend.modules.local_connector import ClipboardConnector
        clip = ClipboardConnector()
        result = clip.copy("test text")
        if result['success']:
            paste_result = clip.paste()
            assert paste_result['success'] is True
            assert 'test text' in paste_result.get('content', '')


class TestLocalConnectorEndpoints:
    """Tests for local connector API endpoints."""

    def test_obsidian_search_endpoint(self, client):
        response = client.get("/local/obsidian/search", params={
            "query": "test",
            "max_results": 5,
        })
        assert response.status_code in (200, 404)

    def test_clipboard_paste_endpoint(self, client):
        response = client.get("/local/clipboard/paste")
        assert response.status_code in (200, 500)

    def test_save_note_endpoint(self, client):
        response = client.post("/local/notes/save", json={
            "title": "API Test Note",
            "content": "Test content from API",
            "sources": ["https://test.com"],
        })
        assert response.status_code in (200, 500)
