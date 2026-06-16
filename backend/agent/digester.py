"""
Failure Digester — AEGIS Phase 2: Trace → Failure Clusters
============================================================

The Digester reads agent execution traces (AgentRunResult history) and produces
structured failure clusters ready for the AEGIS Planner. This is the first stage
of the trace-driven harness evolution pipeline.

Algorithm
---------
1. Extract all failed steps from agent run history
2. Group failures by (tool_name, error_category)
3. Embed failure descriptions with sentence-transformers
4. Cluster failures using similarity (or fallback to text-based grouping)
5. Generate human-readable failure pattern summaries via LLM
6. Suggest which harness dimension needs adjustment per cluster

See HarnessX paper §4.3 (AEGIS Architecture) for the theoretical foundation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from backend.agent.harness import EditDimension, HarnessConfig, HarnessEdit

log = logging.getLogger("jambu.agent.digester")


@dataclass
class FailureExample:
    """A single failure example extracted from an agent run."""
    run_id: str
    query: str
    step_index: int
    tool: str
    args: dict
    error: str
    verdict: dict = field(default_factory=dict)
    config_id: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "query": self.query[:200],
            "step_index": self.step_index,
            "tool": self.tool,
            "args": self.args,
            "error": self.error[:500],
            "verdict": self.verdict,
            "config_id": self.config_id,
        }

    @property
    def description(self) -> str:
        return f"[{self.tool}] {self.error[:200]}"


@dataclass
class FailureCluster:
    """A cluster of related failures, ready for the Planner.

    Each cluster represents a recurring failure pattern that the AEGIS
    Planner should address with harness edits.
    """
    cluster_id: str
    failure_pattern: str                      # human-readable summary
    examples: list[dict] = field(default_factory=list)
    count: int = 0
    common_tool: str = ""
    common_error: str = ""
    common_error_category: str = "unknown"
    suggested_dimension: EditDimension = EditDimension.PROMPT
    suggested_fix: str = ""                   # LLM-generated fix suggestion
    avg_confidence: float = 0.0               # avg verifier confidence across examples
    severity: str = "medium"                  # "critical" | "high" | "medium" | "low"

    def __post_init__(self):
        if not self.cluster_id:
            raw = f"{self.common_tool}:{self.common_error_category}:{self.count}"
            self.cluster_id = hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "failure_pattern": self.failure_pattern,
            "examples": self.examples[:10],
            "count": self.count,
            "common_tool": self.common_tool,
            "common_error": self.common_error[:300],
            "common_error_category": self.common_error_category,
            "suggested_dimension": self.suggested_dimension.value,
            "suggested_fix": self.suggested_fix,
            "avg_confidence": self.avg_confidence,
            "severity": self.severity,
        }

    @property
    def summary(self) -> str:
        return (
            f"[{self.severity.upper()}] {self.common_tool} "
            f"({self.common_error_category}): {self.failure_pattern[:150]} "
            f"— {self.count} occurrences"
        )


# ---------------------------------------------------------------------------
# Error categorisation
# ---------------------------------------------------------------------------

def _categorise_error(error: str) -> str:
    """Map an error message to a coarse category."""
    el = error.lower() if error else ""
    if any(w in el for w in ("timeout", "timed out", "deadline exceeded", "too slow")):
        return "timeout"
    if any(w in el for w in ("not found", "404", "does not exist", "no such", "missing")):
        return "not_found"
    if any(w in el for w in ("permission", "denied", "unauthorized", "forbidden", "403", "401")):
        return "permission_denied"
    if any(w in el for w in ("rate limit", "too many", "429", "throttl", "quota")):
        return "rate_limit"
    if any(w in el for w in ("parse", "json", "malformed", "invalid format", "unexpected token", "syntax")):
        return "parse_error"
    if any(w in el for w in ("connection", "refused", "unreachable", "dns", "network", "econnrefused")):
        return "connection_error"
    if any(w in el for w in ("tool", "unknown tool", "not registered", "no tool")):
        return "tool_not_found"
    if any(w in el for w in ("empty", "no result", "no content", "none found")):
        return "empty_result"
    if any(w in el for w in ("memory", "out of memory", "oom")):
        return "resource_exhausted"
    return "unknown"


def _infer_dimension(category: str, tool: str) -> EditDimension:
    """Infer which harness dimension to adjust based on error category."""
    if category in ("parse_error",):
        return EditDimension.PROMPT
    if category in ("tool_not_found",):
        return EditDimension.TOOL
    if category in ("empty_result", "not_found"):
        return EditDimension.MEMORY
    if category in ("timeout", "rate_limit", "resource_exhausted"):
        return EditDimension.CONTROL_FLOW
    if category in ("connection_error", "permission_denied"):
        return EditDimension.LLM_ROUTING
    return EditDimension.PROMPT


def _infer_severity(count: int, total_runs: int) -> str:
    """Infer cluster severity based on frequency."""
    rate = count / max(1, total_runs)
    if rate >= 0.5:
        return "critical"
    if rate >= 0.25:
        return "high"
    if rate >= 0.1:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Embedding helpers (reuse from memory.retrieval)
# ---------------------------------------------------------------------------

def _try_embed_text(text: str) -> Optional[bytes]:
    """Embed text using sentence-transformers if available."""
    try:
        from backend.memory.retrieval import embed_text
        return embed_text(text)
    except Exception:
        return None


def _cosine_distance(a: bytes, b: bytes) -> float:
    """Cosine distance between two float32-packed vectors (1 - similarity)."""
    try:
        n = len(a) // 4
        va = struct.unpack(f"<{n}f", a)
        vb = struct.unpack(f"<{n}f", b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(x * x for x in vb))
        if na == 0 or nb == 0:
            return 1.0
        return 1.0 - (dot / (na * nb))
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# LLM-based pattern extraction
# ---------------------------------------------------------------------------

_PATTERN_PROMPT = """You are a failure analysis module. Given a cluster of similar agent failures, summarise the common failure pattern in 1-2 sentences and suggest a concrete harness fix.

