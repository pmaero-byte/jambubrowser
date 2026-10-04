"""The /research pipeline phases.

The endpoint was one 218-line function with the same seven-line
"cancelled → broadcast → return" block written out six times. The phases are now
named functions, which makes them testable — and these tests cover the decisions
that are easy to get subtly wrong:

* **ranking** is a pure function, so its ordering is pinned directly: a trusted
  domain outranks a higher-scoring untrusted one, duplicates collapse, and
  `top_n` truncates *after* ranking;
* **screening** happens on search URLs, before anything is fetched;
* **cancelling** returns a fresh dict every time — a shared module-level
  constant would be mutated by the first caller that edited it;
* a failing search engine or a failing scrape loses one source, not the run.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.routes import research as research_mod
from backend.routes.research import (
    INTERRUPTED_RESPONSE,
    TRUSTED_DOMAINS,
    _index_sources,
    _interrupted,
    _rank_results,
    _screen_sources,
    _scrape_sources,
)


class FakeRequest:
    """Only the fields the phases read."""

    def __init__(self, **over):
        self.query = "quantum computing"
        self.domain = "general"
        self.client_id = "test"
        self.top_n = 5
        self.llm_config = None
        self.__dict__.update(over)


# ---------------------------------------------------------------------------
# _rank_results — pure
# ---------------------------------------------------------------------------

class TestRankResults:
    def test_empty_input(self):
        assert _rank_results([], 5) == []

    def test_deduplicates_by_url_keeping_the_first(self):
        results = [
            {"url": "https://a/", "content": "first", "score": 1},
            {"url": "https://a/", "content": "second", "score": 99},
        ]
        out = _rank_results(results, 5)
        assert len(out) == 1
        assert out[0]["content"] == "first"

    def test_results_without_a_url_are_dropped(self):
        out = _rank_results([{"url": "", "score": 10}, {"score": 10}], 5)
        assert out == []

    def test_trusted_domain_outranks_a_higher_score(self):
        """One trusted hit is worth five points — it must beat score 10."""
        results = [
            {"url": "https://blog.example.com/post", "score": 10},
            {"url": "https://arxiv.org/abs/1234", "score": 1},
        ]
        out = _rank_results(results, 5)
        assert out[0]["url"] == "https://arxiv.org/abs/1234"

    def test_among_equals_the_higher_score_wins(self):
        results = [
            {"url": "https://a.example.com/", "score": 1},
            {"url": "https://b.example.com/", "score": 9},
        ]
        out = _rank_results(results, 5)
        assert [r["url"] for r in out] == [
            "https://b.example.com/", "https://a.example.com/",
        ]

    def test_multiple_trusted_hits_stack(self):
        """Trust adds up, so a doubly-trusted URL sorts above a single one."""
        results = [
            {"url": "https://wikipedia.org/wiki/A", "score": 50},   # .org + wikipedia
            {"url": "https://mit.edu/x", "score": 1},                # .edu
        ]
        out = _rank_results(results, 5)
        assert out[0]["url"] == "https://wikipedia.org/wiki/A"

    def test_top_n_truncates_after_ranking(self):
        results = [{"url": f"https://{i}.example.com/", "score": i} for i in range(10)]
        out = _rank_results(results, 3)
        assert len(out) == 3
        assert out[0]["score"] == 9          # the best survived, not the first seen

    def test_trusted_list_covers_the_awkward_suffixes(self):
        """.gov.uk and .edu.au are not registrable suffixes but are trusted."""
        assert ".gov" in TRUSTED_DOMAINS and ".edu" in TRUSTED_DOMAINS
        out = _rank_results([{"url": "https://agency.gov.uk/x", "score": 0},
                             {"url": "https://shop.example/", "score": 50}], 5)
        assert out[0]["url"] == "https://agency.gov.uk/x"


# ---------------------------------------------------------------------------
# _screen_sources
# ---------------------------------------------------------------------------

class TestScreenSources:
    def test_risky_urls_are_dropped_and_safe_ones_kept(self, monkeypatch):
        async def assess(url, client_id, llm_config):
            return "evil" in url

        monkeypatch.setattr(research_mod, "_assess_url_risk", assess)
        results = [{"url": "https://evil.test/"}, {"url": "https://good.test/"}]
        out = asyncio.run(_screen_sources(FakeRequest(), results, "cid"))
        assert [r["url"] for r in out] == ["https://good.test/"]

    def test_screening_runs_before_any_fetch(self, monkeypatch):
        """The point of screening on search URLs: never fetch a flagged host."""
        fetched: list[str] = []

        async def assess(url, client_id, llm_config):
            fetched.append(url)
            return True                       # everything is risky

        async def scrape(url):                # pragma: no cover - must not run
            raise AssertionError("screened URLs must not be scraped")

        monkeypatch.setattr(research_mod, "_assess_url_risk", assess)
        monkeypatch.setattr(research_mod, "_scrape_source", scrape)
        out = asyncio.run(_screen_sources(
            FakeRequest(), [{"url": "https://evil.test/"}], "cid"))
        assert out == []
        assert fetched == ["https://evil.test/"]


# ---------------------------------------------------------------------------
# _scrape_sources
# ---------------------------------------------------------------------------

class TestScrapeSources:
    def test_joins_the_text_of_every_source(self, monkeypatch):
        async def scrape(url):
            return f"content of {url}"

        monkeypatch.setattr(research_mod, "_scrape_source", scrape)
        monkeypatch.setattr(research_mod, "is_cancelled", lambda t: False)
        out = asyncio.run(_scrape_sources(
            FakeRequest(), [{"url": "https://a/"}, {"url": "https://b/"}], "cid", "t"))
        assert out == "content of https://a/\n\ncontent of https://b/"

    def test_one_failing_source_does_not_lose_the_others(self, monkeypatch):
        async def scrape(url):
            if "bad" in url:
                raise RuntimeError("scrape exploded")
            return f"ok {url}"

        monkeypatch.setattr(research_mod, "_scrape_source", scrape)
        monkeypatch.setattr(research_mod, "is_cancelled", lambda t: False)
        out = asyncio.run(_scrape_sources(
            FakeRequest(),
            [{"url": "https://bad/"}, {"url": "https://good/"}], "cid", "t"))
        assert out == "ok https://good/"

    def test_cancellation_returns_none(self, monkeypatch):
        monkeypatch.setattr(research_mod, "is_cancelled", lambda t: True)
        assert asyncio.run(_scrape_sources(
            FakeRequest(), [{"url": "https://a/"}], "cid", "t")) is None


# ---------------------------------------------------------------------------
# _interrupted
# ---------------------------------------------------------------------------

class TestInterrupted:
    def test_broadcasts_cancelled(self, monkeypatch):
        seen: list[dict] = []

        async def broadcast(client_id, task_id, status=None, result_preview=None):
            seen.append({"client_id": client_id, "task_id": task_id,
                         "status": status})

        monkeypatch.setattr(research_mod, "broadcast_task_end", broadcast)
        asyncio.run(_interrupted("cid", "task-1"))
        assert seen == [{"client_id": "cid", "task_id": "task-1",
                         "status": "cancelled"}]

    def test_returns_a_fresh_dict_each_time(self, monkeypatch):
        """A shared constant would be mutated by the first caller that edited it."""
        async def broadcast(*a, **k):
            return None

        monkeypatch.setattr(research_mod, "broadcast_task_end", broadcast)
        first = asyncio.run(_interrupted("cid", "t"))
        first["answer"] = "mutated"
        second = asyncio.run(_interrupted("cid", "t"))
        assert second["answer"] == "[INTERRUPTED]"
        assert INTERRUPTED_RESPONSE["answer"] == "[INTERRUPTED]"

    def test_shape_matches_the_empty_result_contract(self, monkeypatch):
        async def broadcast(*a, **k):
            return None

        monkeypatch.setattr(research_mod, "broadcast_task_end", broadcast)
        body = asyncio.run(_interrupted("cid", "t"))
        assert set(body) == {"answer", "context", "sources", "doc_count"}
        assert body["sources"] == [] and body["doc_count"] == 0


# ---------------------------------------------------------------------------
# _index_sources
# ---------------------------------------------------------------------------

class TestIndexSources:
    def test_a_failing_row_does_not_stop_the_rest(self, monkeypatch):
        written: list[tuple] = []
        calls = {"n": 0}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("db locked")
                written.append(params)

        monkeypatch.setattr(research_mod, "get_db_cursor", FakeCursor)
        _index_sources([{"url": "https://a/", "content": "x"},
                        {"url": "https://b/", "content": "y"}])
        assert written == [("y", "https://b/")]