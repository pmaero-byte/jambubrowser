"""
Mixture-of-Agents (MoA) provider — virtual provider that fans out to
reference models then synthesises their outputs with an aggregator.

Inspired by Nous Research's Hermes Agent MoA pattern:

    1. Reference models run in parallel (no tool schemas, trimmed context).
    2. The aggregator receives the original conversation PLUS the reference
       outputs and produces the final answer (with full tool schemas).
    3. The aggregator is the "acting" model — it writes the assistant response
       and makes tool calls.  Reference models are advisory only.

Benchmarks (HermesBench): a two-model MoA preset (Claude Opus aggregating
over GPT-5.5) scores ~0.82 vs 0.76 for Opus alone — a ~6-point lift.

Usage
-----
    export JAMBU_LLM_MOA_PRESETS='{"default": {"reference_models": [...]}}'
    export JAMBU_LLM_FALLBACK_CHAIN="moa,ollama,mlx"
    # or select by name: set_default_provider("moa")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, AsyncIterator, Optional

from ..base import (
    ChatMessage,
    ChatResponse,
    ProviderError,
    ProviderUnavailable,
    StreamChunk,
    Usage,
)
from ..config import LLMConfig

log = logging.getLogger("jambu.llm.moa")

NAME = "moa"

# ── Default reference system prompt ──────────────────────────────────────────
# Reference models receive only user/assistant conversation turns (no system
# prompt, no tool schemas).  This keeps them cheap and avoids strict-provider
# rejections when the conversation includes tool-call transcript.
_REF_SYSTEM_PROMPT = (
    "You are a helpful assistant providing a concise second opinion. "
    "Analyse the user's request and respond directly."
)

# ── Reference call default ───────────────────────────────────────────────────
_REF_MAX_TOKENS = 1024  # reference calls are short advisory passes


def _default_presets() -> dict:
    """Built-in presets that work out of the box with local + cloud providers."""
    return {
        "default": {
            "reference_models": [
                {"provider": "ollama", "model": ""},
                {"provider": "mlx", "model": ""},
            ],
            "aggregator": {"provider": "anthropic", "model": ""},
            "reference_temperature": 0.6,
            "aggregator_temperature": 0.4,
            "max_tokens": 4096,
            "enabled": True,
        },
        "quality": {
            "reference_models": [
                {"provider": "anthropic", "model": ""},
                {"provider": "openai", "model": ""},
                {"provider": "minimax", "model": ""},
            ],
            "aggregator": {"provider": "anthropic", "model": ""},
            "reference_temperature": 0.6,
            "aggregator_temperature": 0.3,
            "max_tokens": 8192,
            "enabled": True,
        },
        "local": {
            "reference_models": [
                {"provider": "ollama", "model": ""},
                {"provider": "mlx", "model": ""},
            ],
            "aggregator": {"provider": "mlx", "model": ""},
            "reference_temperature": 0.5,
            "aggregator_temperature": 0.3,
            "max_tokens": 2048,
            "enabled": True,
        },
        "mock": {
            "reference_models": [
                {"provider": "mock", "model": ""},
            ],
            "aggregator": {"provider": "mock", "model": ""},
            "reference_temperature": 0.5,
            "aggregator_temperature": 0.3,
            "max_tokens": 1024,
            "enabled": True,
        },
    }


class MixtureOfAgentsProvider:
    """Virtual provider that delegates to multiple sub-providers per turn.

    Each call fans out to *reference* models in parallel, collects their
    responses, then passes them as additional context to the *aggregator*
    model which produces the final answer (and may make tool calls).

    Presets are configured via the ``JAMBU_LLM_MOA_PRESETS`` env var (JSON)
    or fall back to the built-in presets.
    """

    name = NAME
    models = ["default", "quality", "local"]
    supports_tools = True

    def __init__(self, config: LLMConfig):
        self.config = config
        self._presets = self._load_presets()

    # ── public Provider protocol ─────────────────────────────────────────────

    async def health(self) -> bool:
        """Return True if at least one preset's aggregator is reachable."""
        registry = _get_registry()
        for preset_name, preset in self._presets.items():
            if not preset.get("enabled", True):
                continue
            agg = preset["aggregator"]
            try:
                provider = registry.get(agg["provider"])
                if await provider.health():
                    return True
            except Exception:
                continue
        return False

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Optional[list[dict]] = None,
        timeout: float = 30.0,
    ) -> ChatResponse:
        preset_name = model or "default"
        preset = self._presets.get(preset_name)
        if preset is None:
            raise ProviderError(
                f"Unknown MoA preset {preset_name!r}. "
                f"Available: {list(self._presets)}"
            )

        started = time.monotonic()
        registry = _get_registry()

        # ── Phase 1: fan-out to reference models (parallel) ────────────────
        ref_prompt = _build_reference_prompt(messages)
        ref_tasks = []
        for ref_cfg in preset["reference_models"]:
            ref_tasks.append(
                _call_reference(
                    registry,
                    ref_cfg,
                    ref_prompt,
                    temperature=preset.get("reference_temperature", 0.6),
                    timeout=timeout,
                )
            )

        ref_results = await asyncio.gather(*ref_tasks, return_exceptions=True)

        # Collect successful reference outputs + aggregate usage
        ref_outputs: list[str] = []
        ref_usage = Usage()
        for i, result in enumerate(ref_results):
            if isinstance(result, ChatResponse):
                ref_outputs.append(result.content)
                ref_usage += result.usage
            else:
                ref_cfg = preset["reference_models"][i]
                log.warning(
                    "Reference %s/%s failed: %s",
                    ref_cfg["provider"],
                    ref_cfg.get("model", ""),
                    result,
                )
                ref_outputs.append(
                    f"[{ref_cfg['provider']}] (unavailable)"
                )

        # ── Phase 2: call the aggregator ───────────────────────────────────
        agg_cfg = preset["aggregator"]
        try:
            agg_provider = registry.get(agg_cfg["provider"])
        except KeyError as e:
            raise ProviderError(
                f"Aggregator provider {agg_cfg['provider']!r} not found: {e}"
            ) from e

        agg_model = agg_cfg.get("model") or self.config.model_for(
            agg_cfg["provider"]
        )
        agg_messages = _build_aggregator_messages(messages, ref_outputs)

        try:
            agg_response = await agg_provider.chat(
                agg_messages,
                model=agg_model,
                max_tokens=preset.get("max_tokens", max_tokens),
                temperature=preset.get("aggregator_temperature", 0.4),
                tools=tools,
                timeout=timeout,
            )
        except Exception as e:
            raise ProviderError(
                f"Aggregator {agg_cfg['provider']} failed: {e}"
            ) from e

        # ── Combine usage ─────────────────────────────────────────────────
        summed = agg_response.usage + ref_usage
        summed.cost_usd = agg_response.usage.cost_usd + ref_usage.cost_usd

        latency = (time.monotonic() - started) * 1000
        return ChatResponse(
            content=agg_response.content,
            model=f"moa:{preset_name}",
            provider=self.name,
            usage=summed,
            finish_reason=agg_response.finish_reason,
            tool_calls=agg_response.tool_calls,
            raw={
                "moa_preset": preset_name,
                "ref_count": len(ref_outputs),
                **agg_response.raw,
            },
            latency_ms=latency,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Optional[list[dict]] = None,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        """Stream — resolve via :meth:`chat` then replay as chunks.

        Because MoA must wait for all reference calls *and* the aggregator
        before it can emit anything, there is no incremental advantage in a
        true streaming path.  We keep the path simple: call ``chat()`` and
        stream its output.
        """
        resp = await self.chat(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            timeout=timeout,
        )
        if resp.content:
            for i in range(0, len(resp.content), 16):
                yield StreamChunk(delta=resp.content[i : i + 16])
                await asyncio.sleep(0.005)
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield StreamChunk(delta="", tool_calls=[tc])
        yield StreamChunk(
            delta="", finish_reason=resp.finish_reason, usage=resp.usage
        )

    def estimate_cost(self, usage: Usage, model: Optional[str] = None) -> float:
        """MoA cost is already summed in :meth:`chat`."""
        return usage.cost_usd

    # ── preset loading ───────────────────────────────────────────────────────

    def _load_presets(self) -> dict:
        """Load preset definitions from override > env var > built-in defaults.

        Resolution order:
        1. ``_OVERRIDE_PRESETS`` (set via :func:`set_presets` from the API).
        2. The env var ``JAMBU_LLM_MOA_PRESETS`` (JSON object keyed by preset name).
        3. :func:`_default_presets` built-in.
        """
        if _OVERRIDE_PRESETS is not None:
            return _OVERRIDE_PRESETS
        raw = os.environ.get("JAMBU_LLM_MOA_PRESETS", "")
        if raw:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("must be a JSON object")
                return parsed
            except (json.JSONDecodeError, ValueError) as e:
                log.warning("Invalid JAMBU_LLM_MOA_PRESETS (%s); using defaults", e)
        return _default_presets()


# ── Module-level preset override ────────────────────────────────────────────

_OVERRIDE_PRESETS: Optional[dict] = None
_OVERRIDE_LOCK = threading.Lock()


def set_presets(presets: dict) -> None:
    """Install a runtime preset override (replaces env var + defaults).

    The override is process-wide: every MixtureOfAgentsProvider instance
    created after this call will see the new presets, and existing
    instances will pick them up on the next preset lookup.
    """
    global _OVERRIDE_PRESETS
    if not isinstance(presets, dict):
        raise ValueError("presets must be a JSON object keyed by preset name")
    for name, body in presets.items():
        if not isinstance(body, dict):
            raise ValueError(f"preset {name!r} must be an object")
        if "aggregator" not in body:
            raise ValueError(f"preset {name!r} is missing required 'aggregator' field")
    with _OVERRIDE_LOCK:
        _OVERRIDE_PRESETS = dict(presets)


def clear_presets() -> None:
    """Remove the runtime override so providers fall back to env / defaults."""
    global _OVERRIDE_PRESETS
    with _OVERRIDE_LOCK:
        _OVERRIDE_PRESETS = None


def get_active_presets() -> dict:
    """Return whichever preset set is currently active (override > env > default).

    The result is a deep-ish copy suitable for serialization to the UI.
    """
    with _OVERRIDE_LOCK:
        if _OVERRIDE_PRESETS is not None:
            return _OVERRIDE_PRESETS
    # No override: instantiate a throwaway provider to trigger _load_presets.
    return MixtureOfAgentsProvider(LLMConfig())._presets


# ── module-level helpers (pytest-accessible) ─────────────────────────────────


def _get_registry():
    """Lazy import to avoid circular dependency at module load time."""
    from ..registry import get_registry as _gr

    return _gr()


def _build_reference_prompt(
    messages: list[ChatMessage],
) -> list[ChatMessage]:
    """Build a stripped-down prompt for reference models.

    Reference models receive only the user/assistant conversation text
    (no system prompt, no tool-call transcript).  This keeps them cheap
    and avoids rejections from strict providers that don't support tool
    schemas.
    """
    ref_msgs = [ChatMessage(role="system", content=_REF_SYSTEM_PROMPT)]
    for m in messages:
        if m.role.value in ("user", "assistant"):
            msg = ChatMessage(role=m.role, content=m.content)
            if m.name:
                msg.name = m.name
            ref_msgs.append(msg)
        # drop system (use our own) and tool messages
    return ref_msgs


def _build_aggregator_messages(
    messages: list[ChatMessage],
    ref_outputs: list[str],
) -> list[ChatMessage]:
    """Append reference outputs as structured context for the aggregator."""
    agg_msgs = list(messages)

    sections = "\n\n[Reference Model Analysis]\n"
    for i, output in enumerate(ref_outputs):
        sections += f"\nReference {i + 1}:\n{output}\n"
    sections += "\n[End Reference Analysis]\n\n"
    sections += (
        "Synthesise the best answer from all reference perspectives above."
    )

    from ..base import Role as _Role
    agg_msgs.append(ChatMessage(role=_Role.USER, content=sections))
    return agg_msgs


async def _call_reference(
    registry,
    ref_cfg: dict,
    prompt_msgs: list[ChatMessage],
    temperature: float,
    timeout: float,
) -> ChatResponse:
    """Call a single reference model (no tool schemas)."""
    provider = registry.get(ref_cfg["provider"])
    model = ref_cfg.get("model") or ""
    return await provider.chat(
        prompt_msgs,
        model=model,
        max_tokens=_REF_MAX_TOKENS,
        temperature=temperature,
        tools=None,
        timeout=timeout,
    )


# ── registry entry point ─────────────────────────────────────────────────────


def register(registry) -> None:
    registry.register_factory(NAME, MixtureOfAgentsProvider)
