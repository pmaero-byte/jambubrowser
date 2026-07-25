"""
LLM Configuration & Auto-Detection
====================================
Centralized LLM configuration with Gemma 3 as the default
local model. Auto-detects available local LLM providers
(Ollama, llama.cpp) and configures the engine accordingly.

Priority:
1. JAMBU_LLM_* environment variables
2. Running Ollama with Gemma 3
3. Running llama.cpp server
4. Fallback to localhost defaults
"""

import os
import psutil
from typing import Optional, Dict


# ---- Gemma 3 Default Configuration ----

GEMMA4_DEFAULT_CONFIG = {
    "baseUrl": "http://localhost:11434/v1",  # Ollama OpenAI-compatible endpoint
    "modelId": "gemma3:12b",
    "apiKey": "",
    "temperature": 0.7,
    "maxTokens": 4096,
    "contextLength": 8192,
    "provider": "ollama",
    "vision_model": "gemma3:12b",
}

# Alternative provider configurations
PROVIDER_CONFIGS = {
    "ollama": {
        "baseUrl": "http://localhost:11434/v1",
        "modelId": "gemma3:12b",
        "apiKey": "",
    },
    "llamacpp": {
        "baseUrl": "http://localhost:8080/v1",
        "modelId": "gemma-3-12b",
        "apiKey": "",
    },
    "openai": {
        "baseUrl": "https://api.openai.com/v1",
        "modelId": "gpt-4o",
    },
    "openrouter": {
        "baseUrl": "https://openrouter.ai/api/v1",
        "modelId": "google/gemma-3-12b",
    },
}


class LLMConfig:
    """
    Centralized LLM configuration manager.
    Auto-detects local providers and applies Gemma 3 defaults.
    """

    def __init__(self):
        self._config = None
        self._provider = None

    def detect_provider(self) -> str:
        """Auto-detect which LLM provider to use."""
        # 1. Check explicit env var
        provider = os.environ.get("JAMBU_LLM_PROVIDER", "").lower()
        if provider in PROVIDER_CONFIGS:
            return provider

        # 2. Check for Ollama
        try:
            import httpx
            # Quick check if Ollama is running
            import asyncio
            async def check_ollama():
                try:
                    async with httpx.AsyncClient() as c:
                        r = await c.get("http://localhost:11434/api/tags", timeout=2.0)
                        return r.status_code == 200
                except Exception:
                    return False
            if asyncio.get_event_loop().is_running():
                # Can't run async in running loop - assume Ollama if env suggests it
                if os.environ.get("OLLAMA_HOST"):
                    return "ollama"
            else:
                # Check synchronously with subprocess
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                result = s.connect_ex(('localhost', 11434))
                s.close()
                if result == 0:
                    return "ollama"
        except Exception:
            pass

        # 3. Check for llama.cpp
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(('localhost', 8080))
            s.close()
            if result == 0:
                return "llamacpp"
        except Exception:
            pass

        # 4. Check for OpenAI API key
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"

        # 5. Default to Ollama (user can install it)
        return "ollama"

    def get_config(self, provider: str = None) -> Dict:
        """
        Get the LLM configuration.

        Args:
            provider: Override provider ('ollama', 'llamacpp', 'openai', 'openrouter')

        Returns:
            Dict with baseUrl, modelId, apiKey, etc.
        """
        if provider is None:
            provider = self.detect_provider()

        base_config = PROVIDER_CONFIGS.get(provider, GEMMA4_DEFAULT_CONFIG).copy()

        # Override with environment variables
        if os.environ.get("JAMBU_LLM_BASE_URL"):
            base_config["baseUrl"] = os.environ["JAMBU_LLM_BASE_URL"]
        if os.environ.get("JAMBU_LLM_MODEL"):
            base_config["modelId"] = os.environ["JAMBU_LLM_MODEL"]
        if os.environ.get("JAMBU_LLM_API_KEY"):
            base_config["apiKey"] = os.environ["JAMBU_LLM_API_KEY"]
        if os.environ.get("OPENAI_API_KEY") and provider == "openai":
            base_config["apiKey"] = os.environ["OPENAI_API_KEY"]

        return base_config

    def get_default(self) -> Dict:
        """Get the recommended default configuration (Gemma 3)."""
        config = GEMMA4_DEFAULT_CONFIG.copy()

        # Override with env vars
        if os.environ.get("JAMBU_LLM_BASE_URL"):
            config["baseUrl"] = os.environ["JAMBU_LLM_BASE_URL"]
        if os.environ.get("JAMBU_LLM_MODEL"):
            config["modelId"] = os.environ["JAMBU_LLM_MODEL"]
        if os.environ.get("JAMBU_LLM_API_KEY"):
            config["apiKey"] = os.environ["JAMBU_LLM_API_KEY"]

        return config

    def get_vision_config(self) -> Dict:
        """Get configuration for vision-capable model."""
        config = self.get_default()
        config["modelId"] = os.environ.get(
            "JAMBU_VISION_MODEL",
            config.get("vision_model", "gemma3:12b"),
        )
        return config

    def get_system_info(self) -> Dict:
        """Get system information relevant to LLM selection."""
        mem = psutil.virtual_memory()
        return {
            "total_ram_gb": round(mem.total / (1024 ** 3), 1),
            "available_ram_gb": round(mem.available / (1024 ** 3), 1),
            "cpu_count": psutil.cpu_count(),
            "provider": self.detect_provider(),
            "recommended_model": self._recommend_by_ram(mem.available),
            "config": self.get_default(),
        }

    def _recommend_by_ram(self, available_bytes: int) -> str:
        """Recommend a Gemma 3 model based on available RAM."""
        available_gb = available_bytes / (1024 ** 3)
        if available_gb >= 20:
            return "gemma3:27b"
        elif available_gb >= 8:
            return "gemma3:12b"
        elif available_gb >= 4:
            return "gemma3:4b"
        else:
            return "gemma3:1b"


_config_instance: Optional[LLMConfig] = None


def get_llm_config() -> LLMConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = LLMConfig()
    return _config_instance


def get_default_llm_config() -> Dict:
    """Quick access to default LLM config (Gemma 3)."""
    return get_llm_config().get_default()
