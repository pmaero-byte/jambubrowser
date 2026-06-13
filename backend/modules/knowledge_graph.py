"""
Knowledge Graph Engine
=======================
Extracts entities, their relationships, and builds a semantic
knowledge graph from research documents. Powers the 3D Brain Graph
visualization and enables structured querying of the knowledge vault.

Features:
- Named entity extraction (people, orgs, technologies, concepts)
- Relationship inference between entities
- Graph storage and querying
- Relevance scoring and ranking
- Topic clustering
"""

import logging
import re
import json
import hashlib

log = logging.getLogger("jambu.knowledge_graph")
import time
from typing import Optional, List, Dict, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from backend.core.database import get_db_cursor


@dataclass
class Entity:
    """A knowledge graph entity."""
    id: str
    name: str
    entity_type: str  # person, org, technology, concept, location, event, product
    aliases: List[str] = field(default_factory=list)
    occurrences: int = 1
    confidence: float = 1.0
    sources: List[str] = field(default_factory=list)
    first_seen: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class Relation:
    """A relationship between two entities."""
    source_id: str
    target_id: str
    relation_type: str  # uses, created_by, part_of, related_to, competes_with, depends_on
    weight: float = 1.0
    evidence: str = ""
    sources: List[str] = field(default_factory=list)


class EntityExtractor:
    """
    Extracts named entities and their relationships from text using
    regex patterns and heuristic rules.
    """

    # Common entity patterns
    ENTITY_PATTERNS = {
        'person': re.compile(
            r'\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b'
        ),
        'org': re.compile(
            r'\b(?:[A-Z][a-z]*\s)*(?:Inc\.?|Corp\.?|LLC|Ltd\.?|'
            r'University|Institute|Laboratory|Foundation|'
            r'Google|Microsoft|Apple|Amazon|Meta|OpenAI|Anthropic|'
            r'NVIDIA|AMD|Intel|Tesla|SpaceX)\b'
        ),
        'technology': re.compile(
            r'\b(?:AI|ML|LLM|GPU|CPU|API|SDK|REST|GraphQL|Docker|'
            r'Kubernetes|React|Python|Rust|TypeScript|PyTorch|'
            r'TensorFlow|Transformer|GPT|BERT|LSTM|CNN|RNN|'
            r'blockchain|quantum|neural network|deep learning|'
            r'machine learning|artificial intelligence)\b', re.I
        ),
        'concept': re.compile(
            r'\b(?:algorithm|architecture|protocol|framework|'
            r'paradigm|methodology|approach|technique|'
            r'semantic search|vector database|embedding|'
            r'RAG|fine-tuning|transfer learning|attention mechanism)\b', re.I
        ),
    }

    # Relationship indicators
    RELATION_PATTERNS = {
        'uses': re.compile(
            r'(?:uses?|utilizes?|leverages?|employs?|powered by|built with|'
            r'runs? on|based on|implemented in)\s+([^.]+?)(?:\.|,|;|and|or|\n)',
            re.I
        ),
        'created_by': re.compile(
            r'(?:created by|developed by|authored by|written by|founded by|'
            r'built by|designed by|made by)\s+([^.]+?)(?:\.|,|;|and|or|\n)',
            re.I
        ),
        'part_of': re.compile(
            r'(?:part of|belongs to|component of|module of|'
            r'subsystem of|included in)\s+([^.]+?)(?:\.|,|;|\n)',
            re.I
        ),
        'competes_with': re.compile(
            r'(?:competes? with|rivals?|alternatives? to|'
            r'vs\.?|versus|compared to)\s+([^.]+?)(?:\.|,|;|\n)',
            re.I
        ),
    }

    def extract_entities(self, text: str, source_url: str = "") -> List[Entity]:
        """Extract all entities from text."""
        entities = []
        seen = set()

        for entity_type, pattern in self.ENTITY_PATTERNS.items():
            for match in pattern.finditer(text):
                name = match.group(0).strip()
                normalized = name.lower()

                if normalized in seen:
                    continue
                seen.add(normalized)

                entity_id = hashlib.md5(
                    f"{entity_type}:{normalized}".encode()
                ).hexdigest()[:16]

                entities.append(Entity(
                    id=entity_id,
                    name=name,
                    entity_type=entity_type,
                    sources=[source_url] if source_url else [],
                ))

        # Deduplicate and merge similar entities
        return self._merge_entities(entities)

    def extract_relations(self, text: str, entities: List[Entity],
                           source_url: str = "") -> List[Relation]:
        """Extract relationships between known entities."""
        relations = []
        entity_names = {e.name.lower(): e for e in entities}

        for rel_type, pattern in self.RELATION_PATTERNS.items():
            for match in pattern.finditer(text):
                context = match.group(0)
                target_text = match.group(1).strip()

                # Find the source entity (entity before the relation phrase)
                prefix = text[max(0, match.start() - 100):match.start()]
                source_entity = self._find_entity_in_text(prefix, entity_names)

                # Find the target entity
                target_entity = self._find_entity_in_text(target_text, entity_names)

                if source_entity and target_entity:
                    relations.append(Relation(
                        source_id=source_entity.id,
                        target_id=target_entity.id,
                        relation_type=rel_type,
                        evidence=context[:200],
                        sources=[source_url] if source_url else [],
                    ))

        return relations

    def _find_entity_in_text(self, text: str,
                              entity_map: Dict[str, Entity]) -> Optional[Entity]:
        """Find a known entity mentioned in text."""
        text_lower = text.lower()
        for name_lower, entity in sorted(
            entity_map.items(), key=lambda x: -len(x[0])
        ):
            if name_lower in text_lower:
                return entity
        return None

    def _merge_entities(self, entities: List[Entity]) -> List[Entity]:
        """Merge duplicate entities with similar names."""
        merged = []
        seen_names = {}

        for entity in sorted(entities, key=lambda e: -len(e.name)):
            normalized = entity.name.lower().strip()

            # Check if this is a substring of an existing entity
            is_duplicate = False
            for existing in merged:
                if normalized in existing.name.lower():
                    existing.occurrences += entity.occurrences
                    existing.sources.extend(entity.sources)
                    is_duplicate = True
                    break
                if existing.name.lower() in normalized:
                    entity.occurrences += existing.occurrences
                    entity.sources.extend(existing.sources)
                    merged.remove(existing)
                    break

            if not is_duplicate and normalized not in seen_names:
                seen_names[normalized] = entity
                merged.append(entity)

        return merged


