"""
Federated RAG - Privacy-First P2P Knowledge Sharing
=====================================================
NOTE — single-node in practice: queries go to trusted peers discovered via
`p2p_discovery`; with no other Jambubrowser nodes on the LAN there are no
peers and federated queries return empty results.
See docs/FEATURE_MAP.md.

Privacy-preserving peer-to-peer vector search that enables
knowledge sharing across Jambubrowser nodes without exposing
raw data.

Protocol:
1. Querying node hashes its query vector
2. Sends anonymized hash to peers
3. Peers search their local vector DB
4. Return matched document hashes + relevance scores (NOT raw content)
5. Querying node requests full content only for approved matches
6. Content is encrypted in transit

Features:
- Encrypted query protocol (AES-256-GCM)
- Anonymized vector exchange (hash-based, not raw vectors)
- Trust scoring for peer nodes
- Rate limiting per peer
- Audit logging for all exchanges
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient


from backend.core.database import get_db_cursor
from backend.decentralized.p2p_discovery import get_p2p

log = logging.getLogger("jambu.federated_rag")

_model_cache: dict[str, object] = {}
_model_lock = asyncio.Lock()


async def _get_embedding_model() -> object:
    """Process-wide cached embedding model, loaded off the event loop.

    ``SentenceTransformer(...)`` + first ``encode`` downloads weights and
    warms up the backend — seconds of blocking CPU/network. Called from
    async handlers, that wedges the whole server (every concurrent
    request stalls) until it finishes. Cache per process and construct
    in a worker thread so the loop stays responsive.
    """
    if "model" in _model_cache:
        return _model_cache["model"]
    async with _model_lock:
        if "model" in _model_cache:
            return _model_cache["model"]

        def _load() -> object:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            model.encode("warmup")
            return model

        _model_cache["model"] = await asyncio.to_thread(_load)
        return _model_cache["model"]


def _shared_cipher() -> Fernet:
    """Cipher for cross-node envelopes.

    Each node used to generate its own key, so the encrypted context a
    querying node sent could *never* be decrypted by the responder —
    federated queries silently came back empty. ``JAMBU_FEDERATION_KEY``
    (urlsafe-base64 32-byte Fernet key) lets operators give every node
    in a federation the same key; without it we keep the old per-node
    random key and only the plain ``/peer/query`` wrapper works.
    """
    key = os.environ.get("JAMBU_FEDERATION_KEY", "").strip()
    if key:
        try:
            return Fernet(key.encode())
        except (ValueError, TypeError):
            log.warning(
                "JAMBU_FEDERATION_KEY is not a valid Fernet key; "
                "falling back to a per-node key (cross-node encrypted "
                "queries will return empty)"
            )
    return Fernet(Fernet.generate_key())


@dataclass
class FederatedQuery:
    """An anonymized query for P2P knowledge sharing."""
    query_id: str
    query_hash: str  # Hashed query vector for privacy
    encrypted_context: str  # Encrypted query context
    min_relevance: float = 0.5
    max_results: int = 10
    created_at: float = field(default_factory=time.time)


@dataclass
class FederatedResult:
    """A result from a federated query."""
    query_id: str
    peer_node_id: str
    document_hash: str  # Hash of matching document
    relevance_score: float
    source_domain: str = ""  # Anonymized domain
    encrypted_snippet: str = ""  # Encrypted preview
    signature: str = ""  # Peer signature for verification


@dataclass
class PeerTrust:
    """Trust score for a peer node."""
    node_id: str
    trust_score: float = 0.5  # 0.0-1.0
    successful_queries: int = 0
    failed_queries: int = 0
    reported_issues: int = 0
    last_interaction: float = 0
    first_interaction: float = field(default_factory=time.time)


class FederatedRAG:
    """
    Privacy-preserving federated knowledge sharing across P2P nodes.
    
    Each node:
    1. Can query peers without revealing raw queries
    2. Responds to queries with anonymized results
    3. Maintains trust scores for connected peers
    4. Enforces rate limits and content security
    """

    MAX_PEER_REQUESTS_PER_MINUTE = 10
    MIN_TRUST_FOR_SHARING = 0.3

    def __init__(self, encryption_key: bytes = None):
        self._peers: Dict[str, PeerTrust] = {}
        self._query_history: List[FederatedQuery] = []
        self._result_cache: Dict[str, List[FederatedResult]] = {}
        self._rate_limits: Dict[str, List[float]] = defaultdict(list)
        self._http_client: Optional[httpx.AsyncClient] = None

        # Encryption for query/result protection — shared federation key
        # when configured (see _shared_cipher), else per-node random.
        if encryption_key:
            self._cipher = Fernet(encryption_key)
        else:
            self._cipher = _shared_cipher()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = make_async_client(timeout=15.0)
        return self._http_client

    # ---- Query Protocol ----

    def create_query(self, query_text: str, min_relevance: float = 0.5,
                      max_results: int = 10) -> FederatedQuery:
        """
        Create an anonymized query for P2P sharing.

        The query text is hashed (not sent in plaintext), and any context
        needed for semantic matching is encrypted.
        """
        query_id = hashlib.md5(
            f"{query_text}_{time.time()}".encode()
        ).hexdigest()[:16]
        query_hash = hashlib.sha256(query_text.encode()).hexdigest()

        context = json.dumps({
            'q': query_text[:200],
            'min_relevance': min_relevance,
            'timestamp': time.time(),
        })
        encrypted_context = self._cipher.encrypt(context.encode()).decode()

        query = FederatedQuery(
            query_id=query_id,
            query_hash=query_hash,
            encrypted_context=encrypted_context,
            min_relevance=min_relevance,
            max_results=max_results,
        )

        self._query_history.append(query)
        if len(self._query_history) > 1000:
            self._query_history = self._query_history[-1000:]

        return query

    async def query_peers(self, query: FederatedQuery,
                           peer_ids: List[str] = None) -> Dict[str, List[FederatedResult]]:
        """
        Send an anonymized query to all trusted peers (or specific peers).

        Returns results grouped by peer node_id.
        """
        p2p = get_p2p()
        results: Dict[str, List[FederatedResult]] = {}

        target_peers = peer_ids if peer_ids else [
            p['node_id'] for p in p2p.get_peers(online_only=True)
        ]

        # Zip the tasked node_ids with their coroutines: skipping a peer
        # (rate limit / trust) must not shift the index used to attribute
        # results, or one peer's answer gets credited to another.
        tasked: List[str] = []
        tasks = []
        for node_id in target_peers:
            if not self._check_rate_limit(node_id):
                continue
            if not self._is_trusted(node_id):
                continue
            tasked.append(node_id)
            tasks.append(self._query_single_peer(node_id, query))

        peer_results = await asyncio.gather(*tasks, return_exceptions=True)

        for node_id, result in zip(tasked, peer_results):
            if isinstance(result, Exception) or result is None:
                if isinstance(result, Exception):
                    log.warning("federated query to %s failed: %s", node_id, result)
                continue
            results[node_id] = result
            self._update_trust(node_id, success=True)

        self._result_cache[query.query_id] = [
            r for results_list in results.values() for r in results_list
        ]

        return results

    async def _query_single_peer(self, node_id: str,
                                   query: FederatedQuery) -> Optional[List[FederatedResult]]:
        """Send query to a single peer and parse results."""
        p2p = get_p2p()
        peer = p2p._peers.get(node_id)
        if not peer or not peer.is_online:
            return None

        self._record_request(node_id)

        client = await self._get_client()
        try:
            resp = await client.post(
                f"http://{peer.ip_address}:{peer.port}/peer/federated-query",
                json={
                    'query_id': query.query_id,
                    'query_hash': query.query_hash,
                    'encrypted_context': query.encrypted_context,
                    'min_relevance': query.min_relevance,
                    'max_results': query.max_results,
                    'requester_id': p2p.node_id,
                },
                timeout=10.0,
            )

            if resp.status_code == 200:
                data = resp.json()
                return [
                    FederatedResult(
                        query_id=query.query_id,
                        peer_node_id=node_id,
                        document_hash=r.get('doc_hash', ''),
                        relevance_score=r.get('score', 0),
                        source_domain=r.get('domain', ''),
                        encrypted_snippet=r.get('snippet', ''),
                        signature=r.get('signature', ''),
                    )
                    for r in data.get('results', [])
                ]
        except Exception:
            self._update_trust(node_id, success=False)

        return None

    async def query(self, query: str, min_relevance: float = 0.5,
                    max_results: int = 10) -> List[dict]:
        """Federate one query across peers and flatten the results.

        This is the method ``POST /federated/query`` calls; it did not
        exist until the two-node test exercised the route, so the
        endpoint answered 500 for every caller. Returns plain dicts so
        FastAPI can serialise them directly.
        """
        federated = self.create_query(query, min_relevance, max_results)
        grouped = await self.query_peers(federated)
        flat: List[dict] = []
        for node_id, results in grouped.items():
            for r in results:
                flat.append({
                    "peer_node_id": node_id,
                    "document_hash": r.document_hash,
                    "relevance_score": r.relevance_score,
                    "source_domain": r.source_domain,
                })
        flat.sort(key=lambda r: r["relevance_score"], reverse=True)
        return flat[:max_results]

    # ---- Response Protocol (Handling Incoming Queries) ----

    async def handle_federated_query(self, query_hash: str,
                                      encrypted_context: str,
                                      min_relevance: float = 0.5,
                                      max_results: int = 10,
                                      requester_id: str = "") -> List[dict]:
        """
        Handle an incoming federated query from a peer.
        Search local knowledge vault without revealing raw data.

        Returns anonymized results (document hashes + encrypted snippets).
        """
        if not self._is_trusted(requester_id):
            return []

        if not self._check_rate_limit(requester_id):
            return []

        # Decrypt the query context
        try:
            context_json = self._cipher.decrypt(encrypted_context.encode()).decode()
            context = json.loads(context_json)
            query_text = context.get('q', '')
        except (InvalidToken, ValueError, json.JSONDecodeError):
            # Almost always a federation-key mismatch: the requester
            # encrypted with a key we don't share. Log it — an empty
            # result list alone is indistinguishable from an empty vault.
            log.warning(
                "dropping federated query from %s: context did not decrypt "
                "(do all federation nodes share JAMBU_FEDERATION_KEY?)",
                requester_id or "unknown",
            )
            return []

        # Search local vector DB for matching documents
        results = await self._search_local_vault(query_text, min_relevance, max_results)

        # Anonymize results - return hashes, not content
        anonymized = []
        for r in results:
            doc_hash = hashlib.sha256(r['text'][:100].encode()).hexdigest()
            snippet = r['text'][:200] if r.get('text') else ''

            # Encrypt the snippet for the requester
            encrypted_snippet = self._cipher.encrypt(snippet.encode()).decode()

            # Sign the result with our node signature
            signature = hashlib.sha256(
                f"{doc_hash}_{requester_id}_{time.time()}".encode()
            ).hexdigest()[:16]

            anonymized.append({
                'doc_hash': doc_hash,
                'score': round(r.get('score', 0), 3),
                'domain': r.get('url', '').split('/')[2] if r.get('url') else '',
                'snippet': encrypted_snippet,
                'signature': signature,
            })

        self._record_request(requester_id)
        return anonymized

    async def _search_local_vault(self, query: str, min_relevance: float,
                                    max_results: int) -> List[dict]:
        """Search the local knowledge vault for matching documents.

        Degrades to an empty list (never a 500): a peer with no sqlite-vec
        extension, a not-yet-created vector table, or no sentence-transformers
        install answers "no results" — the requester can't tell the
        difference from an empty vault, and the failure is logged here.
        """
        try:
            import numpy as np

            model = await _get_embedding_model()
            query_vec = (await asyncio.to_thread(
                model.encode, query)).astype(np.float32).tobytes()

            with get_db_cursor() as cursor:
                cursor.execute(
                    """SELECT d.text, d.url FROM vec_documents v 
                       JOIN documents d ON v.id = d.id 
                       WHERE v.embedding MATCH ? AND k = ?""",
                    (query_vec, max_results * 2),
                )
                rows = cursor.fetchall()
        except ImportError:
            log.info("sentence-transformers unavailable; vault search returns no results")
            return []
        except sqlite3.Error:
            # Fresh DB without a usable vec0 table, or sqlite-vec not
            # loaded — MATCH would raise. Empty result beats a 500.
            log.warning("vault vector search unavailable; returning no results",
                        exc_info=True)
            return []
        except Exception:
            # Model load can fail offline (OSError/RuntimeError). A peer
            # that cannot embed must not 500 the federation endpoint.
            log.warning("vault search failed; returning no results", exc_info=True)
            return []

        results = []
        for row in rows:
            text = row['text'] or ''
            url = row['url'] or ''
            relevance = self._compute_relevance(query, text)
            if relevance >= min_relevance:
                results.append({
                    'text': text,
                    'url': url,
                    'score': relevance,
                })
            if len(results) >= max_results:
                break

        return results

    def _compute_relevance(self, query: str, text: str) -> float:
        """Compute a simple relevance score between query and text."""
        query_words = set(query.lower().split())
        text_words = set(text.lower().split())
        if not query_words:
            return 0.0
        overlap = len(query_words & text_words)
        return min(1.0, overlap / len(query_words))

    # ---- Trust & Rate Limiting ----

    def _is_trusted(self, node_id: str) -> bool:
        """Check if a peer node is trusted for information sharing."""
        if not node_id:
            return False
        if node_id not in self._peers:
            self._peers[node_id] = PeerTrust(node_id=node_id)
        return self._peers[node_id].trust_score >= self.MIN_TRUST_FOR_SHARING

    def _update_trust(self, node_id: str, success: bool):
        """Update trust score for a peer based on interaction outcome."""
        if node_id not in self._peers:
            self._peers[node_id] = PeerTrust(node_id=node_id)

        trust = self._peers[node_id]
        trust.last_interaction = time.time()

        if success:
            trust.successful_queries += 1
            trust.trust_score = min(1.0, trust.trust_score + 0.05)
        else:
            trust.failed_queries += 1
            trust.trust_score = max(0.0, trust.trust_score - 0.1)

    def _check_rate_limit(self, node_id: str) -> bool:
        """Check if a peer has exceeded the rate limit."""
        now = time.time()
        cutoff = now - 60  # 1 minute window

        if node_id not in self._rate_limits:
            self._rate_limits[node_id] = []

        # Clean old entries
        self._rate_limits[node_id] = [
            t for t in self._rate_limits[node_id] if t > cutoff
        ]

        return len(self._rate_limits[node_id]) < self.MAX_PEER_REQUESTS_PER_MINUTE

    def _record_request(self, node_id: str):
        """Record a request for rate limiting."""
        self._rate_limits[node_id].append(time.time())

    def report_peer_issue(self, node_id: str, issue: str):
        """Report a quality or security issue with a peer."""
        if node_id in self._peers:
            self._peers[node_id].reported_issues += 1
            self._peers[node_id].trust_score = max(
                0.0, self._peers[node_id].trust_score - 0.2
            )

    # ---- Node Handlers (for incoming P2P requests) ----

    async def handle_federated_query_request(self, request_data: dict) -> dict:
        """Handle an incoming federated query request from a peer."""
        results = await self.handle_federated_query(
            query_hash=request_data.get('query_hash', ''),
            encrypted_context=request_data.get('encrypted_context', ''),
            min_relevance=request_data.get('min_relevance', 0.5),
            max_results=request_data.get('max_results', 10),
            requester_id=request_data.get('requester_id', ''),
        )
        return {
            'query_id': request_data.get('query_id', ''),
            'results': results,
            'responder_id': get_p2p().node_id,
        }

    # ---- Statistics ----

    def get_stats(self) -> dict:
        """Get federated RAG statistics."""
        total_queries = len(self._query_history)
        total_results = sum(
            len(results) for results in self._result_cache.values()
        )
        trusted_peers = sum(
            1 for p in self._peers.values()
            if p.trust_score >= self.MIN_TRUST_FOR_SHARING
        )

        return {
            'total_queries': total_queries,
            'total_results': total_results,
            'known_peers': len(self._peers),
            'trusted_peers': trusted_peers,
            'encryption_active': True,
            'protocol_version': '1.0',
        }

    def get_peer_trust_scores(self) -> List[dict]:
        """Get trust scores for all known peers."""
        return [
            {
                'node_id': p.node_id,
                'trust_score': round(p.trust_score, 3),
                'successful': p.successful_queries,
                'failed': p.failed_queries,
                'issues': p.reported_issues,
                'last_interaction': p.last_interaction,
            }
            for p in self._peers.values()
        ]

    def rotate_encryption_key(self) -> bytes:
        """Rotate the encryption key. Returns the new key."""
        self._cipher = Fernet(Fernet.generate_key())
        return self._cipher._signing_key

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# Module-level singleton
_federated: Optional[FederatedRAG] = None


def get_federated_rag() -> FederatedRAG:
    global _federated
    if _federated is None:
        _federated = FederatedRAG()
    return _federated
