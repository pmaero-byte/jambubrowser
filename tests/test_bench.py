"""
Retrieval, grading and the benchmark runner.

The retrieval layer (query expansion, cross-engine dedup, blended ranking) and
the deterministic graders are what make an agentic-search comparison possible
without a frontier judge model.

See ``docs/SEARCH_BENCHMARK_2026.md`` for the competitive analysis against the
published agentic-search benchmarks.
"""

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------

class TestExpansion:
    def test_original_is_always_first(self):
        """A reformulation must never replace the user's phrasing."""
        from backend.modules.retrieval import expand_query
        q = "How much weight can the gripper lift?"
        assert expand_query(q)[0] == q

    def test_empty_query(self):
        from backend.modules.retrieval import expand_query
        assert expand_query("") == []
        assert expand_query("   ") == []

    def test_quoted_phrase_is_extracted(self):
        from backend.modules.retrieval import expand_query
        variants = expand_query('What is the "Atlas Gripper G2" payload?')
        assert any(v.startswith('"Atlas Gripper G2"') for v in variants)

    def test_synonym_substitution(self):
        """A question saying "weight" must reach a document saying "payload"."""
        from backend.modules.retrieval import expand_query
        variants = expand_query("How much weight can the gripper lift?")
        assert any("payload" in v.lower() for v in variants)

    def test_variants_are_unique(self):
        from backend.modules.retrieval import expand_query
        variants = expand_query("price of the Atlas Robotics plans")
        assert len(variants) == len(set(v.lower() for v in variants))

    def test_max_variants_respected(self):
        from backend.modules.retrieval import expand_query
        assert len(expand_query("price of plans for a robot gripper fleet", max_variants=2)) <= 2

    def test_stopwords_are_dropped_from_terms(self):
        from backend.modules.retrieval import terms
        assert terms("What is the price of the plan?") == ["price", "plan"]


# ---------------------------------------------------------------------------
# Deduplication and ranking
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_same_url_different_tracking_is_one_document(self):
        from backend.modules.retrieval import dedupe
        rows = [
            {"url": "https://a.test/x?utm_source=news", "title": "One"},
            {"url": "https://a.test/x", "title": "One"},
        ]
        assert len(dedupe(rows)) == 1

    def test_same_title_different_url_is_one_document(self):
        """Catches the same page served under a different address."""
        from backend.modules.retrieval import dedupe
        rows = [
            {"url": "https://a.test/x", "title": "Atlas Gripper G2"},
            {"url": "https://mirror.test/x", "title": "Atlas Gripper G2"},
        ]
        assert len(dedupe(rows)) == 1

    def test_distinct_documents_survive(self):
        from backend.modules.retrieval import dedupe
        rows = [
            {"url": "https://a.test/x", "title": "One"},
            {"url": "https://a.test/y", "title": "Two"},
        ]
        assert len(dedupe(rows)) == 2

    def test_non_dict_rows_are_ignored(self):
        from backend.modules.retrieval import dedupe
        assert len(dedupe([{"url": "https://a.test/x", "title": "One"}, "junk"])) == 1


