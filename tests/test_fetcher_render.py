"""
Page fetching with optional JavaScript rendering.

The transport policy is tested without a browser or a network: both transports
are injected, so "escalate only when the page looks like a shell" is a
deterministic assertion rather than a hope about someone else's site.
"""

import asyncio
import pytest


# ---------------------------------------------------------------------------
# Shell detection
# ---------------------------------------------------------------------------

RICH_PAGE = """
<html><head><title>An article</title></head><body>
<article>
  <h1>A real article</h1>
  <p>{}</p>
</article></body></html>
""".format("This is a sentence of genuine prose about the subject. " * 8)

SPA_SHELL = """
<html><head><title>App</title><script src="/bundle.js"></script></head>
<body><div id="root"></div><script>window.__DATA__={}</script></body></html>
""".format("x" * 2000)


class TestShellDetection:
    def test_rich_page_is_not_a_shell(self):
        from backend.modules.fetcher import looks_js_shell
        assert looks_js_shell(RICH_PAGE) is False

    def test_spa_mount_point_is_a_shell(self):
        from backend.modules.fetcher import looks_js_shell
        assert looks_js_shell(SPA_SHELL) is True

    def test_large_document_with_no_text_is_a_shell(self):
        from backend.modules.fetcher import looks_js_shell
        html = "<html><body><div>{}</div></body></html>".format("<span></span>" * 200)
        assert looks_js_shell(html) is True

    def test_small_document_is_not_judged(self):
        """Too little to judge: don't launch a browser over a short error page."""
        from backend.modules.fetcher import looks_js_shell
        assert looks_js_shell("<p>Not found</p>") is False
        assert looks_js_shell("") is False

    def test_script_only_document_is_ignored_entirely(self):
        from backend.modules.fetcher import looks_js_shell, readable_text
        html = "<html><body><script>{}</script></body></html>".format("var a=1;" * 200)
        assert readable_text(html) == ""
        assert looks_js_shell(html) is True

    def test_readable_text_strips_code_not_prose(self):
        from backend.modules.fetcher import readable_text
        html = "<style>p{color:red}</style><p>Hello</p><script>x()</script>"
        assert readable_text(html) == "Hello"


# ---------------------------------------------------------------------------
# Transport policy
# ---------------------------------------------------------------------------

def _http(body, status=200):
    async def get(url, max_age=0.0):
        return (status, body)
    return get


def _browser(body):
    async def get(url):
        return body
    return get


def _browser_unavailable():
    async def get(url):
        raise RuntimeError("playwright is not installed")
    return get


class TestRenderPolicy:
    def test_never_uses_http_only(self):
        from backend.modules.fetcher import fetch_page
        called = {"n": 0}

        async def get(url):
            called["n"] += 1
            return "<p>rendered</p>"

        out = asyncio.run(fetch_page(
            "https://x.test/", render="never",
            http_get=_http(RICH_PAGE), browser_get=get,
        ))
        assert out["transport"] == "http"
        assert out["rendered"] is False
        assert called["n"] == 0

    def test_always_uses_the_browser(self):
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="always",
            http_get=_http(RICH_PAGE), browser_get=_browser("<p>rendered</p>"),
        ))
        assert out["transport"] == "browser"
        assert out["rendered"] is True
        assert out["body"] == "<p>rendered</p>"

    def test_auto_stays_on_http_for_a_rich_page(self):
        from backend.modules.fetcher import fetch_page
        called = {"n": 0}

        async def get(url):
            called["n"] += 1
            return "rendered"

        out = asyncio.run(fetch_page(
            "https://x.test/", render="auto",
            http_get=_http(RICH_PAGE), browser_get=get,
        ))
        assert out["transport"] == "http"
        assert called["n"] == 0, "should not launch a browser for a static page"

    def test_auto_escalates_for_a_shell(self):
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="auto",
            http_get=_http(SPA_SHELL), browser_get=_browser("<p>real content</p>"),
        ))
        assert out["transport"] == "browser"
        assert out["rendered"] is True
        assert "escalated" in out["note"]

    def test_always_falls_back_when_the_browser_is_missing(self):
        """A missing Playwright install must not fail the request."""
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="always",
            http_get=_http(RICH_PAGE), browser_get=_browser_unavailable(),
        ))
        assert out["transport"] == "http"
        assert out["rendered"] is False
        assert out["body"] == RICH_PAGE
        assert "unavailable" in out["note"]

    def test_auto_keeps_the_http_body_when_escalation_fails(self):
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="auto",
            http_get=_http(SPA_SHELL), browser_get=_browser_unavailable(),
        ))
        assert out["body"] == SPA_SHELL
        assert out["transport"] == "http"
        assert "unavailable" in out["note"]

    def test_auto_keeps_the_http_body_when_the_browser_returns_nothing(self):
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="auto",
            http_get=_http(SPA_SHELL), browser_get=_browser(""),
        ))
        assert out["body"] == SPA_SHELL
        assert out["transport"] == "http"

    def test_unknown_mode_falls_back_to_auto(self):
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="banana",
            http_get=_http(RICH_PAGE), browser_get=_browser("r"),
        ))
        assert out["transport"] == "http"  # auto, and the page is rich

    def test_http_status_is_preserved(self):
        from backend.modules.fetcher import fetch_page
        out = asyncio.run(fetch_page(
            "https://x.test/", render="never", http_get=_http("gone", status=404),
        ))
        assert out["status"] == 404


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

