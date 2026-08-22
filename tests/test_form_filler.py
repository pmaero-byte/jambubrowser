"""Tests for backend.modules.form_filler — FormDetector + FormFiller + async entry points."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.vault import get_vault
from backend.modules.form_filler import (
    DetectedForm,
    FormDetector,
    FormFiller,
    FormField,
    detect_and_classify,
    generate_fill_js,
    get_form_filler,
)

# Ensure the vault is unlocked for all tests in this module.
# The test env sets JAMBU_VAULT_KEY, so unlock() will derive the key.
# Re-lock after the module finishes to avoid leaking into other test files.


@pytest.fixture(scope="module", autouse=True)
def _unlock_vault():
    vault = get_vault()
    vault.unlock("test-key-do-not-use-in-production-32bytes!")
    yield
    vault.lock()


@pytest.fixture(autouse=True)
def _clear_vault_credentials():
    """Wipe the vault before every test in this module.

    Other test files (test_engine.py::TestCredentialVault) POST to /login
    which stores credentials in the same module-level vault singleton.
    Without this fixture, the form-fill tests run against a vault that's
    already populated with `example.com/login` and assertFalse fails.
    """
    vault = get_vault()
    try:
        vault.secure_delete_all()
    except Exception:
        # Vault may be locked if running in isolation; ignore.
        pass
    yield


# ── Sample HTML fixtures ─────────────────────────────────────────────────────

LOGIN_HTML = """
<html>
<body>
<form id="login-form" action="/login" method="POST">
  <label for="email">Email</label>
  <input type="email" name="email" id="email" placeholder="you@example.com" required>
  <label for="password">Password</label>
  <input type="password" name="password" id="password" placeholder="Enter password">
  <button type="submit">Sign In</button>
</form>
</body>
</html>
"""

SEARCH_HTML = """
<html>
<body>
<form>
  <input type="text" name="q" placeholder="Search...">
  <button type="submit">Search</button>
</form>
</body>
</html>
"""

REGISTRATION_HTML = """
<html>
<body>
<form id="register">
  <input type="text" name="fullname" placeholder="Full Name">
  <input type="email" name="email" placeholder="Email">
  <input type="password" name="password" placeholder="Password">
  <select name="country">
    <option value="us">US</option>
    <option value="ca">Canada</option>
  </select>
  <input type="submit" value="Register">
