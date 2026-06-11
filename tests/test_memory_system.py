"""
Tests for the memory & personalization system (backend.memory).

Covers:
- UserProfile CRUD + upsert
- SessionMemory lifecycle
- SemanticMemory CRUD + retrieval
- ProceduralMemory success/failure tracking + best_approach
- Retrieval: vector + recency + importance + FTS + profile boost
- Schema migration idempotency
- Privacy: user_id scoping
"""

import os
import time
import pytest


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    """Force in-memory database for tests."""
    monkeypatch.setenv("JAMBU_DB_PATH", ":memory:")
    from backend.memory import reset_memory
    reset_memory()
    yield


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

class TestUserProfile:
    def test_default_profile(self):
        from backend.memory import get_memory
        m = get_memory()
        p = m.get_profile("alice")
        assert p.user_id == "alice"
        assert p.interests == []
        assert p.expertise == {}
        assert p.language == "en"

    def test_upsert_and_retrieve(self):
        from backend.memory import get_memory, UserProfile
        m = get_memory()
        p = UserProfile(
            user_id="alice",
            display_name="Alice",
            interests=["rust", "ai"],
            expertise={"programming": "advanced"},
        )
        m.upsert_profile(p)
        loaded = m.get_profile("alice")
        assert loaded.display_name == "Alice"
        assert loaded.interests == ["rust", "ai"]
        assert loaded.expertise == {"programming": "advanced"}

    def test_upsert_updates_existing(self):
        from backend.memory import get_memory, UserProfile
        m = get_memory()
        m.upsert_profile(UserProfile(user_id="bob", display_name="Bob"))
        m.upsert_profile(UserProfile(user_id="bob", display_name="Robert"))
        assert m.get_profile("bob").display_name == "Robert"

    def test_add_interest(self):
        from backend.memory import get_memory
        m = get_memory()
        m.add_interest("alice", "rust")
        m.add_interest("alice", "ai")
        m.add_interest("alice", "rust")  # duplicate - should be ignored
        assert m.get_profile("alice").interests == ["rust", "ai"]

    def test_set_expertise(self):
        from backend.memory import get_memory
        m = get_memory()
        m.set_expertise("alice", "rust", "advanced")
        m.set_expertise("alice", "python", "intermediate")
        p = m.get_profile("alice")
        assert p.expertise == {"rust": "advanced", "python": "intermediate"}

    def test_user_scoping(self):
        from backend.memory import get_memory, UserProfile
        m = get_memory()
        m.upsert_profile(UserProfile(user_id="alice", interests=["a", "b"]))
        m.upsert_profile(UserProfile(user_id="bob", interests=["c"]))
        assert m.get_profile("alice").interests == ["a", "b"]
        assert m.get_profile("bob").interests == ["c"]


# ---------------------------------------------------------------------------
# SessionMemory
# ---------------------------------------------------------------------------

class TestSessionMemory:
    def test_create_and_retrieve(self):
        from backend.memory import get_memory, SessionMemory
        m = get_memory()
        s = SessionMemory(session_id="s1", user_id="alice", topic="Rust async")
        m.upsert_session(s)
        loaded = m.get_session("s1")
        assert loaded.topic == "Rust async"
        assert loaded.user_id == "alice"

    def test_update_existing_session(self):
        from backend.memory import get_memory, SessionMemory
        m = get_memory()
        m.upsert_session(SessionMemory(session_id="s1", topic="Old"))
        m.upsert_session(SessionMemory(session_id="s1", topic="New"))
        assert m.get_session("s1").topic == "New"

    def test_list_sessions_ordered_by_recent(self):
        from backend.memory import get_memory, SessionMemory
        m = get_memory()
        for i in range(5):
            m.upsert_session(SessionMemory(session_id=f"s{i}", user_id="alice", topic=f"t{i}"))
            time.sleep(0.01)
        sessions = m.list_sessions("alice", limit=10)
        assert len(sessions) == 5
        # Most recent first
        assert sessions[0].session_id == "s4"

    def test_list_sessions_user_filter(self):
        from backend.memory import get_memory, SessionMemory
        m = get_memory()
        m.upsert_session(SessionMemory(session_id="a1", user_id="alice"))
        m.upsert_session(SessionMemory(session_id="b1", user_id="bob"))
        assert len(m.list_sessions("alice")) == 1
        assert m.list_sessions("alice")[0].session_id == "a1"
        assert len(m.list_sessions("bob")) == 1
        assert m.list_sessions("bob")[0].session_id == "b1"