class TestRanking:
    def test_title_match_outweighs_snippet_match(self):
        from backend.modules.retrieval import score_result
        in_title = score_result({"title": "gripper payload", "content": "nothing"}, ["gripper"])
        in_body = score_result({"title": "nothing", "content": "gripper payload"}, ["gripper"])
        assert in_title > in_body

    def test_partial_match_scores_below_full_match(self):
        from backend.modules.retrieval import score_result
        full = score_result({"title": "a b c", "content": ""}, ["a", "b", "c"])
        partial = score_result({"title": "a", "content": ""}, ["a", "b", "c"])
        assert full > partial

    def test_backend_order_is_preserved_over_a_weaker_signal(self):
        """Re-ranking must not be a downgrade of the backend's own ordering."""
        from backend.modules.retrieval import rank
        rows = [
            {"url": "https://a.test/1", "title": "nothing relevant", "content": ""},
            {"url": "https://a.test/2", "title": "nothing relevant", "content": ""},
        ]
        out = rank(rows, "gripper payload")
        assert out[0]["url"] == "https://a.test/1"

    def test_a_strong_match_can_climb(self):
        from backend.modules.retrieval import rank
        rows = [
            {"url": "https://a.test/1", "title": "unrelated", "content": ""},
            {"url": "https://a.test/2", "title": "unrelated", "content": ""},
            {"url": "https://a.test/3", "title": "unrelated", "content": ""},
            {"url": "https://a.test/4", "title": "gripper payload", "content": "gripper payload"},
        ]
        out = rank(rows, "gripper payload")
        assert out[0]["url"] == "https://a.test/4"

    def test_limit_is_applied(self):
        from backend.modules.retrieval import rank
        rows = [{"url": f"https://a.test/{i}", "title": f"document {i}", "content": ""}
                for i in range(20)]
        assert len(rank(rows, "document", limit=5)) == 5


# ---------------------------------------------------------------------------
# Graders
# ---------------------------------------------------------------------------

class TestGraders:
    def test_exact_match(self):
        from backend.bench.grader import grade_exact
        assert grade_exact("40 kg payloads", "40 kg payloads").score == 1.0
        assert grade_exact("something else", "40 kg payloads").score == 0.0

    def test_exact_uses_aliases(self):
        from backend.bench.grader import grade_exact
        assert grade_exact("40kg", "40 kg payloads", ["40kg"]).score == 1.0

    def test_exact_is_case_and_punctuation_insensitive(self):
        from backend.bench.grader import grade_exact
        assert grade_exact("40 KG Payloads!", "40 kg payloads").score == 1.0

    def test_f1_items_counts_each_gold_independently(self):
        """DSQA judges every gold item separately — a response that packs
        several facts into one line must not lose the others."""
        from backend.bench.grader import grade_f1_items
        g = grade_f1_items("Rotterdam and Austin", "Rotterdam; Austin")
        assert g.score == pytest.approx(1.0)

    def test_f1_items_penalises_extra_items(self):
        from backend.bench.grader import grade_f1_items
        g = grade_f1_items("Rotterdam; Austin; Berlin", "Rotterdam; Austin")
        assert 0.0 < g.score < 1.0
        assert g.detail["excessive"] == 1

    def test_entities_counts_passes(self):
        from backend.bench.grader import grade_entities
        g = grade_entities([{"name": "Atlas Gripper G2"}, {"name": "Other"}], ["Atlas Gripper G2"])
        assert g.score == 1.0
        assert g.detail["returned"] == 2

    def test_entities_is_normalised(self):
        from backend.bench.grader import grade_entities
        g = grade_entities([{"name": "A"}, {"name": "B"}], ["A", "B", "C", "D"])
        assert g.normalised == pytest.approx(0.5)

    def test_table_f1(self):
        from backend.bench.grader import grade_table
        gold = [{"name": "G2", "price": "12500"}]
        pred = [{"name": "G2", "price": "12,500"}]
        g = grade_table(pred, gold)
        assert g.score == pytest.approx(1.0)

    def test_table_f1_counts_only_matching_rows(self):
        from backend.bench.grader import grade_table
        gold = [{"name": "G2", "price": "12500"}]
        pred = [{"name": "G1", "price": "6900"}]
        assert grade_table(pred, gold).score == 0.0

    def test_unknown_metric_raises(self):
        from backend.bench.grader import grade
        with pytest.raises(ValueError):
            grade({"metric": "vibes", "expected": "x"}, "x")


# ---------------------------------------------------------------------------
# The corpus and the runner
# ---------------------------------------------------------------------------