class KnowledgeGraph:
    """
    Manages the knowledge graph: stores entities, relations,
    provides querying and visualization data.
    """

    def __init__(self):
        self._extractor = EntityExtractor()
        self._entity_index: Dict[str, Entity] = {}
        self._relations: List[Relation] = []

    def ingest_document(self, text: str, url: str = "") -> Dict:
        """
        Process a document: extract entities and relations,
        add them to the knowledge graph.

        Returns summary of what was extracted.
        """
        entities = self._extractor.extract_entities(text, url)
        relations = self._extractor.extract_relations(text, entities, url)

        new_entities = 0
        for entity in entities:
            if entity.id not in self._entity_index:
                self._entity_index[entity.id] = entity
                new_entities += 1
            else:
                existing = self._entity_index[entity.id]
                existing.occurrences += 1
                if url not in existing.sources:
                    existing.sources.append(url)

        new_relations = 0
        for rel in relations:
            if not any(
                r.source_id == rel.source_id and r.target_id == rel.target_id
                for r in self._relations
            ):
                self._relations.append(rel)
                new_relations += 1

        # Also store in documents table for /research endpoint
        try:
            from backend.core.database import get_db_cursor
            from backend.core.vector_search import store_embedding
            
            # First insert the document
            doc_id = None
            with get_db_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO documents (url, text) VALUES (?, ?)",
                    (url, text[:5000])
                )
                doc_id = cursor.lastrowid
            
            # Then generate and store embedding in a separate connection
            if doc_id:
                try:
                    from sentence_transformers import SentenceTransformer
                    import numpy as np
                    
                    model = SentenceTransformer("all-MiniLM-L6-v2")
                    embedding = model.encode(text).astype(np.float32).tobytes()
                    store_embedding(doc_id, embedding)
                except Exception as e:
                    log.warning("Could not generate embedding: %s", e)
        except Exception as e:
            log.warning("Could not insert into documents table: %s", e)

        return {
            'entities_extracted': len(entities),
            'new_entities': new_entities,
            'total_entities': len(self._entity_index),
            'relations_extracted': len(relations),
            'new_relations': new_relations,
            'total_relations': len(self._relations),
        }

    def get_graph_data(self, max_nodes: int = 100) -> dict:
        """Generate node/edge data for 3D visualization."""
        sorted_entities = sorted(
            self._entity_index.values(),
            key=lambda e: e.occurrences,
            reverse=True,
        )[:max_nodes]

        nodes = [
            {
                'id': e.id,
                'label': e.name,
                'type': e.entity_type,
                'val': min(e.occurrences, 20),
                'sources': e.sources[:5],
                'confidence': e.confidence,
            }
            for e in sorted_entities
        ]

        entity_ids = {e.id for e in sorted_entities}
        edges = []
        for rel in self._relations:
            if rel.source_id in entity_ids and rel.target_id in entity_ids:
                edges.append({
                    'source': rel.source_id,
                    'target': rel.target_id,
                    'type': rel.relation_type,
                    'weight': rel.weight,
                    'evidence': rel.evidence[:100],
                })

        return {
            'nodes': nodes,
            'edges': edges,
            'total_entities': len(self._entity_index),
            'total_relations': len(self._relations),
        }

    def search_entities(self, query: str, limit: int = 20) -> List[dict]:
        """Search for entities matching a query."""
        query_lower = query.lower()
        results = []

        for entity in self._entity_index.values():
            if query_lower in entity.name.lower():
                results.append({
                    'id': entity.id,
                    'name': entity.name,
                    'type': entity.entity_type,
                    'occurrences': entity.occurrences,
                    'sources': entity.sources[:3],
                })
            if len(results) >= limit:
                break

        return results

    def get_entity_relations(self, entity_id: str) -> dict:
        """Get all relations for a specific entity."""
        if entity_id not in self._entity_index:
            return {'error': 'Entity not found'}

        entity = self._entity_index[entity_id]
        connections = []

        for rel in self._relations:
            if rel.source_id == entity_id:
                target = self._entity_index.get(rel.target_id)
                if target:
                    connections.append({
                        'direction': 'outgoing',
                        'entity': target.name,
                        'entity_type': target.entity_type,
                        'relation': rel.relation_type,
                        'evidence': rel.evidence[:200],
                    })
            elif rel.target_id == entity_id:
                source = self._entity_index.get(rel.source_id)
                if source:
                    connections.append({
                        'direction': 'incoming',
                        'entity': source.name,
                        'entity_type': source.entity_type,
                        'relation': rel.relation_type,
                        'evidence': rel.evidence[:200],
                    })

        return {
            'entity': {
                'id': entity.id,
                'name': entity.name,
                'type': entity.entity_type,
                'occurrences': entity.occurrences,
            },
            'connections': connections,
            'connection_count': len(connections),
        }

    def get_topic_clusters(self, max_clusters: int = 10) -> List[dict]:
        """Group entities into topic clusters based on co-occurrence."""
        clusters = defaultdict(list)

        for rel in self._relations:
            source = self._entity_index.get(rel.source_id)
            if source:
                clusters[source.entity_type].append(source.name)

        result = []
        for entity_type, names in sorted(
            clusters.items(), key=lambda x: -len(x[1])
        )[:max_clusters]:
            # Get unique names sorted by frequency
            name_counts = defaultdict(int)
            for name in names:
                name_counts[name] += 1
            top_names = sorted(name_counts, key=name_counts.get, reverse=True)[:10]

            result.append({
                'type': entity_type,
                'count': len(set(names)),
                'top_entities': top_names,
            })

        return result

    def get_stats(self) -> dict:
        """Get knowledge graph statistics."""
        type_counts = defaultdict(int)
        for e in self._entity_index.values():
            type_counts[e.entity_type] += 1

        return {
            'total_entities': len(self._entity_index),
            'total_relations': len(self._relations),
            'entity_types': dict(type_counts),
            'most_connected': sorted(
                [
                    {
                        'name': e.name,
                        'connections': sum(
                            1 for r in self._relations
                            if r.source_id == e.id or r.target_id == e.id
                        ),
                    }
                    for e in self._entity_index.values()
                ],
                key=lambda x: x['connections'],
                reverse=True,
            )[:10],
        }


# Module-level singleton
_graph: Optional[KnowledgeGraph] = None


def get_knowledge_graph() -> KnowledgeGraph:
    global _graph
    if _graph is None:
        _graph = KnowledgeGraph()
    return _graph