</form>
</body>
</html>
"""

MULTI_FORM_HTML = LOGIN_HTML + SEARCH_HTML

NO_FORM_HTML = "<html><body><h1>Hello</h1></body></html>"


# ── FormDetector tests ───────────────────────────────────────────────────────


class TestFormDetector:
    def setup_method(self):
        self.detector = FormDetector()

    def test_detects_login_form(self):
        forms = self.detector.detect_forms(LOGIN_HTML, "https://example.com/login")
        assert len(forms) == 1
        form = forms[0]
        assert form.action_url == "/login"
        assert form.has_submit is True
        assert len(form.fields) == 3  # email + password + submit button
        field_types = {f.field_type for f in form.fields}
        assert "email" in field_types
        assert "password" in field_types
        assert "submit" in field_types

    def test_detects_search_form(self):
        forms = self.detector.detect_forms(SEARCH_HTML, "https://example.com/search")
        assert len(forms) == 1
        form = forms[0]
        field_types = {f.field_type for f in form.fields}
        assert "search" in field_types

    def test_detects_registration_form_with_select(self):
        forms = self.detector.detect_forms(REGISTRATION_HTML, "https://example.com/register")
        assert len(forms) == 1
        form = forms[0]
        assert len(form.fields) >= 3  # fullname, email, password, country
        # Check that a select field is detected
        names = {f.name for f in form.fields}
        assert "country" in names

    def test_detects_multiple_forms(self):
        forms = self.detector.detect_forms(MULTI_FORM_HTML, "https://example.com")
        assert len(forms) == 2

    def test_returns_empty_list_for_no_forms(self):
        forms = self.detector.detect_forms(NO_FORM_HTML, "https://example.com")
        assert forms == []

    def test_classifies_field_types_from_attributes(self):
        """Verify the _classify_field heuristic picks up semantic types from name/label/placeholder."""
        forms = self.detector.detect_forms(SEARCH_HTML, "https://example.com")
        search_field = forms[0].fields[0]
        assert search_field.field_type == "search"

    def test_confidence_is_zero_for_empty_fields(self):
        form = self.detector._compute_confidence([])
        assert form == 0.0

    def test_confidence_increases_with_identifiable_fields(self):
        identifiable = [FormField(name="email", field_type="email", selector='[name="email"]')]
        text_only = [FormField(name="other", field_type="text", selector='[name="other"]')]
        assert self.detector._compute_confidence(identifiable) == 1.0
        assert self.detector._compute_confidence(identifiable + text_only) == 0.5

    def test_build_selector_prefers_name_attribute(self):
        assert self.detector._build_selector("email", "email") == 'input[name="email"]'
        assert self.detector._build_selector("", "password") == 'input[type="password"]'
        assert self.detector._build_selector("", "text") == 'input[type="text"]'

    def test_guess_form_selector_uses_id_or_class(self):
        id_form = '<form id="login-form" action="/">'
        assert "form#login-form" in self.detector._guess_form_selector(id_form)
        class_form = '<form class="myform other" action="/">'
        assert "form.myform" in self.detector._guess_form_selector(class_form)
        no_id_class = '<form action="/">'
        assert self.detector._guess_form_selector(no_id_class) == "form"


# ── FormFiller tests (without vault — unit tests for detection + classification) ──


class TestFormFiller:
    def setup_method(self):
        self.filler = FormFiller()

    def test_detect_and_match_returns_correct_structure(self):
        result = self.filler.detect_and_match(LOGIN_HTML, "https://example.com/login")
        assert result["url"] == "https://example.com/login"
        assert result["domain"] == "example.com"
        assert result["forms_found"] == 1
        assert len(result["forms"]) == 1

    def test_classify_form_login(self):
        result = self.filler.detect_and_match(LOGIN_HTML, "https://example.com/login")
        assert result["forms"][0]["form_type"] == "login"

    def test_classify_form_search(self):
        result = self.filler.detect_and_match(SEARCH_HTML, "https://example.com/search")
        assert result["forms"][0]["form_type"] == "search"

    def test_classify_form_registration(self):
        result = self.filler.detect_and_match(REGISTRATION_HTML, "https://example.com/register")
        assert result["forms"][0]["form_type"] == "registration"

    def test_each_form_has_field_details(self):
        result = self.filler.detect_and_match(LOGIN_HTML, "https://example.com/login")
        fields = result["forms"][0]["fields"]
        assert len(fields) == 3  # email + password + submit button
        for f in fields:
            assert "name" in f
            assert "type" in f
            assert "selector" in f
            assert "label" in f
            assert "required" in f

    def test_empty_page_returns_no_forms(self):
        result = self.filler.detect_and_match(NO_FORM_HTML, "https://example.com")
        assert result["forms_found"] == 0

    def test_no_vault_credential_sets_auto_fillable_false(self):
        result = self.filler.detect_and_match(LOGIN_HTML, "https://example.com/login")
        assert result["forms"][0]["auto_fillable"] is False
        assert result["forms"][0]["matched_credential"] is None


# ── generate_js_fill_script tests ────────────────────────────────────────────


class TestGenerateJSScript:
    def setup_method(self):
        self.filler = FormFiller()

    def test_generates_self_invoking_async_function(self):
        js = self.filler.generate_js_fill_script(
            {'input[name="email"]': "user@example.com"}, submit=False
        )
        assert js.startswith("(async()=>{")
        assert js.endswith("})()")
        assert "document.querySelector('input[name=\"email\"]')" in js
        assert "el.value='user@example.com'" in js

    def test_includes_submit_when_requested(self):
        js = self.filler.generate_js_fill_script(
            {'input[name="email"]': "user@example.com"}, submit=True
        )
        assert "submitBtn.click()" in js
        assert "querySelector('button[type=\"submit\"]" in js

    def test_omits_submit_when_flag_is_false(self):
        js = self.filler.generate_js_fill_script(
            {'input[name="email"]': "user@example.com"}, submit=False
        )
        assert "submitBtn" not in js

    def test_can_generate_empty_script(self):
        js = self.filler.generate_js_fill_script({}, submit=False)
        assert js == "(async()=>{})()"

    def test_does_not_submit_when_empty_and_submit_flagged(self):
        js = self.filler.generate_js_fill_script({}, submit=True)
        assert "submitBtn" not in js  # no fields → no submit

    def test_escapes_single_quotes_in_values(self):
        js = self.filler.generate_js_fill_script(
            {'input[name="name"]': "O'Reilly"}, submit=False
        )
        assert "O\\'Reilly" in js or "\\\\'Reilly" in js

    def test_multiple_selectors_produce_multiple_query_blocks(self):
        js = self.filler.generate_js_fill_script(
            {
                'input[name="email"]': "a@b.com",
                'input[name="password"]': "secret",
            },
            submit=False,
        )
        assert "input[name=\"email\"]" in js
        assert "input[name=\"password\"]" in js


# ── Module-level async entry point tests ─────────────────────────────────────


class TestDetectAndClassify:
    @pytest.mark.asyncio
    async def test_returns_form_data_for_valid_html(self):
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = LOGIN_HTML
            mock_get.return_value = mock_resp

            result = await detect_and_classify("https://example.com/login")
            assert result["forms_found"] == 1
            assert result["forms"][0]["form_type"] == "login"

    @pytest.mark.asyncio
    async def test_passes_url_to_client(self):
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = LOGIN_HTML
            mock_get.return_value = mock_resp

            await detect_and_classify("https://target.com/foo")
            mock_get.assert_called_once_with("https://target.com/foo")

    @pytest.mark.asyncio
    async def test_locked_vault_still_detects_forms(self):
        """Regression: with the vault locked (default state), /forms/detect used to
        500 with PermissionError. Detection must still work — just without any
        matched credential."""
        filler = get_form_filler()
        with (
            patch("httpx.AsyncClient.get") as mock_get,
            patch.object(filler._vault, "find_best_credential", side_effect=PermissionError("Credential vault is locked.")),
        ):
            mock_resp = MagicMock()
            mock_resp.text = LOGIN_HTML
            mock_get.return_value = mock_resp

            result = await detect_and_classify("https://example.com/login")
            assert result["forms_found"] == 1
            assert result["forms"][0]["form_type"] == "login"
            assert result["forms"][0]["matched_credential"] is None
            assert result["forms"][0]["auto_fillable"] is False


class TestGenerateFillJS:
    @pytest.mark.asyncio
    async def test_returns_scripts_when_auto_fillable(self):
        """When vault has a matching credential, the script should be generated."""
        with (
            patch("httpx.AsyncClient.get") as mock_get,
            patch.object(get_form_filler()._vault, "find_best_credential") as mock_find,
        ):
            mock_resp = MagicMock()
            mock_resp.text = LOGIN_HTML
            mock_get.return_value = mock_resp
            mock_find.return_value = {
                "domain": "example.com",
                "username": "user@example.com",
                "password": "secret",
                "metadata": {},
            }

            result = await generate_fill_js("https://example.com/login")
            assert result["forms_found"] == 1
            assert len(result["scripts"]) == 1
            script = result["scripts"][0]
            assert script["form_type"] == "login"
            assert "(async()=>{" in script["script"]

    @pytest.mark.asyncio
    async def test_returns_empty_scripts_when_not_auto_fillable(self):
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = LOGIN_HTML
            mock_get.return_value = mock_resp

            result = await generate_fill_js("https://example.com/login")
            assert result["forms_found"] == 1
            assert result["scripts"] == []