class TestCorpus:
    def test_loads_the_local_corpus(self):
        from backend.bench.corpus import LocalIndex
        from backend.bench.runner import local_suite_dir
        index = LocalIndex.load(local_suite_dir() / "corpus.jsonl")
        assert len(index) > 20

    def test_search_ranks_relevant_documents_first(self):
        from backend.bench.corpus import LocalIndex
        from backend.bench.runner import local_suite_dir
        index = LocalIndex.load(local_suite_dir() / "corpus.jsonl")
        hits = index.search("Atlas Gripper G2 payload")
        assert hits and "Gripper" in hits[0]["title"]

    def test_search_with_no_matching_terms_is_empty(self):
        from backend.bench.corpus import LocalIndex
        from backend.bench.runner import local_suite_dir
        index = LocalIndex.load(local_suite_dir() / "corpus.jsonl")
        assert index.search("zzzzqqqqxxxx") == []

    def test_search_respects_the_limit(self):
        from backend.bench.corpus import LocalIndex
        from backend.bench.runner import local_suite_dir
        index = LocalIndex.load(local_suite_dir() / "corpus.jsonl")
        assert len(index.search("atlas robotics", limit=3)) <= 3


class TestRunner:
    def _index(self):
        from backend.bench.corpus import LocalIndex
        from backend.bench.runner import local_suite_dir
        return LocalIndex.load(local_suite_dir() / "corpus.jsonl")

    def test_runs_the_local_suite(self):
        from backend.bench.policies import naive_policy
        from backend.bench.runner import load_local_suite, run_suite
        index = self._index()
        report = run_suite(
            load_local_suite("local"), naive_policy,
            search=lambda q, l: index.search(q, limit=l),
        )
        assert report.n == 16
        assert report.failures == 0

    def test_bootstrap_ci_is_bounded(self):
        from backend.bench.policies import naive_policy
        from backend.bench.runner import load_local_suite, run_suite
        index = self._index()
        report = run_suite(
            load_local_suite("local"), naive_policy,
            search=lambda q, l: index.search(q, limit=l),
        )
        lo, hi = report.bootstrap_ci()
        assert 0.0 <= lo <= hi <= 1.0

    def test_retrieval_beats_naive_on_the_local_suite(self):
        """The benchmark exists to catch this; if it ever stops holding, the
        retrieval work has regressed."""
        from backend.bench.policies import naive_policy, retrieval_policy
        from backend.bench.runner import load_local_suite, run_suite
        index = self._index()
        tasks = load_local_suite("local")
        kwargs = {"search": lambda q, l: index.search(q, limit=l)}
        naive = run_suite(tasks, naive_policy, **kwargs)
        better = run_suite(tasks, retrieval_policy, **kwargs)
        assert better.mean > naive.mean, (
            f"retrieval {better.mean:.4f} did not beat naive {naive.mean:.4f}"
        )

    def test_rounds_are_capped(self):
        from backend.bench.policies import retrieval_policy
        from backend.bench.runner import load_local_suite, run_suite
        index = self._index()
        report = run_suite(
            load_local_suite("local"), retrieval_policy,
            search=lambda q, l: index.search(q, limit=l),
            max_rounds=1,
        )
        assert all(r.rounds_used <= 1 for r in report.results)

    def test_a_broken_task_does_not_stop_the_run(self):
        from backend.bench.policies import naive_policy
        from backend.bench.runner import run_suite, Task

        def boom(query, limit):
            raise RuntimeError("backend down")

        tasks = [Task(id="a", query="x", metric="exact", expected="x")]
        report = run_suite(tasks, naive_policy, search=boom)
        assert report.n == 1
        assert report.failures == 1
        assert report.results[0].error

    def test_report_serialises(self):
        from backend.bench.policies import naive_policy
        from backend.bench.runner import load_local_suite, run_suite
        index = self._index()
        report = run_suite(
            load_local_suite("local"), naive_policy,
            search=lambda q, l: index.search(q, limit=l),
        )
        payload = report.to_dict()
        assert payload["tasks"] == 16
        assert "ci95" in payload and len(payload["ci95"]) == 2
