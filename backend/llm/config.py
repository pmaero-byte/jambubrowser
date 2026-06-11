"""
Environment-based LLM configuration.

Reads from env vars (JAMBU_LLM_*) at startup. Use `get_config()` to read the
current config; `reload_config()` to re-read from env (useful for tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    return val if val is not None and val != "" else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key, "").lower().strip()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class LLMConfig:
    """Provider selection + per-provider credentials. Loaded from env."""
    # Default provider selection
    default_provider: str = "auto"
    default_model: str = ""

    # Fallback chain (comma-separated)
    fallback_chain: list[str] = field(default_factory=lambda: ["ollama"])

    # Timeouts
    request_timeout: float = 30.0
    health_timeout: float = 3.0

    # Default params
    max_tokens: int = 1024
    temperature: float = 0.3

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_base_url: str = "https://api.anthropic.com"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # Ollama
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "gemma4:12b-it-qat"

    # MLX
    mlx_base_url: str = "http://127.0.0.1:8080/v1"
    mlx_model: str = "gemma4:12b"

    # MiniMax
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M2.7"

    # Behavior
    auto_health_check: bool = True
    force_local_only: bool = False  # privacy mode enforcement

    @classmethod
    def from_env(cls) -> "LLMConfig":
        chain_raw = _env("JAMBU_LLM_FALLBACK_CHAIN", "ollama,mlx,anthropic,openai,minimax")
        chain = [c.strip() for c in chain_raw.split(",") if c.strip()]
        return cls(
            default_provider=_env("JAMBU_LLM_PROVIDER", "auto"),
            default_model=_env("JAMBU_LLM_MODEL", ""),
            fallback_chain=chain or ["ollama"],
            request_timeout=_env_float("JAMBU_LLM_TIMEOUT", 30.0),
            health_timeout=_env_float("JAMBU_LLM_HEALTH_TIMEOUT", 3.0),
            max_tokens=_env_int("JAMBU_LLM_MAX_TOKENS", 1024),
            temperature=_env_float("JAMBU_LLM_TEMPERATURE", 0.3),
            anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
            anthropic_model=_env("JAMBU_LLM_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            anthropic_base_url=_env("JAMBU_LLM_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            openai_api_key=_env("OPENAI_API_KEY", ""),
            openai_model=_env("JAMBU_LLM_OPENAI_MODEL", "gpt-4o"),
            openai_base_url=_env("JAMBU_LLM_OPENAI_BASE_URL", "https://api.openai.com/v1"),
            ollama_base_url=_env("JAMBU_LLM_OLLAMA_BASE_URL", _env("OLLAMA_BASE_URL", "http://localhost:11434/v1")),
            ollama_model=_env("JAMBU_LLM_OLLAMA_MODEL", _env("OLLAMA_MODEL", "gemma4:12b-it-qat")),
            mlx_base_url=_env("JAMBU_LLM_MLX_BASE_URL", "http://127.0.0.1:8080/v1"),
            mlx_model=_env("JAMBU_LLM_MLX_MODEL", "gemma4:12b"),
            minimax_api_key=_env("MINIMAX_API_KEY", ""),
            minimax_base_url=_env("JAMBU_LLM_MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
            minimax_model=_env("JAMBU_LLM_MINIMAX_MODEL", "MiniMax-M2.7"),
            auto_health_check=_env_bool("JAMBU_LLM_AUTO_HEALTH_CHECK", True),
            force_local_only=_env_bool("JAMBU_LLM_LOCAL_ONLY", False),
        )

    def model_for(self, provider: str) -> str:
        """Return the configured default model for a given provider."""
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
            "ollama": self.ollama_model,
            "mlx": self.mlx_model,
            "minimax": self.minimax_model,
            "mock": "mock-model",
        }.get(provider, "")


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_CONFIG: Optional[LLMConfig] = None


def get_config() -> LLMConfig:
    """Return the current LLMConfig, loading from env on first call."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = LLMConfig.from_env()
    return _CONFIG


def reload_config() -> LLMConfig:
    """Force-reload config from environment (used by tests)."""
    global _CONFIG
    _CONFIG = LLMConfig.from_env()
    return _CONFIG