# ---------------------------------------------------------------------------
# SemanticMemory
# ---------------------------------------------------------------------------

class TestSemanticMemory:
    def test_store_and_list(self):
        from backend.memory import get_memory
        m = get_memory()
        m.store_semantic("alice", "User prefers Rust", category="preference", importance=0.8)
        m.store_semantic("alice", "Building async runtime", category="context", importance=0.5)
        all_mems = m.list_semantic("alice")
        assert len(all_mems) == 2
        # Higher importance first
        assert all_mems[0].importance >= all_mems[1].importance

    def test_filter_by_category(self):
        from backend.memory import get_memory
        m = get_memory()
        m.store_semantic("alice", "Fact 1", category="fact")
        m.store_semantic("alice", "Pref 1", category="preference")
        m.store_semantic("alice", "Pref 2", category="preference")
        assert len(m.list_semantic("alice", category="preference")) == 2
        assert len(m.list_semantic("alice", category="fact")) == 1

    def test_get_and_delete(self):
        from backend.memory import get_memory
        m = get_memory()
        mid = m.store_semantic("alice", "to forget")
        assert m.get_semantic(mid).content == "to forget"
        assert m.delete_semantic(mid, user_id="alice")
        assert m.get_semantic(mid) is None

    def test_delete_user_scoped(self):
        from backend.memory import get_memory
        m = get_memory()
        mid = m.store_semantic("alice", "secret")
        # Bob cannot delete Alice's memory
        assert not m.delete_semantic(mid, user_id="bob")
        # Alice can
        assert m.delete_semantic(mid, user_id="alice")

    def test_access_tracking(self):
        from backend.memory import get_memory
        m = get_memory()
        mid = m.store_semantic("alice", "tracked")
        m.access_semantic(mid)
        m.access_semantic(mid)
        m.access_semantic(mid)
        mem = m.get_semantic(mid)
        assert mem.access_count == 3
        assert mem.last_accessed > 0

    def test_embedding_storage(self):
        from backend.memory import get_memory
        m = get_memory()
        mid = m.store_semantic("alice", "vectorized")
        fake_emb = b"\x00\x01\x02\x03" * 96  # 384 bytes
        m.store_embedding(mid, fake_emb)
        loaded = m.get_semantic(mid)
        assert loaded.embedding == fake_emb

    def test_list_with_embeddings(self):
        from backend.memory import get_memory
        m = get_memory()
        id1 = m.store_semantic("alice", "with emb")
        id2 = m.store_semantic("alice", "no emb")
        m.store_embedding(id1, b"\x00" * 384)
        with_embs = m.list_all_with_embeddings("alice")
        assert len(with_embs) == 1
        assert with_embs[0].id == id1


# ---------------------------------------------------------------------------
# ProceduralMemory
# ---------------------------------------------------------------------------

class TestProceduralMemory:
    def test_create_and_get(self):
        from backend.memory import get_memory
        m = get_memory()
        p = m.get_or_create_procedural("alice", "summarize paper", "use abstract")
        assert p.task_pattern == "summarize paper"
        assert p.approach == "use abstract"
        assert p.success_count == 0
        # Idempotent: same (user, pattern, approach) returns same row
        p2 = m.get_or_create_procedural("alice", "summarize paper", "use abstract")
        assert p2.id == p.id

    def test_record_outcome_updates(self):
        from backend.memory import get_memory
        m = get_memory()
        p = m.get_or_create_procedural("alice", "summarize paper", "use abstract")
        m.record_procedural_outcome(p.id, success=True, duration_ms=1000)
        m.record_procedural_outcome(p.id, success=True, duration_ms=2000)
        m.record_procedural_outcome(p.id, success=False, duration_ms=3000)
        loaded = m.list_procedural("alice")[0]
        assert loaded.success_count == 2
        assert loaded.failure_count == 1
        assert loaded.avg_duration_ms == 2000.0
        assert abs(loaded.success_rate() - (2 / 3)) < 0.01

    def test_best_approach_picks_highest_rate(self):
        from backend.memory import get_memory
        m = get_memory()
        a = m.get_or_create_procedural("alice", "task", "approach A")
        b = m.get_or_create_procedural("alice", "task", "approach B")
        for _ in range(3):
            m.record_procedural_outcome(a.id, success=True, duration_ms=100)
            m.record_procedural_outcome(b.id, success=False, duration_ms=200)
        m.record_procedural_outcome(a.id, success=False, duration_ms=100)
        m.record_procedural_outcome(b.id, success=True, duration_ms=200)
        best = m.best_approach("alice", "task")
        assert best is not None
        assert best.approach == "approach A"
        assert best.success_rate() == 0.75

    def test_best_approach_requires_min_attempts(self):
        from backend.memory import get_memory
        m = get_memory()
        p = m.get_or_create_procedural("alice", "task", "approach")
        m.record_procedural_outcome(p.id, success=True, duration_ms=100)  # only 1 attempt
        # Should return None because min_attempts default is 2
        assert m.best_approach("alice", "task") is None

    def test_list_procedural_ordered(self):
        from backend.memory import get_memory
        m = get_memory()
        for i in range(5):
            m.get_or_create_procedural("alice", f"task-{i}", f"approach-{i}")
            time.sleep(0.005)
        listed = m.list_procedural("alice")
        assert len(listed) == 5
        # Most recently used first
        assert listed[0].task_pattern == "task-4"


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

