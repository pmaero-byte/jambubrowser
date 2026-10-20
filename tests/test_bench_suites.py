"""
Public benchmark loaders and retrieval metrics.

The decryption is tested against ciphertext this file generates itself with the
published canary, so the test never depends on the network and never stores
benchmark content.
"""

import base64
import hashlib

import pytest


# ---------------------------------------------------------------------------
# Decryption
# ---------------------------------------------------------------------------

def _encrypt(value: str, canary: str) -> str:
    """The inverse of the benchmark's scheme, so a round trip can be tested."""
    from backend.bench.suites import derive_key

    raw = value.encode("utf-8")
    key = derive_key(canary, len(raw))
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, key))).decode()


class TestDecryption:
    def test_round_trip(self):
        from backend.bench.suites import BCP_CANARY, decrypt_value
        plain = "The Lives of Others"
        assert decrypt_value(_encrypt(plain, BCP_CANARY), BCP_CANARY) == plain

    def test_key_is_repeated_to_length(self):
        from backend.bench.suites import derive_key
        key = derive_key("pw", 100)
        assert len(key) == 100
        assert key[:32] == key[32:64] == hashlib.sha256(b"pw").digest()

    def test_wrong_canary_does_not_silently_succeed(self):
        from backend.bench.suites import decrypt_value
        cipher = _encrypt("secret answer", "right")
        with pytest.raises(Exception):
            decrypt_value(cipher, "wrong")

    def test_decrypt_row_handles_nested_documents(self):
        from backend.bench.suites import BCP_CANARY, decrypt_row
        row = {
            "query_id": "1",
            "query": _encrypt("a question", BCP_CANARY),
            "answer": _encrypt("an answer", BCP_CANARY),
            "gold_docs": [{"docid": "d1", "text": _encrypt("doc text", BCP_CANARY)}],
        }
        out = decrypt_row(row)
        assert out["query"] == "a question"
        assert out["answer"] == "an answer"
        assert out["gold_docs"][0]["text"] == "doc text"
        assert out["gold_docs"][0]["docid"] == "d1"

    def test_plain_values_are_left_alone(self):
        from backend.bench.suites import decrypt_row
        out = decrypt_row({"query_id": "1", "plain": "already readable"})
        assert out["plain"] == "already readable"

    def test_non_string_values_survive(self):
        from backend.bench.suites import decrypt_row
        out = decrypt_row({"n": 5, "f": 1.5, "b": True})
        assert out["n"] == 5 and out["f"] == 1.5 and out["b"] is True


# ---------------------------------------------------------------------------
# Corpus, judgements and tasks
# ---------------------------------------------------------------------------

def _row(qid="1", gold=("g1",), evidence=("e1",), negative=("n1",)):
    return {
        "query_id": qid,
        "query": "a question",
        "answer": "an answer",
        "gold_docs": [{"docid": d, "text": f"gold {d}", "url": f"https://{d}"} for d in gold],
        "evidence_docs": [{"docid": d, "text": f"ev {d}", "url": f"https://{d}"} for d in evidence],
        "negative_docs": [{"docid": d, "text": f"neg {d}", "url": f"https://{d}"} for d in negative],
    }


class TestCorpusAndQrels:
    def test_corpus_collects_every_bucket(self):
        from backend.bench.suites import corpus_from_rows
        corpus = corpus_from_rows([_row()])
        kinds = {d["kind"] for d in corpus}
        assert kinds == {"gold", "evidence", "negative"}

    def test_corpus_dedupes_by_docid(self):
        from backend.bench.suites import corpus_from_rows
        row = _row()
        row["evidence_docs"].append({"docid": "g1", "text": "dup", "url": "u"})
        corpus = corpus_from_rows([row])
        assert len([d for d in corpus if d["id"] == "g1"]) == 1

    def test_gold_wins_over_negative_for_the_same_document(self):
        from backend.bench.suites import corpus_from_rows
        row = _row(gold=("shared",), negative=("shared",))
        corpus = corpus_from_rows([row])
        shared = [d for d in corpus if d["id"] == "shared"]
        assert len(shared) == 1
        assert shared[0]["kind"] == "gold"

    def test_qrels_rank_gold_above_evidence(self):
        from backend.bench.suites import qrels_from_rows
        qrels = qrels_from_rows([_row()])
        assert qrels["1"]["g1"] == 2
        assert qrels["1"]["e1"] == 1

    def test_tasks_use_the_answer_in_text_metric(self):
        from backend.bench.suites import tasks_from_rows
        tasks = tasks_from_rows([_row(qid="42")])
        assert tasks[0]["id"] == "bcp_42"
        assert tasks[0]["metric"] == "answer_in_text"
        assert tasks[0]["expected"] == "an answer"


# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------

class TestRetrievalMetrics:
    def test_recall_at_k(self):
        from backend.bench.suites import recall_at_k
        rel = {"a": 2, "b": 1, "c": 1}
        assert recall_at_k(["a", "x"], rel, k=2) == pytest.approx(1 / 3)

    def test_recall_respects_k(self):
        from backend.bench.suites import recall_at_k
        rel = {"a": 2}
        assert recall_at_k(["x", "y", "a"], rel, k=2) == 0.0
        assert recall_at_k(["x", "y", "a"], rel, k=3) == 1.0

    def test_gold_hit_only_counts_gold(self):
        from backend.bench.suites import gold_hit_at_k
        rel = {"e": 1, "g": 2}
        assert gold_hit_at_k(["e"], rel) == 0.0
        assert gold_hit_at_k(["g"], rel) == 1.0

    def test_ndcg_is_one_for_a_perfect_ranking(self):
        from backend.bench.suites import ndcg_at_k
        rel = {"g": 2, "e": 1}
        assert ndcg_at_k(["g", "e"], rel, k=2) == pytest.approx(1.0)

    def test_ndcg_penalises_a_worse_order(self):
        from backend.bench.suites import ndcg_at_k
        rel = {"g": 2, "e": 1}
        assert ndcg_at_k(["e", "g"], rel, k=2) < 1.0

    def test_evaluate_retrieval_aggregates(self):
        from backend.bench.suites import evaluate_retrieval
        runs = {"1": ["g", "e"], "2": ["x"]}
        qrels = {"1": {"g": 2, "e": 1}, "2": {"g": 2}}
        out = evaluate_retrieval(runs, qrels, k=2)
        assert out["queries"] == 2
        assert out["gold_hit@k"] == pytest.approx(0.5)

    def test_evaluate_retrieval_with_no_runs(self):
        from backend.bench.suites import evaluate_retrieval
        assert evaluate_retrieval({}, {})["queries"] == 0


# ---------------------------------------------------------------------------
# The answer-in-text grader
# ---------------------------------------------------------------------------

class TestAnswerInText:
    def test_finds_the_answer(self):
        from backend.bench.grader import grade_answer_in_text
        g = grade_answer_in_text("the film was The Lives of Others (2006)", "The Lives of Others")
        assert g.score == 1.0

    def test_absent_answer_scores_zero(self):
        from backend.bench.grader import grade_answer_in_text
        assert grade_answer_in_text("something else entirely", "The Lives of Others").score == 0.0

    def test_empty_expected(self):
        from backend.bench.grader import grade_answer_in_text
        assert grade_answer_in_text("anything", "").score == 0.0


# ---------------------------------------------------------------------------
# Cache safety
# ---------------------------------------------------------------------------

class TestCacheSafety:
    def test_cache_dir_refuses_to_point_inside_the_repository(self, monkeypatch):
        """Decrypted benchmark content must never land where it could be
        committed — the benchmark's own canary says so."""
        from backend.bench import suites
        monkeypatch.setenv("JAMBU_BENCH_CACHE", str(suites.Path(__file__).resolve().parents[1] / "cache"))
        with pytest.raises(RuntimeError, match="must not be written"):
            suites.cache_dir()

    def test_cache_dir_is_outside_by_default(self, monkeypatch, tmp_path):
        from backend.bench import suites
        monkeypatch.setenv("JAMBU_BENCH_CACHE", str(tmp_path / "bench"))
        assert suites.cache_dir() == (tmp_path / "bench").resolve()
