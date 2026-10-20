"""
Dense and hybrid retrieval
==========================

Lexical retrieval cannot bridge a vocabulary gap: a question saying "how much
weight" will not retrieve a document saying "handles 40 kg payloads", and a
thesaurus only covers the pairs you thought of. The published evidence says
this is the whole game — a local *embedding* retriever scores 70.1% on
BrowseComp-Plus where BM25 scores 3.86% and live-web Google Search scores
59.9% (arXiv 2508.06600, ACL 2026).

Two dense backends, and one way to combine them:

``LsaEmbedder``
    TF-IDF + truncated SVD over the corpus itself. This is latent semantic
    indexing: it is a *real* dense representation that can connect terms which
    co-occur across the corpus, and it needs nothing but numpy. It is not as
    good as a neural encoder, but it is computable offline, which means the
    dense path can be measured rather than assumed.

``SentenceTransformerEmbedder``
    The neural encoder. The project already depends on
    ``sentence-transformers`` (the RAG store uses ``all-MiniLM-L6-v2``), so
    this adds no new dependency — but it needs the model on disk.

``HybridIndex``
    Fuses the two with reciprocal rank fusion. Hybrid retrieval is what
    actually works in practice: dense retrieval finds paraphrase, lexical
    retrieval pins exact identifiers, and the fusion keeps both.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from backend.modules.retrieval import dedupe, terms

log = logging.getLogger("jambu.semantic")

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-\.]*")


class EmbedderUnavailable(RuntimeError):
    """The embedding backend could not be loaded."""


# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------

class LsaEmbedder:
    """Latent semantic indexing over a fixed corpus.

    Fit on the corpus, then encode queries against the same vocabulary and
    project them into the corpus's latent space. Unseen query terms simply
    contribute nothing, which is a limitation of the method, not a bug.
    """

    def __init__(self, corpus_texts: Sequence[str], *, n_components: int = 64):
        self.corpus_texts = list(corpus_texts)
        self.n_components = n_components
        self.vocab: Dict[str, int] = {}
        self.idf: List[float] = []
        self._components = None  # Vt (k x terms)
        self._fit()

    # -- fitting ------------------------------------------------------------

    def _tokens(self, text: str) -> List[str]:
        return [w.lower() for w in _WORD_RE.findall(text or "")]

    def _fit(self) -> None:
        try:
            import numpy as np
        except ImportError as e:  # pragma: no cover - numpy is a declared dep
            raise EmbedderUnavailable(f"numpy is required for LSA: {e}") from e

        docs = [self._tokens(t) for t in self.corpus_texts]
        for doc in docs:
            for token in doc:
                self.vocab.setdefault(token, len(self.vocab))

        n_docs = max(1, len(docs))
        n_terms = max(1, len(self.vocab))
        matrix = np.zeros((len(docs), n_terms), dtype="float64")

        for row, doc in enumerate(docs):
            counts: Dict[int, int] = {}
            for token in doc:
                idx = self.vocab.get(token)
                if idx is not None:
                    counts[idx] = counts.get(idx, 0) + 1
            for idx, count in counts.items():
                matrix[row, idx] = count

        df = (matrix > 0).sum(axis=0)
        self.idf = [math.log((1 + n_docs) / (1 + int(d))) + 1.0 for d in df]

        # Sub-linear tf, standard idf.
        for row in range(matrix.shape[0]):
            for col in range(matrix.shape[1]):
                if matrix[row, col]:
                    matrix[row, col] = (1.0 + math.log(matrix[row, col])) * self.idf[col]

        k = max(1, min(self.n_components, min(matrix.shape)))
        try:
            _u, _s, vt = np.linalg.svd(matrix, full_matrices=False)
        except np.linalg.LinAlgError:  # pragma: no cover
            vt = np.eye(n_terms)
        self._components = vt[:k]

    # -- encoding -----------------------------------------------------------

    def _project(self, text: str):
        import numpy as np

        vector = np.zeros(len(self.vocab), dtype="float64")
        counts: Dict[int, int] = {}
        for token in self._tokens(text):
            idx = self.vocab.get(token)
            if idx is not None:
                counts[idx] = counts.get(idx, 0) + 1
        for idx, count in counts.items():
            vector[idx] = (1.0 + math.log(count)) * self.idf[idx]
        return vector @ self._components.T

    def encode(self, texts: Sequence[str]):
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype="float64")
        return np.vstack([self._project(t) for t in texts])


class SentenceTransformerEmbedder:
    """The neural encoder, using the project's existing dependency."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise EmbedderUnavailable(
                    "sentence-transformers is not installed; "
                    "install it with: pip install sentence-transformers"
                ) from e
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: Sequence[str]):
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype="float64")
        model = self._load()
        vectors = model.encode(
            list(texts), convert_to_numpy=True, normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype="float64")


# ---------------------------------------------------------------------------
# Semantic index
# ---------------------------------------------------------------------------

