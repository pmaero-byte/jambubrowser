"""
Tests: Phase 4 - Knowledge Graph, P2P, Multimodal
===================================================
Tests for knowledge graph entity extraction, P2P discovery,
and multi-modal input processing.
"""

import pytest
import json
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestKnowledgeGraph:
    """Tests for knowledge graph entity extraction and querying."""

    def test_entity_extraction(self):
        from backend.modules.knowledge_graph import EntityExtractor
        extractor = EntityExtractor()
        text = (
            "Google developed the Transformer architecture for natural language processing. "
            "OpenAI created GPT models. NVIDIA produces GPUs used for AI training. "
            "PyTorch and TensorFlow are popular deep learning frameworks."
        )
        entities = extractor.extract_entities(text)
        assert len(entities) > 0

    def test_relation_extraction(self):
        from backend.modules.knowledge_graph import EntityExtractor
        extractor = EntityExtractor()
        text = (
            "Google uses PyTorch for research. "
            "NVIDIA created the CUDA platform. "
            "OpenAI built GPT using Transformer architecture."
        )
        entities = extractor.extract_entities(text)
        relations = extractor.extract_relations(text, entities)
        assert len(entities) > 0

    def test_knowledge_graph_ingest(self):
        from backend.modules.knowledge_graph import get_knowledge_graph
        graph = get_knowledge_graph()
        result = graph.ingest_document(
            "Apple released the M4 chip built on 3nm process. "
            "It competes with Intel and AMD processors.",
            "https://example.com/tech",
        )
        assert 'entities_extracted' in result
        assert result['entities_extracted'] > 0

    def test_knowledge_graph_search(self):
        from backend.modules.knowledge_graph import get_knowledge_graph
        graph = get_knowledge_graph()
        graph.ingest_document("Microsoft Azure is a cloud computing platform.")
        results = graph.search_entities("Azure")
        assert len(results) > 0
        assert any(r['name'] == 'Microsoft Azure' for r in results)

    def test_knowledge_graph_stats(self):
        from backend.modules.knowledge_graph import get_knowledge_graph
        graph = get_knowledge_graph()
        graph.ingest_document("Python is a programming language. Django is a Python framework.")
        stats = graph.get_stats()
        assert stats['total_entities'] > 0

    def test_topic_clusters(self):
        from backend.modules.knowledge_graph import get_knowledge_graph
        graph = get_knowledge_graph()
        graph.ingest_document("React is a JavaScript library. Vue is also a JavaScript framework.")
        clusters = graph.get_topic_clusters()
        assert isinstance(clusters, list)


class TestKnowledgeGraphEndpoints:
    """Tests for knowledge graph API endpoints."""

    def test_ingest_endpoint(self, client):
        response = client.post("/knowledge/ingest", json={
            "text": "TensorFlow and PyTorch are machine learning frameworks.",
            "url": "https://test.com",
        })
        assert response.status_code == 200
        assert 'entities_extracted' in response.json()

    def test_graph_data_endpoint(self, client):
        response = client.get("/knowledge/graph", params={"max_nodes": 10})
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data

    def test_search_endpoint(self, client):
        client.post("/knowledge/ingest", json={
            "text": "Blockchain technology powers Bitcoin and Ethereum.",
        })
        response = client.get("/knowledge/search", params={"query": "Blockchain"})
        assert response.status_code == 200
        assert "entities" in response.json()

    def test_clusters_endpoint(self, client):
        response = client.get("/knowledge/clusters")
        assert response.status_code == 200
        assert "clusters" in response.json()

    def test_stats_endpoint(self, client):
        response = client.get("/knowledge/stats")
        assert response.status_code == 200
        assert "total_entities" in response.json()


