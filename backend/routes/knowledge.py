"""Knowledge graph and graph data endpoints."""
from fastapi import APIRouter

from backend.core.database import get_db_cursor

router = APIRouter(tags=["knowledge"])


@router.get("/graph_data")
async def get_graph_data():
    """Generate node/edge data for 3D brain visualization."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT id, text, url FROM documents ORDER BY id DESC LIMIT 50")
        docs = cursor.fetchall()

    nodes = [{"id": d[0], "label": d[1][:30] + "...", "url": d[2], "val": 1} for d in docs]
    edges = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            wi = set(docs[i][1].lower().split())
            wj = set(docs[j][1].lower().split())
            if len(wi & wj) > 5:
                edges.append({"source": docs[i][0], "target": docs[j][0]})

    return {"nodes": nodes, "edges": edges}


# ── Knowledge Graph ──


from pydantic import BaseModel, validator
from typing import Optional

from backend.core.security import is_safe_url


class KnowledgeGraphIngestRequest(BaseModel):
    text: str
    url: str = ""

    @validator("url")
    def validate_url(cls, v):
        if v and not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


@router.post("/knowledge/ingest")
async def knowledge_ingest(req: KnowledgeGraphIngestRequest):
    """Ingest content into the knowledge graph with entity extraction."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    entities = kg.ingest(req.text, source_url=req.url)
    return {"ingested": True, "entities_found": entities}


@router.get("/knowledge/graph")
async def knowledge_graph_data(max_nodes: int = 100):
    """Get knowledge graph visualization data: nodes and edges."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    data = kg.get_graph_data(max_nodes=max_nodes)
    return data


@router.get("/knowledge/search")
async def knowledge_search(query: str, limit: int = 20):
    """Search entities in the knowledge graph."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    results = kg.search(query, limit=limit)
    return {"results": results}


@router.get("/knowledge/entity/{entity_id}")
async def knowledge_entity(entity_id: str):
    """Get a specific entity and its relationships."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    entity = kg.get_entity(entity_id)
    if not entity:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")
    return entity


@router.get("/knowledge/clusters")
async def knowledge_clusters(max_clusters: int = 10):
    """Get topic clusters from the knowledge graph."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    return kg.get_clusters(max_clusters=max_clusters)


@router.get("/knowledge/stats")
async def knowledge_stats():
    """Get knowledge graph statistics."""
    from backend.modules.knowledge_graph import get_knowledge_graph
    kg = get_knowledge_graph()
    return kg.get_stats()