@dataclass
class SemanticIndex:
    """Dense retrieval over a document set."""

    documents: List[dict] = field(default_factory=list)
    embedder: Optional[object] = None
    _vectors: object = None
    available: bool = True
    unavailable_reason: str = ""

    def build(self) -> bool:
        """Embed the corpus. Returns False if the backend is unavailable."""
        if self.embedder is None:
            self.available = False
            self.unavailable_reason = "no embedder configured"
            return False
        try:
            texts = [f"{d.get('title', '')} {d.get('content', '')}" for d in self.documents]
            self._vectors = self.embedder.encode(texts)
            self.available = True
            return True
        except EmbedderUnavailable as e:
            self.available = False
            self.unavailable_reason = str(e)
            log.info("semantic index unavailable: %s", e)
            return False
        except Exception as e:  # noqa: BLE001
            self.available = False
            self.unavailable_reason = f"{type(e).__name__}: {e}"
            log.info("semantic index failed to build: %r", e)
            return False

    def search(self, query: str, limit: int = 10) -> List[dict]:
        """Dense retrieval, cosine similarity, most similar first."""
        if not self.available or self._vectors is None or not self.documents:
            return []
        import numpy as np

        q = self.embedder.encode([query])
        if q.size == 0:
            return []
        qv = np.asarray(q[0], dtype="float64")
        norm = np.linalg.norm(qv)
        if norm:
            qv = qv / norm
        matrix = np.asarray(self._vectors, dtype="float64")
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        scores = (matrix @ qv) / norms
        order = np.argsort(-scores)[:limit]
        out = []
        for i in order:
            doc = self.documents[int(i)]
            row = dict(doc)
            row["score"] = round(float(scores[int(i)]), 4)
            out.append(row)
        return out


# ---------------------------------------------------------------------------
# Hybrid
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[dict]],
    *,
    k: int = 60,
    limit: int = 10,
    weights: Optional[Sequence[float]] = None,
) -> List[dict]:
    """Fuse several rankings with reciprocal rank fusion.

    RRF is used rather than score blending because the two rankings are not on
    a comparable scale — BM25 scores and cosine similarities are not — while
    ranks are.

    ``weights`` damps a list's influence. That matters: the local benchmark
    showed that fusing a strong lexical ranking with a *weak* dense ranking at
    equal weight is worse than the lexical ranking alone, because the dense
    side promotes documents the lexical side had already rejected. A weaker
    ranker should contribute less, not equally.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: Dict[str, float] = {}
    rows: Dict[str, dict] = {}
    for index, ranking in enumerate(rankings):
        weight = weights[index] if index < len(weights) else 1.0
        if weight == 0:
            continue
        for position, row in enumerate(ranking or []):
            key = str(row.get("url") or row.get("id") or position)
            scores[key] = scores.get(key, 0.0) + weight / (k + position + 1)
            rows.setdefault(key, row)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    fused = []
    for key, score in ordered[:limit]:
        row = dict(rows[key])
        row["score"] = round(score, 6)
        fused.append(row)
    return fused


@dataclass
class HybridIndex:
    """Lexical + dense retrieval, fused.

    ``dense_weight`` damps the dense side. The default is deliberately below
    1.0: measured on the local suite, equal-weight fusion with a weak dense
    backend scored *worse* than lexical alone (0.5417 vs 0.7292), because the
    dense side was promoting documents lexical had rejected. Fuse a strong
    ranker with a weak one and the weak one must not have equal say.
    """

    lexical: object
    semantic: SemanticIndex
    k: int = 60
    dense_weight: float = 0.5

    @property
    def available(self) -> bool:
        return bool(getattr(self.semantic, "available", False))

    def search(self, query: str, limit: int = 10) -> List[dict]:
        lexical_rows = self.lexical.search(query, limit=limit) if self.lexical else []
        semantic_rows = self.semantic.search(query, limit=limit) if self.available else []
        if not semantic_rows:
            return lexical_rows
        return reciprocal_rank_fusion(
            [lexical_rows, semantic_rows], k=self.k, limit=limit,
            weights=[1.0, self.dense_weight],
        )


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def build_embedder(kind: str, corpus_texts: Sequence[str], *, model_name: str = "all-MiniLM-L6-v2"):
    """Create an embedder by name. Raises EmbedderUnavailable for a missing model."""
    if kind == "lsa":
        return LsaEmbedder(corpus_texts)
    if kind in ("sentence-transformers", "st"):
        return SentenceTransformerEmbedder(model_name)
    raise ValueError(f"unknown embedder {kind!r} (use 'lsa' or 'sentence-transformers')")


def build_index(
    documents: Sequence[dict],
    *,
    retriever: str = "lexical",
    embedder_kind: str = "lsa",
    model_name: str = "all-MiniLM-L6-v2",
):
    """Build a retrieval index of the requested kind.

    ``retriever`` is one of ``lexical``, ``dense`` or ``hybrid``. A dense or
    hybrid index whose embedder is unavailable returns an index that reports
    ``available == False`` rather than raising, so callers can fall back.
    """
    from backend.bench.corpus import LocalIndex

    docs = list(documents)
    lexical = LocalIndex(documents=[
        _as_document(d) for d in docs
    ])

    if retriever == "lexical":
        return lexical

    texts = [f"{d.get('title', '')} {d.get('content', '')}" for d in docs]
    try:
        embedder = build_embedder(embedder_kind, texts, model_name=model_name)
    except EmbedderUnavailable as e:
        index = SemanticIndex(documents=docs, available=False, unavailable_reason=str(e))
        return index if retriever == "dense" else HybridIndex(lexical=lexical, semantic=index)

    semantic = SemanticIndex(documents=docs, embedder=embedder)
    semantic.build()
    if retriever == "dense":
        return semantic
    if retriever == "hybrid":
        return HybridIndex(lexical=lexical, semantic=semantic)
    raise ValueError(f"unknown retriever {retriever!r} (use 'lexical', 'dense' or 'hybrid')")


def _as_document(row: dict):
    from backend.bench.corpus import Document

    return Document(
        id=str(row.get("id", row.get("url", ""))),
        url=row.get("url", ""),
        title=row.get("title", ""),
        content=row.get("content", ""),
        entities=list(row.get("entities", [])),
        facts=list(row.get("facts", [])),
    )
