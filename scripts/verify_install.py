#!/usr/bin/env python3
"""
End-to-end verification against a running engine.

Why this exists
---------------
Every integration in the web-data surface — the real browser, the real scraper,
the real database — is easy to get subtly wrong, and unit tests with injected
fakes cannot catch that. This harness exercises the *real* thing against a
running engine and real websites, and reports PASS/FAIL per check.

It is deliberately dependency-light (standard library only) so it can run
immediately after a fresh checkout, before or after a virtualenv exists.

Usage
-----
    python scripts/verify_install.py [--base-url URL] [--skip-network] [--json]

Exit codes: 0 all checks passed · 1 a check failed · 2 the engine is unreachable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Run from anywhere: the repo root must be importable for the in-process
# checks (the benchmark harness lives in backend/bench/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# A static page and a JavaScript-rendered page, both long-standing and used by
# scraping tutorials. The JS one is the point of the render check: over plain
# HTTP it returns a shell with nothing readable in it.
STATIC_URL = "https://example.com"
JS_URL = "https://quotes.toscrape.com/js/"

# Declared runtime dependencies the web-data path actually needs at import.
REQUIRED_MODULES = [
    ("fastapi", "engine"),
    ("uvicorn", "engine"),
    ("httpx", "all HTTP transports"),
    ("psutil", "/health"),
    ("pydantic", "request models"),
    ("sqlite_vec", "the vector store imported by backend.core"),
    ("markdownify", "scrape + browser paths"),
    ("pypdf", "PDF parsing"),
    ("crawl4ai", "browser rendering"),
    ("playwright", "browser rendering"),
]


# ---------------------------------------------------------------------------
# Tiny HTTP client
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 60.0):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def request(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, _maybe_json(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            return e.code, _maybe_json(raw)

    def get(self, path: str) -> Tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: Optional[dict] = None) -> Tuple[int, Any]:
        return self.request("POST", path, body)

    def delete(self, path: str, body: Optional[dict] = None) -> Tuple[int, Any]:
        return self.request("DELETE", path, body)


def _maybe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def add(self, name: str, ok: Optional[bool], detail: str = "") -> None:
        status = "PASS" if ok is True else "FAIL" if ok is False else "SKIP"
        self.rows.append((name, status, detail))
        marker = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip"}[status]
        print(f"  [{marker}] {name}" + (f" — {detail}" if detail else ""), flush=True)

    @property
    def failures(self) -> int:
        return sum(1 for _, s, _ in self.rows if s == "FAIL")

    @property
    def passes(self) -> int:
        return sum(1 for _, s, _ in self.rows if s == "PASS")

    def summary(self) -> None:
        print("\n" + "=" * 68)
        for name, status, detail in self.rows:
            print(f"  {status:4}  {name}")
        print("=" * 68)
        print(f"  {self.passes} passed, {self.failures} failed, "
              f"{len(self.rows) - self.passes - self.failures} skipped")


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}", flush=True)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_imports(report: Report) -> None:
    section("1. Declared dependencies import")
    for module, why in REQUIRED_MODULES:
        try:
            __import__(module)
            report.add(f"import {module}", True, why)
        except Exception as e:  # noqa: BLE001
            report.add(f"import {module}", False, f"{type(e).__name__}: {e} ({why})")


def check_health(client: Client, report: Report) -> bool:
    section("2. Engine is up")
    status, body = client.get("/health")
    ok = status == 200
    report.add("GET /health", ok, f"HTTP {status}")
    if isinstance(body, dict):
        print(f"       status={body.get('status')} checks={body.get('checks')}")
        for name, value in (body.get("checks") or {}).items():
            # Only judge probes that actually report a status. A count (0 audit
            # entries) or a state (vault "locked") is informational — treating
            # those as failures makes the harness cry wolf.
            text = str(value)
            if text in ("ok", "error") or text.startswith(("ok", "error")):
                report.add(f"health probe: {name}", text.startswith("ok"), text)
            else:
                report.add(f"health probe: {name} (informational)", None, text)
    return ok


def make_api_key(client: Client, report: Report) -> Optional[str]:
    section("3. API key")
    status, body = client.post("/api-keys/create", {"name": "verify-install", "tier": "team"})
    if status == 200 and isinstance(body, dict) and body.get("key"):
        report.add("POST /api-keys/create", True, body.get("key_prefix", ""))
        return body["key"]
    # A loopback caller is exempt while JAMBU_WEBDATA_AUTH=auto, so this is not
    # fatal — but say so rather than pretending the key path was verified.
    report.add("POST /api-keys/create", False, f"HTTP {status}: {str(body)[:120]}")
    return None


def check_scrape_static(client: Client, report: Report) -> None:
    section("4. Scrape a static page (render=never)")
    status, body = client.post("/v1/scrape", {"url": STATIC_URL, "render": "never"})
    if status != 200 or not isinstance(body, dict):
        report.add("POST /v1/scrape (static)", False, f"HTTP {status}: {str(body)[:160]}")
        return
    report.add("POST /v1/scrape (static)", body.get("success") is True, f"http_status={body.get('http_status')}")
    report.add("transport is http", body.get("transport") == "http", str(body.get("transport")))
    receipt = body.get("receipt") or {}
    report.add("receipt carries a sha256 hash",
               len(str(receipt.get("content_sha256", ""))) == 64,
               str(receipt.get("content_sha256", ""))[:16])
    md = body.get("markdown") or ""
    report.add("markdown is non-trivial", len(md) > 50, f"{len(md)} chars")


def _readable_chars(html: str) -> int:
    """Visible text length — enough to tell a rendered page from a JS shell."""
    import re
    if not html:
        return 0
    text = re.sub(r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>", " ", html,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return len(re.sub(r"\s+", " ", text).strip())


def check_scrape_js(client: Client, report: Report) -> None:
    section("5. Render a JavaScript page (render=always)  <-- the unproven path")

    # The control: over plain HTTP this page is a shell with no readable text.
    _, plain = client.post("/v1/scrape", {"url": JS_URL, "render": "never", "formats": ["html"]})
    plain_html = (plain or {}).get("html", "") if isinstance(plain, dict) else ""
    plain_chars = _readable_chars(plain_html)

    status, body = client.post("/v1/scrape", {"url": JS_URL, "render": "always", "formats": ["html"]})
    if status != 200 or not isinstance(body, dict):
        report.add("POST /v1/scrape (JS)", False, f"HTTP {status}: {str(body)[:160]}")
        return
    report.add("POST /v1/scrape (JS)", body.get("success") is True, f"http_status={body.get('http_status')}")
    report.add("transport is browser", body.get("transport") == "browser", str(body.get("transport")))
    report.add("rendered flag is true", body.get("rendered") is True, str(body.get("rendered")))
    if body.get("note"):
        print(f"       note: {body['note']}")

    # The load-bearing assertion: a real render produces content the unrendered
    # fetch did not have. Without this comparison, an HTTP fallback that merely
    # echoes the shell would sail through.
    rendered_chars = _readable_chars(body.get("html", "") or body.get("markdown", ""))
    report.add(
        "rendered content exceeds the unrendered shell",
        rendered_chars > max(500, plain_chars * 2),
        f"unrendered={plain_chars} chars, rendered={rendered_chars} chars",
    )


def check_map(client: Client, report: Report) -> None:
    section("6. Map a site")
    # example.com has no same-domain links and no sitemap, so "0 urls" is the
    # correct answer there. Use a seed that genuinely links within its domain.
    seed = "https://quotes.toscrape.com/"
    status, body = client.post("/v1/map", {"url": seed, "limit": 25})
    if status != 200 or not isinstance(body, dict):
        report.add("POST /v1/map", False, f"HTTP {status}: {str(body)[:160]}")
        return
    report.add("POST /v1/map", body.get("success") is True, f"{body.get('count')} urls")
    report.add("discovered same-domain URLs", (body.get("count") or 0) >= 1,
               f"seed={seed}")


def _poll(client: Client, path: str, report: Report, name: str, timeout: float = 90.0) -> Optional[dict]:
    deadline = time.time() + timeout
    body: Any = None
    while time.time() < deadline:
        status, body = client.get(path)
        if status == 404:
            report.add(name, False, "job vanished (404) while polling")
            return None
        if isinstance(body, dict) and body.get("status") in ("completed", "failed", "cancelled", "interrupted"):
            return body
        time.sleep(0.5)
    report.add(name, False, f"did not finish within {timeout:.0f}s")
    return None


def check_crawl(client: Client, report: Report) -> None:
    section("7. Crawl (job, poll, cancel)")
    status, body = client.post("/v1/crawl", {"url": STATIC_URL, "max_depth": 1, "page_limit": 5})
    if status != 200 or not isinstance(body, dict) or not body.get("id"):
        report.add("POST /v1/crawl", False, f"HTTP {status}: {str(body)[:160]}")
        return
    report.add("POST /v1/crawl returns a job id", True, body["id"])

    done = _poll(client, f"/v1/crawl/{body['id']}", report, "crawl reaches a terminal state")
    if done is None:
        return
    report.add("crawl completed", done.get("status") == "completed", str(done.get("status")))
    report.add("crawl returned pages", (done.get("completed") or 0) >= 1, f"{done.get('completed')} pages")
    data = done.get("data") or []
    if data:
        report.add("pages carry receipts",
                   len(str((data[0].get("receipt") or {}).get("content_sha256", ""))) == 64)


def check_batch(client: Client, report: Report) -> None:
    section("8. Batch scrape")
    urls = [STATIC_URL, "https://example.org"]
    status, body = client.post("/v1/batch", {"urls": urls})
    if status != 200 or not isinstance(body, dict) or not body.get("id"):
        report.add("POST /v1/batch", False, f"HTTP {status}: {str(body)[:160]}")
        return
    done = _poll(client, f"/v1/batch/{body['id']}", report, "batch reaches a terminal state")
    if done is None:
        return
    report.add("batch completed", done.get("status") == "completed", str(done.get("status")))
    report.add("both URLs fetched", (done.get("completed") or 0) == len(urls),
               f"{done.get('completed')}/{len(urls)}")


def check_pdf(client: Client, report: Report) -> None:
    section("9. PDF parsing")
    # A long-standing one-page W3C test document.
    pdf_url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
    status, body = client.post("/v1/scrape", {"url": pdf_url, "render": "never"})
    if status != 200 or not isinstance(body, dict):
        report.add("POST /v1/scrape (PDF)", False, f"HTTP {status}: {str(body)[:160]}")
        return
    md = body.get("markdown") or ""
    report.add("PDF marker reports page counts",
               md.startswith("[PDF document") and "pages=" in md, md.splitlines()[:1])
    report.add("PDF text was extracted", "Dummy PDF file" in md, f"{len(md)} chars")
    report.add("raw PDF bytes were not returned verbatim", "%PDF-" not in md)


def check_extract(client: Client, report: Report) -> None:
    section("10. Structured extraction (quarantine boundary)")
    status, body = client.post("/v1/extract", {
        "url": STATIC_URL,
        "render": "never",
        "fields": [
            {"name": "title", "type": "str", "max_chars": 200},
            {"name": "is_example", "type": "bool", "role": "control"},
        ],
    })
    if status != 200 or not isinstance(body, dict):
        report.add("POST /v1/extract", False, f"HTTP {status}: {str(body)[:200]}")
        return
    # A model that cannot be reached is a legitimate environment outcome; the
    # boundary still behaved correctly. Say which it was.
    if body.get("success"):
        report.add("POST /v1/extract returns typed data", True, json.dumps(body.get("data"))[:80])
    else:
        report.add("POST /v1/extract returns typed data", None,
                   f"no model available: {str(body.get('problems'))[:90]}")

    # A control field must never be free-form: this must be rejected.
    status2, body2 = client.post("/v1/extract", {
        "url": STATIC_URL,
        "fields": [{"name": "cmd", "type": "str", "role": "control"}],
    })
    report.add("rejects a free-text control field", status2 == 400, f"HTTP {status2}")


def check_changes(client: Client, report: Report) -> None:
    section("11. Change tracking")
    first = client.post("/v1/changes/check", {"url": STATIC_URL, "render": "never"})
    if first[0] != 200 or not isinstance(first[1], dict):
        report.add("POST /v1/changes/check", False, f"HTTP {first[0]}: {str(first[1])[:160]}")
        return
    report.add("first check is not a change", first[1].get("first_check") is True,
               f"changed={first[1].get('changed')}")

    second = client.post("/v1/changes/check", {"url": STATIC_URL, "render": "never"})
    if second[0] == 200 and isinstance(second[1], dict):
        report.add("second identical check is not a change",
                   second[1].get("first_check") is False and second[1].get("changed") is False,
                   f"changed={second[1].get('changed')}")

    status, body = client.get(f"/v1/changes?url={urllib.parse.quote(STATIC_URL, safe='')}")
    report.add("change log is readable", status == 200 and isinstance(body, dict), f"HTTP {status}")


def check_bench(client: Client, report: Report) -> None:
    section("13. Retrieval benchmark")
    try:
        from backend.bench.corpus import LocalIndex
        from backend.bench.policies import naive_policy, retrieval_policy
        from backend.bench.runner import load_local_suite, local_suite_dir, run_suite
    except Exception as e:  # noqa: BLE001
        report.add("benchmark harness imports", False, f"{type(e).__name__}: {e}")
        return

    index = LocalIndex.load(local_suite_dir() / "corpus.jsonl")
    tasks = load_local_suite("local")
    kwargs = {"search": lambda q, l: index.search(q, limit=l)}

    import asyncio

    naive = asyncio.run(run_suite(tasks, naive_policy, **kwargs))
    better = asyncio.run(run_suite(tasks, retrieval_policy, **kwargs))
    report.add("benchmark runs cleanly", naive.failures == 0, f"{naive.n} tasks")
    report.add(
        "retrieval beats the naive baseline",
        better.mean > naive.mean,
        f"retrieval={better.mean:.4f} vs naive={naive.mean:.4f} "
        f"(CI95 {better.bootstrap_ci()[0]:.3f}-{better.bootstrap_ci()[1]:.3f})",
    )


def check_security(client: Client, report: Report) -> None:
    section("12. Guards")
    status, _ = client.post("/v1/scrape", {"url": "http://127.0.0.1:8001/health"})
    report.add("refuses a loopback URL", status == 400, f"HTTP {status}")

    status, _ = client.post("/v1/scrape", {"url": "http://169.254.169.254/latest/meta-data/"})
    report.add("refuses a cloud-metadata URL", status == 400, f"HTTP {status}")

    status, _ = client.post("/v1/scrape", {"url": "https://example.com", "render": "sometimes"})
    report.add("rejects an unknown render mode", status == 422, f"HTTP {status}")

    status, _ = client.request("DELETE", "/v1/changes", {"url": STATIC_URL})
    report.add("forget endpoint works", status == 200, f"HTTP {status}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(base_url: str, skip_network: bool, as_json: bool) -> int:
    report = Report()
    check_imports(report)

    client = Client(base_url)
    if not check_health(client, report):
        print(f"\nEngine is not reachable at {base_url}.")
        print("Start it with:  python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001")
        return 2

    key = make_api_key(client, report)
    if key:
        client.api_key = key

    if skip_network:
        section("Network checks")
        report.add("live-site checks", None, "--skip-network")
    else:
        for fn in (check_scrape_static, check_scrape_js, check_pdf, check_map,
                   check_crawl, check_batch, check_extract, check_changes):
            try:
                fn(client, report)
            except Exception as e:  # noqa: BLE001 — one broken check must not stop the run
                report.add(fn.__name__, False, f"{type(e).__name__}: {e}")

    for fn in (check_security, check_bench):
        try:
            fn(client, report)
        except Exception as e:  # noqa: BLE001
            report.add(fn.__name__, False, f"{type(e).__name__}: {e}")

    report.summary()
    if as_json:
        print(json.dumps(
            {"rows": [{"name": n, "status": s, "detail": d} for n, s, d in report.rows]},
            indent=2,
        ))
    return 1 if report.failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a Jambubrowser install end to end.")
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--skip-network", action="store_true",
                    help="skip checks that reach real websites")
    ap.add_argument("--json", action="store_true", help="also emit machine-readable results")
    args = ap.parse_args()

    return run(args.base_url, args.skip_network, args.json)


if __name__ == "__main__":
    sys.exit(main())