class TestRenderWiring:
    @pytest.fixture(autouse=True)
    def _jobs(self):
        from backend.modules.crawl_jobs import reset_crawl_jobs
        reset_crawl_jobs()
        yield
        reset_crawl_jobs()

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.core.api_keys import create_api_key
        from backend.engine import app
        raw, _ = create_api_key("render-tests")
        monkeypatch.delenv("JAMBU_WEBDATA_AUTH", raising=False)
        with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
            yield c

    def test_scrape_reports_the_transport(self, client, monkeypatch):
        from backend.routes import webdata

        async def http(url, max_age=0.0):
            return (200, "<p>static page with quite a lot of readable prose here to read</p>")

        monkeypatch.setattr(webdata, "_http_fetch", http)
        body = client.post("/v1/scrape", json={
            "url": "https://x.test/", "render": "never",
        }).json()
        assert body["transport"] == "http"
        assert body["rendered"] is False

    def test_scrape_escalates_and_reports_it(self, client, monkeypatch):
        from backend.routes import webdata

        shell = "<html><body><div id='root'></div>{}</body></html>".format("<!-- filler -->" * 200)

        async def http(url, max_age=0.0):
            return (200, shell)

        async def browser(url):
            return "<p>browser rendered content</p>"

        monkeypatch.setattr(webdata, "_http_fetch", http)
        monkeypatch.setattr(webdata, "_browser_fetch", browser)

        body = client.post("/v1/scrape", json={"url": "https://x.test/", "render": "auto"}).json()
        assert body["transport"] == "browser"
        assert body["rendered"] is True
        assert "browser rendered content" in body["markdown"]

    def test_scrape_rejects_an_unknown_render_mode(self, client):
        r = client.post("/v1/scrape", json={"url": "https://x.test/", "render": "sometimes"})
        assert r.status_code == 422

    def test_crawl_passes_the_render_policy_through(self, client, monkeypatch):
        from backend.routes import webdata
        seen = []

        async def browser(url):
            seen.append(url)
            return "<p>rendered</p>"

        async def http(url, max_age=0.0):
            return (200, "<html><body><div id='root'></div>{}</body></html>".format("<i></i>" * 200))

        monkeypatch.setattr(webdata, "_http_fetch", http)
        monkeypatch.setattr(webdata, "_browser_fetch", browser)

        r = client.post("/v1/crawl", json={
            "url": "https://x.test/", "page_limit": 1, "sync": True, "render": "always",
        })
        assert r.status_code == 200
        assert seen, "the browser transport was never used despite render=always"

    def test_extract_accepts_a_render_mode(self, client, monkeypatch):
        from backend.routes import webdata

        async def http(url, max_age=0.0):
            return (200, "<p>a page with enough readable prose to be judged as static content</p>")

        monkeypatch.setattr(webdata, "_http_fetch", http)

        async def fake_chat(messages):
            return '{"title": "T", "have_enough_information": true}'

        monkeypatch.setattr("backend.core.observations.registry_chat", fake_chat)
        body = client.post("/v1/extract", json={
            "url": "https://x.test/", "render": "never",
            "fields": [{"name": "title", "type": "str"}],
        }).json()
        assert body["success"] is True
        assert body["data"]["title"] == "T"