class TestP2PDiscovery:
    """Tests for P2P discovery module."""

    def test_node_info(self):
        from backend.modules.p2p_discovery import get_p2p
        p2p = get_p2p()
        info = p2p.get_node_info()
        assert "node_id" in info
        assert "hostname" in info
        assert "capabilities" in info
        assert "research" in info["capabilities"]

    def test_get_peers_empty(self):
        from backend.modules.p2p_discovery import get_p2p
        p2p = get_p2p()
        peers = p2p.get_peers()
        assert isinstance(peers, list)

    def test_p2p_stats(self):
        from backend.modules.p2p_discovery import get_p2p
        stats = get_p2p().get_stats()
        assert "node_id" in stats
        assert "total_peers" in stats
        assert "online_peers" in stats

    def test_peer_dataclass(self):
        from backend.modules.p2p_discovery import Peer
        peer = Peer(node_id="test1", hostname="test-host",
                     ip_address="192.168.1.100", port=8001,
                     capabilities=["research", "scrape"])
        d = peer.to_dict()
        assert d["node_id"] == "test1"
        assert "research" in d["capabilities"]


class TestP2PEndpoints:
    """Tests for P2P API endpoints."""

    def test_node_info_endpoint(self, client):
        response = client.get("/p2p/info")
        assert response.status_code == 200
        data = response.json()
        assert "node_id" in data

    def test_list_peers_endpoint(self, client):
        response = client.get("/p2p/peers")
        assert response.status_code == 200
        assert "peers" in response.json()

    def test_stats_endpoint(self, client):
        response = client.get("/p2p/stats")
        assert response.status_code == 200
        assert "node_id" in response.json()

    def test_peer_info_handler(self, client):
        response = client.get("/peer/info")
        assert response.status_code == 200
        assert "node_id" in response.json()


class TestMultimodalInput:
    """Tests for multi-modal input processing."""

    def test_text_processor_url_detection(self):
        from backend.modules.multimodal_input import get_processor
        import asyncio
        processor = get_processor()
        result = asyncio.run(processor.process_text_input(
            "Check out https://example.com/article and https://test.com"
        ))
        assert result.input_type == "url"
        assert result.structured_data is not None

    def test_text_processor_code_detection(self):
        from backend.modules.multimodal_input import get_processor
        import asyncio
        processor = get_processor()
        result = asyncio.run(processor.process_text_input(
            "def calculate_sum(a, b):\n    return a + b"
        ))
        assert result.input_type == "text"
        assert "code" in result.summary.lower()

    def test_image_type_detection(self):
        from backend.modules.multimodal_input import get_processor
        processor = get_processor()
        assert processor.is_supported_image("photo.png")
        assert processor.is_supported_image("screenshot.jpg")
        assert not processor.is_supported_image("document.pdf")

    def test_file_type_detection(self):
        from backend.modules.multimodal_input import get_processor
        processor = get_processor()
        assert processor.is_supported_file("data.csv")
        assert processor.is_supported_file("config.json")
        assert not processor.is_supported_file("movie.mp4")


class TestMultimodalEndpoints:
    """Tests for multi-modal API endpoints."""

    def test_text_endpoint(self, client):
        response = client.post("/multimodal/text", json={
            "text": "Visit https://example.com for more info"
        })
        assert response.status_code == 200
        data = response.json()
        assert "input_type" in data
        assert data["input_type"] == "url"

    def test_image_endpoint_invalid(self, client):
        response = client.post("/multimodal/image", json={
            "image_data": "not-valid-base64!!!",
            "filename": "test.png",
        })
        assert response.status_code in (200, 500)

    def test_file_csv_parsing(self):
        from backend.modules.multimodal_input import get_processor
        import asyncio
        processor = get_processor()
        csv_data = "name,age,city\nAlice,30,NYC\nBob,25,SF"
        result = asyncio.run(processor.process_file(
            csv_data.encode(), "data.csv"
        ))
        assert result.structured_data is not None
        assert result.structured_data['row_count'] == 2

    def test_file_json_parsing(self):
        from backend.modules.multimodal_input import get_processor
        import asyncio
        processor = get_processor()
        json_data = '{"key": "value", "list": [1, 2, 3]}'
        result = asyncio.run(processor.process_file(
            json_data.encode(), "config.json"
        ))
        assert result.structured_data is not None
        assert result.structured_data['key'] == 'value'