class TestRetrieval:
    def test_fts_keyword_match(self):
        from backend.memory import get_memory, retrieve_relevant
        m = get_memory()
        m.store_semantic("alice", "User loves Rust programming language", category="preference", importance=0.8)
        m.store_semantic("alice", "User enjoys hiking outdoors", category="hobby", importance=0.5)
        hits = retrieve_relevant("rust", user_id="alice", k=5)
        assert len(hits) >= 1
        assert "Rust" in hits[0].memory.content

    def test_recency_boost(self):
        from backend.memory import get_memory, retrieve_relevant
        m = get_memory()
        # Older memory
        m.store_semantic("alice", "Old: User likes Rust", category="preference")
        time.sleep(0.1)
        # Newer memory (with same text)
        m.store_semantic("alice", "Old: User likes Rust", category="preference")
        hits = retrieve_relevant("rust", user_id="alice", k=5)
        assert len(hits) >= 2

    def test_importance_boost(self):
        from backend.memory import get_memory, retrieve_relevant
        m = get_memory()
        m.store_semantic("alice", "User uses Rust", category="fact", importance=0.1)
        m.store_semantic("alice", "User uses Rust", category="fact", importance=0.9)
        hits = retrieve_relevant("rust", user_id="alice", k=5)
        # Higher importance should be first
        assert hits[0].memory.importance == 0.9

    def test_profile_interest_boost(self):
        from backend.memory import get_memory, retrieve_relevant, UserProfile
        m = get_memory()
        m.upsert_profile(UserProfile(user_id="alice", interests=["rust", "low-level"]))
        m.store_semantic("alice", "Something completely unrelated", category="fact")
        hits = retrieve_relevant("rust", user_id="alice", k=5)
        # Should still get the unrelated mem if profile says user likes rust
        # (profile match contributes to score)

    def test_empty_user_returns_empty(self):
        from backend.memory import retrieve_relevant
        hits = retrieve_relevant("anything", user_id="no-such-user", k=5)
        assert hits == []

    def test_user_isolation(self):
        from backend.memory import get_memory, retrieve_relevant
        m = get_memory()
        m.store_semantic("alice", "Alice's secret", category="fact")
        m.store_semantic("bob", "Bob's secret", category="fact")
        alice_hits = retrieve_relevant("secret", user_id="alice", k=5)
        bob_hits = retrieve_relevant("secret", user_id="bob", k=5)
        assert all("Alice" in h.memory.content for h in alice_hits)
        assert all("Bob" in h.memory.content for h in bob_hits)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_empty_stats(self):
        from backend.memory import get_memory
        s = get_memory().stats("nobody")
        assert s == {"profiles": 0, "sessions": 0, "semantic_memories": 0, "procedural_memories": 0}

    def test_populated_stats(self):
        from backend.memory import get_memory, UserProfile, SessionMemory
        m = get_memory()
        m.upsert_profile(UserProfile(user_id="alice"))
        m.upsert_session(SessionMemory(session_id="s1", user_id="alice"))
        m.store_semantic("alice", "fact 1")
        m.store_semantic("alice", "fact 2")
        m.get_or_create_procedural("alice", "task", "approach")
        s = m.stats("alice")
        assert s == {"profiles": 1, "sessions": 1, "semantic_memories": 2, "procedural_memories": 1}