Failures (all share tool="{tool}", error_category="{category}"):
{examples}

Respond with JSON:
{{
  "failure_pattern": "<1-2 sentence summary of the common pattern>",
  "suggested_fix": "<concrete harness change to prevent this failure>"
}}
"""


async def _extract_failure_pattern(
    tool: str,
    category: str,
    examples: list[FailureExample],
) -> tuple[str, str]:
    """Use the LLM to summarise the common failure pattern and suggest a fix."""
    examples_text = "\n".join(
        f"- Run {e.run_id}, Step {e.step_index}: {e.error[:300]}"
        for e in examples[:8]
    )
    if not examples_text:
        return f"Unknown failure pattern for {tool}/{category}", ""

    prompt = _PATTERN_PROMPT.format(
        tool=tool,
        category=category,
        examples=examples_text,
    )
    try:
        from backend.llm import ChatMessage, Role, get_default
        llm = get_default()
        resp = await llm.chat(
            [ChatMessage(role=Role.USER, content=prompt)],
            temperature=0.2,
            max_tokens=300,
        )
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        return (
            data.get("failure_pattern", f"Recurring {tool} failures in category {category}"),
            data.get("suggested_fix", ""),
        )
    except Exception as e:
        log.warning("Failed to extract failure pattern via LLM: %s", e)
        return (
            f"Recurring failures in tool '{tool}' (category: {category})",
            f"Review {tool} configuration and error handling",
        )


# ---------------------------------------------------------------------------
# Digester
# ---------------------------------------------------------------------------

class Digester:
    """Reads agent run history and produces failure clusters.

    This is the first stage of the AEGIS pipeline: traces → clusters.
    The clusters are consumed by the Planner to propose harness edits.

    Usage::

        digester = Digester()
        clusters = await digester.digest(agent.history)
        for c in clusters:
            print(c.summary)
    """

    def __init__(self, similarity_threshold: float = 0.3):
        self.similarity_threshold = similarity_threshold  # max cosine distance for same cluster
        self._last_digest_time: float = 0.0
        self._cluster_count: int = 0

    async def digest(
        self,
        agent_history: list,
        *,
        use_llm: bool = True,
    ) -> list[FailureCluster]:
        """Digest agent run history into failure clusters.

        Args:
            agent_history: List of AgentRunResult objects from Agent.history.
            use_llm: If True, use LLM to summarise failure patterns.

        Returns:
            List of FailureCluster, sorted by count descending.
        """
        if not agent_history:
            return []

        t0 = time.time()

        # Step 1: Extract all failures from history
        examples: list[FailureExample] = []
        total_runs = len(agent_history)
        for result in agent_history:
            if not hasattr(result, "plan"):
                continue
            for step in result.plan.steps:
                if step.status.value == "failed" and step.error:
                    examples.append(FailureExample(
                        run_id=result.run_id,
                        query=result.query,
                        step_index=step.index,
                        tool=step.tool or "unknown",
                        args=step.args,
                        error=step.error,
                        verdict=step.verification or {},
                        config_id=step.verification.get("config_id", "") if step.verification else "",
                    ))

        if not examples:
            log.info("No failures found in %d agent runs", total_runs)
            return []

        log.info("Extracted %d failure examples from %d runs", len(examples), total_runs)

        # Step 2: Group by (tool, error_category)
        groups: dict[tuple[str, str], list[FailureExample]] = {}
        for ex in examples:
            cat = _categorise_error(ex.error)
            key = (ex.tool, cat)
            groups.setdefault(key, []).append(ex)

        # Step 3: Build clusters from groups
        clusters: list[FailureCluster] = []
        for (tool, category), group_examples in sorted(groups.items(), key=lambda x: -len(x[1])):
            cluster = await self._build_cluster(
                tool, category, group_examples, total_runs, use_llm=use_llm,
            )
            clusters.append(cluster)

        # Step 4: Merge similar clusters using embeddings
        clusters = self._merge_similar_clusters(clusters)

        # Sort by count descending
        clusters.sort(key=lambda c: c.count, reverse=True)

        self._last_digest_time = time.time() - t0
        self._cluster_count = len(clusters)
        log.info(
            "Digest complete: %d clusters in %.1fs (%d examples, %d runs)",
            len(clusters), self._last_digest_time, len(examples), total_runs,
        )

        return clusters

    async def _build_cluster(
        self,
        tool: str,
        category: str,
        group_examples: list[FailureExample],
        total_runs: int,
        *,
        use_llm: bool = True,
    ) -> FailureCluster:
        """Build a single FailureCluster from a group of failures."""
        count = len(group_examples)
        avg_conf = sum(
            e.verdict.get("confidence", 0.5) if isinstance(e.verdict, dict) else 0.5
            for e in group_examples
        ) / max(1, count)

        # Most common error message
        error_counter = Counter(e.error[:100] for e in group_examples)
        common_error = error_counter.most_common(1)[0][0] if error_counter else ""

        # Suggested dimension
        dimension = _infer_dimension(category, tool)
        severity = _infer_severity(count, total_runs)

        # Generate failure pattern
        if use_llm and count >= 2:
            pattern, fix = await _extract_failure_pattern(tool, category, group_examples)
        else:
            pattern = f"Recurring failures in tool '{tool}' (category: {category})"
            fix = ""

        return FailureCluster(
            cluster_id="",
            failure_pattern=pattern,
            examples=[e.to_dict() for e in group_examples[:10]],
            count=count,
            common_tool=tool,
            common_error=common_error,
            common_error_category=category,
            suggested_dimension=dimension,
            suggested_fix=fix,
            avg_confidence=avg_conf,
            severity=severity,
        )

    def _merge_similar_clusters(self, clusters: list[FailureCluster]) -> list[FailureCluster]:
        """Merge clusters that are semantically similar using embeddings."""
        if len(clusters) <= 1:
            return clusters

        # Try embedding-based merging
        embeddings: list[Optional[bytes]] = []
        for c in clusters:
            text = f"{c.common_tool} {c.common_error_category} {c.failure_pattern[:200]}"
            emb = _try_embed_text(text)
            embeddings.append(emb)

        merged: list[FailureCluster] = []
        used: set[int] = set()

        for i, c in enumerate(clusters):
            if i in used:
                continue
            if embeddings[i] is None:
                merged.append(c)
                used.add(i)
                continue

            # Find all similar clusters
            similar_indices = [i]
            for j in range(i + 1, len(clusters)):
                if j in used or embeddings[j] is None:
                    continue
                dist = _cosine_distance(embeddings[i], embeddings[j])
                if dist < self.similarity_threshold:
                    similar_indices.append(j)

            if len(similar_indices) == 1:
                merged.append(c)
                used.add(i)
            else:
                # Merge similar clusters
                merged_cluster = self._merge_cluster_list(
                    [clusters[idx] for idx in similar_indices]
                )
                merged.append(merged_cluster)
                used.update(similar_indices)

        return merged

    def _merge_cluster_list(self, to_merge: list[FailureCluster]) -> FailureCluster:
        """Merge multiple clusters into one."""
        base = to_merge[0]
        all_examples = list(base.examples)
        total_count = base.count
        for c in to_merge[1:]:
            all_examples.extend(c.examples)
            total_count += c.count

        # Take the most severe among merged
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        best_severity = min(to_merge, key=lambda c: severity_order.get(c.severity, 99))

        return FailureCluster(
            cluster_id="",
            failure_pattern=base.failure_pattern,
            examples=all_examples[:15],
            count=total_count,
            common_tool=base.common_tool,
            common_error=base.common_error,
            common_error_category=base.common_error_category,
            suggested_dimension=base.suggested_dimension,
            suggested_fix=base.suggested_fix,
            avg_confidence=base.avg_confidence,
            severity=best_severity.severity,
        )

    def digest_from_store(
        self,
        run_store_path: str,
        *,
        limit: int = 50,
    ) -> list[FailureCluster]:
        """Synchronous wrapper for digesting from a file-based run store."""
        import asyncio
        # Stub — real implementation would read from a persistent run store
        return []


# Singleton
_DIGESTER: Optional[Digester] = None


def get_digester() -> Digester:
    global _DIGESTER
    if _DIGESTER is None:
        _DIGESTER = Digester()
    return _DIGESTER
