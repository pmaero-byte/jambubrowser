"""Regression: token "savings" counted screenshot base64 as context tokens.

A flow with one screenshot reported tens of thousands of "flow tokens",
skewing every savings figure. Binary payloads are now excluded.
"""
from __future__ import annotations

import asyncio
import base64
import os

from backend.modules.browser_flow import estimate_tokens, strip_binary
from tests.test_browser_flow import FlowPage, make_session

_BLOB = base64.b64encode(os.urandom(30_000)).decode()


def test_base64_keys_are_excluded():
    small = estimate_tokens({"step": "screenshot", "screenshot_base64": "<binary>"})
    big = estimate_tokens({"step": "screenshot", "screenshot_base64": _BLOB})
    assert big == small


def test_data_uri_and_base64_run_in_text_are_excluded():
    text = f"before data:image/png;base64,{_BLOB} after"
    clean, excluded = strip_binary(text)
    assert clean == "before <binary> after"
    assert excluded > 40_000
    clean2, _ = strip_binary({"note": f"raw {_BLOB}"})
    assert clean2 == {"note": "raw <binary>"}


def test_ordinary_text_untouched():
    assert estimate_tokens("x" * 400) == 100
    assert strip_binary("x" * 5000) == ("x" * 5000, 0)
    assert strip_binary({"screenshot": "shots/a.png"}) == ({"screenshot": "shots/a.png"}, 0)
    assert strip_binary({"raw": b"\x00" * 10})[1] == 10


class _ShotPage(FlowPage):
    async def screenshot(self, full_page=False):
        return _BLOB


def test_flow_report_excludes_screenshot():
    page = _ShotPage()
    session = make_session(page)
    report = asyncio.run(session.run_flow([
        {"action": "navigate", "url": "https://example.com/"},
        {"action": "screenshot"},
    ]))
    assert report["ok"] is True
    assert report["binary_chars_excluded"] >= len(_BLOB)  # the blob is in the report...
    assert report["tokens_estimate"] < 5_000  # ...but not in the token count
    assert report["savings"]["flow_tokens"] == report["tokens_estimate"]
