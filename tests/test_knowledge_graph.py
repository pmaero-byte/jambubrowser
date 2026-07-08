"""Tests: backend/modules/knowledge_graph.py — search_entities + endpoint."""
import pytest


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setenv("JAMBU_DB_PATH", ":memory:")
    monkeypatch.setenv("JAMBU_LLM_PROVIDER", "mock")
    from backend.modules.knowledge_graph import KnowledgeGraph
    yield


def _make_entity(eid, name, **kwargs):
    from backend.modules.knowledge_graph import Entity
    kwargs.setdefault("entity_type", "test")
    return Entity(id=eid, name=name, **kwargs)


class TestSearchEntities:
    def test_matches_name_case_insensitive(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg._entity_index["a"] = _make_entity("a", "FastAPI")
        kg._entity_index["b"] = _make_entity("b", "Django")
        results = kg.search_entities("fastapi")
        assert len(results) == 1
        assert results[0]["name"] == "FastAPI"
        assert results[0]["match_type"] == "name"

    def test_matches_alias(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        e = _make_entity("a", "DjangoORM", aliases=["py", "python3"])
        kg._entity_index["a"] = e
        results = kg.search_entities("py")
        assert len(results) == 1
        assert results[0]["name"] == "DjangoORM"
        assert results[0]["match_type"] == "alias"

    def test_matches_metadata(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        e = _make_entity("a", "WebFramework", metadata={"description": "high-performance async framework"})
        kg._entity_index["a"] = e
        results = kg.search_entities("async")
        assert len(results) == 1
        assert results[0]["match_type"] == "metadata"

    def test_name_match_takes_priority_over_alias(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg._entity_index["a"] = _make_entity("a", "Python")
        kg._entity_index["b"] = _make_entity("b", "PyPy", aliases=["python"])
        results = kg.search_entities("python")
        assert results[0]["name"] == "Python"
        assert results[0]["match_type"] == "name"
        assert results[1]["name"] == "PyPy"
        assert results[1]["match_type"] == "alias"

    def test_limit_respected(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        for i in range(5):
            kg._entity_index[f"e{i}"] = _make_entity(f"e{i}", f"Entity{i}")
        results = kg.search_entities("Entity", limit=2)
        assert len(results) == 2

    def test_empty_query_returns_empty(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg._entity_index["a"] = _make_entity("a", "FastAPI")
        assert kg.search_entities("") == []
        assert kg.search_entities("   ") == []

    def test_no_match_returns_empty(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg._entity_index["a"] = _make_entity("a", "FastAPI")
        assert kg.search_entities("django") == []

    def test_invalid_limit_clamped(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg._entity_index["a"] = _make_entity("a", "FastAPI")
        assert len(kg.search_entities("FastAPI", limit=0)) == 1
        assert len(kg.search_entities("FastAPI", limit=200)) == 1

    def test_match_type_field_always_present(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        kg._entity_index["a"] = _make_entity("a", "Test")
        results = kg.search_entities("Test")
        assert "match_type" in results[0]

    def test_results_include_id_name_type_occurrences_sources(self):
        from backend.modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()
        e = _make_entity("xyz", "FastAPI", entity_type="technology", occurrences=5, sources=["https://fastapi.tiangolo.com"])
        kg._entity_index["xyz"] = e
        results = kg.search_entities("FastAPI")
        assert results[0]["id"] == "xyz"
        assert results[0]["name"] == "FastAPI"
        assert results[0]["type"] == "technology"
        assert results[0]["occurrences"] == 5
        assert results[0]["sources"] == ["https://fastapi.tiangolo.com"]


class TestKnowledgeSearchEndpoint:
    def test_search_returns_results(self):
        from backend.modules.knowledge_graph import get_knowledge_graph
        from backend.modules.knowledge_graph import KnowledgeGraph, Entity
        from backend.routes.knowledge import knowledge_search
        kg = get_knowledge_graph()
        kg._entity_index["a"] = Entity(id="a", name="FastAPI", entity_type="technology")
        import asyncio
        result = asyncio.run(knowledge_search(query="fastapi", limit=10))
        assert result["query"] == "fastapi"
        assert result["count"] == 1
        assert result["results"][0]["name"] == "FastAPI"

    def test_search_empty_query(self):
        from backend.routes.knowledge import knowledge_search
        import asyncio
        result = asyncio.run(knowledge_search(query="", limit=10))
        assert result["count"] == 0
        assert result["results"] == []

    def test_search_rejects_invalid_limit(self):
        from backend.routes.knowledge import knowledge_search
        import asyncio
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(knowledge_search(query="x", limit=0))
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            asyncio.run(knowledge_search(query="x", limit=500))
        assert exc.value.status_code == 400
