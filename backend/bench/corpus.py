"""
A small local corpus and its index
===================================

The published agentic-search benchmarks all need the live web and a frontier
judge. That makes them useless for the inner loop of development: too slow,
too expensive, and too noisy to tell whether a retrieval change helped.

So this module provides a **fixed local corpus** with a BM25-style index. It
exists so that a change to query expansion, dedup or ranking produces a
measurable delta in seconds, with no network and no judge.

It is not a substitute for the real benchmarks — it is a *sensitivity check*.
If a change does not move the local number, it will not move a real one.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from backend.modules.retrieval import dedupe, rank, terms

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-\.]*")


@dataclass
class Document:
    id: str
    url: str
    title: str
    content: str
    entities: List[str] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "url": self.url, "title": self.title,
                "content": self.content, "entities": list(self.entities),
                "facts": list(self.facts)}


@dataclass
class LocalIndex:
    """A BM25-ish index over a fixed corpus."""

    documents: List[Document] = field(default_factory=list)
    _df: Dict[str, int] = field(default_factory=dict)
    _avg_len: float = 0.0

    def __post_init__(self) -> None:
        self._reindex()

    # -- building ----------------------------------------------------------

    def _reindex(self) -> None:
        self._df = {}
        total = 0
        for doc in self.documents:
            toks = self._tokenise(doc)
            total += len(toks)
            for term in set(toks):
                self._df[term] = self._df.get(term, 0) + 1
        self._avg_len = (total / len(self.documents)) if self.documents else 0.0

    @staticmethod
    def _tokenise(doc: Document) -> List[str]:
        text = f"{doc.title} {doc.title} {doc.content}"  # title counts twice
        return [w.lower() for w in _WORD_RE.findall(text)]

    # -- querying ----------------------------------------------------------

    def get_document(self, docid: str) -> Optional[dict]:
        """Fetch one document by id — the protocol's second tool."""
        for doc in self.documents:
            if doc.id == str(docid) or doc.url == str(docid):
                return doc.to_dict()
        return None

    def search(self, query: str, limit: int = 10, k1: float = 1.5, b: float = 0.75) -> List[dict]:
        """Return the top ``limit`` documents for a query, as result dicts."""
        query_terms = terms(query)
        if not query_terms:
            return []
        n_docs = max(1, len(self.documents))
        scored: List[tuple] = []
        for doc in self.documents:
            toks = self._tokenise(doc)
            if not toks:
                continue
            length = len(toks)
            counts: Dict[str, int] = {}
            for tok in toks:
                counts[tok] = counts.get(tok, 0) + 1
            score = 0.0
            for term in query_terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / max(1.0, self._avg_len)))
                score += idf * norm
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"id": doc.id, "url": doc.url, "title": doc.title, "content": doc.content,
             "entities": list(doc.entities), "facts": list(doc.facts),
             "score": round(score, 4)}
            for score, doc in scored[:limit]
        ]

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "LocalIndex":
        docs: List[Document] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                docs.append(Document(
                    id=str(row.get("id", row.get("url", ""))),
                    url=row.get("url", ""),
                    title=row.get("title", ""),
                    content=row.get("content", ""),
                    entities=list(row.get("entities", [])),
                    facts=list(row.get("facts", [])),
                ))
        return cls(documents=docs)

    def __len__(self) -> int:
        return len(self.documents)
