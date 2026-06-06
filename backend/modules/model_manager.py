"""
Local Model Manager
====================
Pull, manage, and monitor local AI models with a focus on
Google Gemma 4 models for fully local intelligence.

Supports:
- Ollama (recommended): `ollama pull gemma4:12b`
- llama.cpp: via llama-server with GGUF files
- HuggingFace Hub: direct GGUF downloads

Gemma 4 variants available locally:
- gemma4:1b  (lightweight, ~0.8GB)
- gemma4:4b  (balanced, ~2.4GB)
- gemma4:12b (recommended, ~7GB)
- gemma4:27b (powerful, ~16GB)
"""

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

import httpx


# Gemma 4 model definitions
GEMMA4_MODELS = {
    "gemma4:1b": {
        "name": "gemma4:1b",
        "family": "gemma4",
        "size": "1B",
        "disk_gb": 0.8,
        "ram_gb": 2,
        "context": 8192,
        "quantization": "Q4_K_M",
        "description": "Lightweight Gemma 4 - ideal for quick queries and edge devices",
    },
    "gemma4:4b": {
        "name": "gemma4:4b",
        "family": "gemma4",
        "size": "4B",
        "disk_gb": 2.4,
        "ram_gb": 4,
        "context": 8192,
        "quantization": "Q4_K_M",
        "description": "Balanced Gemma 4 - good for general research tasks",
    },
    "gemma4:12b": {
        "name": "gemma4:12b",
        "family": "gemma4",
        "size": "12B",
        "disk_gb": 7.0,
        "ram_gb": 8,
        "context": 8192,
        "quantization": "Q4_K_M",
        "description": "Recommended Gemma 4 - excellent reasoning and research capabilities",
    },
    "gemma4:27b": {
        "name": "gemma4:27b",
        "family": "gemma4",
        "size": "27B",
        "disk_gb": 16.0,
        "ram_gb": 20,
        "context": 8192,
        "quantization": "Q4_K_M",
        "description": "Powerful Gemma 4 - maximum intelligence for complex analysis",
    },
}


@dataclass
class ModelInfo:
    """Information about an installed model."""
    name: str
    family: str
    size: str
    provider: str  # ollama, llamacpp, huggingface
    status: str  # ready, pulling, error, not_installed
    modified_at: str = ""
    disk_size: str = ""
    digest: str = ""


class ModelManager:
    """
    Local model lifecycle manager.
    Pull, list, and monitor AI models with Ollama and llama.cpp.
    """

    DEFAULT_OLLAMA_URL = "http://localhost:11434"
    DEFAULT_LLAMACPP_URL = "http://localhost:8080"

    def __init__(self):
        self._ollama_url = os.environ.get("OLLAMA_HOST", self.DEFAULT_OLLAMA_URL)
        self._llamacpp_url = os.environ.get("LLAMACPP_HOST", self.DEFAULT_LLAMACPP_URL)
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    @staticmethod
    def get_default_model() -> str:
        """Get the recommended default model."""
        return os.environ.get("JAMBU_DEFAULT_MODEL", "gemma4:12b")

    @staticmethod
    def get_vision_model() -> str:
        """Get the recommended vision-capable model."""
        return os.environ.get("JAMBU_VISION_MODEL", "gemma4:12b")

    @staticmethod
    def get_available_models() -> List[dict]:
        """Get all available Gemma 4 model definitions."""
        return [
            {
                "name": m["name"],
                "size": m["size"],
                "disk_gb": m["disk_gb"],
                "ram_gb": m["ram_gb"],
                "context": m["context"],
                "description": m["description"],
            }
            for m in GEMMA4_MODELS.values()
        ]

    # ---- Ollama Operations ----

    async def _ollama_api(self, endpoint: str, method: str = "GET",
                           json_data: dict = None) -> Optional[dict]:
        """Call the Ollama REST API."""
        client = await self._get_client()
        try:
            if method == "GET":
                resp = await client.get(f"{self._ollama_url}/api/{endpoint}", timeout=10.0)
            elif method == "POST":
                resp = await client.post(
                    f"{self._ollama_url}/api/{endpoint}",
                    json=json_data or {},
                    timeout=120.0,
                )
            else:
                resp = await client.delete(f"{self._ollama_url}/api/{endpoint}", timeout=10.0)

            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    async def is_ollama_running(self) -> bool:
        """Check if Ollama is installed and running."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._ollama_url}/api/tags", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def pull_model(self, model_name: str) -> Dict:
        """
        Pull a model using Ollama.

        Args:
            model_name: e.g. 'gemma4:12b', 'gemma4:4b', 'gemma4:1b'

        Returns:
            dict with status, progress, and model info
        """
        model_def = GEMMA4_MODELS.get(model_name, {})

        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self._ollama_url}/api/pull",
                json={"name": model_name, "stream": False},
                timeout=600.0,  # 10 min timeout for large models
            )

            if resp.status_code == 200:
                return {
                    "status": "success",
                    "model": model_name,
                    "size": model_def.get("size", "unknown"),
                    "description": model_def.get("description", ""),
                    "message": f"Successfully pulled {model_name}",
                }

            return {
                "status": "error",
                "model": model_name,
                "message": f"Failed to pull model (status {resp.status_code})",
            }
        except httpx.TimeoutException:
            return {
                "status": "timeout",
                "model": model_name,
                "message": f"Pull timed out. {model_name} may still be downloading.",
            }
        except httpx.ConnectError:
            return {
                "status": "error",
                "model": model_name,
                "message": "Ollama is not running. Start it with: ollama serve",
                "help": "Install Ollama: curl -fsSL https://ollama.com/install.sh | sh",
            }
        except Exception as e:
            return {
                "status": "error",
                "model": model_name,
                "message": str(e)[:200],
            }

    async def pull_model_stream(self, model_name: str):
        """
        Pull a model with streaming progress updates.
        Yields progress dicts.
        """
        client = await self._get_client()
        try:
            async with client.stream(
                "POST",
                f"{self._ollama_url}/api/pull",
                json={"name": model_name, "stream": True},
                timeout=600.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            yield {
                                "status": data.get("status", "pulling"),
                                "model": model_name,
                                "completed": data.get("completed", 0),
                                "total": data.get("total", 0),
                                "progress": round(
                                    data.get("completed", 0) / max(data.get("total", 1), 1) * 100, 1
                                ),
                            }
                            if data.get("status") == "success":
                                break
                        except json.JSONDecodeError:
                            continue
        except httpx.ConnectError:
            yield {
                "status": "error",
                "model": model_name,
                "message": "Ollama is not running.",
            }

    async def list_ollama_models(self) -> List[ModelInfo]:
        """List models installed via Ollama."""
        data = await self._ollama_api("tags")
        if not data:
            return []

        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            model_def = GEMMA4_MODELS.get(name, {})

            models.append(ModelInfo(
                name=name,
                family=model_def.get("family", "unknown"),
                size=model_def.get("size", m.get("details", {}).get("parameter_size", "")),
                provider="ollama",
                status="ready",
                modified_at=m.get("modified_at", ""),
                disk_size=m.get("size", ""),
                digest=m.get("digest", ""),
            ))

        return models

    async def delete_ollama_model(self, model_name: str) -> Dict:
        """Delete a model from Ollama."""
        data = await self._ollama_api("delete", method="DELETE")
        # Ollama delete uses the model name in the request
        client = await self._get_client()
        try:
            resp = await client.delete(
                f"{self._ollama_url}/api/delete",
                json={"name": model_name},
                timeout=30.0,
            )
            if resp.status_code == 200:
                return {"status": "deleted", "model": model_name}
            return {"status": "error", "message": f"Delete failed (status {resp.status_code})"}
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    # ---- llama.cpp Operations ----

    async def is_llamacpp_running(self) -> bool:
        """Check if llama.cpp server is running."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._llamacpp_url}/health", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def get_llamacpp_models(self) -> List[ModelInfo]:
        """Get models loaded in llama.cpp server."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._llamacpp_url}/v1/models", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    ModelInfo(
                        name=m.get("id", ""),
                        family="unknown",
                        size="",
                        provider="llamacpp",
                        status="ready",
                    )
                    for m in data.get("data", [])
                ]
        except Exception:
            pass
        return []

    # ---- Combined Operations ----

    async def list_all_models(self) -> List[ModelInfo]:
        """List all models across all providers."""
        ollama_models = await self.list_ollama_models()
        llamacpp_models = await self.get_llamacpp_models()
        all_models = ollama_models + llamacpp_models

        # Add Gemma 4 models that aren't installed yet
        installed_names = {m.name for m in all_models}
        for name, info in GEMMA4_MODELS.items():
            if name not in installed_names:
                all_models.append(ModelInfo(
                    name=name,
                    family=info["family"],
                    size=info["size"],
                    provider="not_installed",
                    status="not_installed",
                ))

        return all_models

    async def get_model_status(self, model_name: str) -> Dict:
        """Get detailed status of a specific model."""
        models = await self.list_all_models()
        for m in models:
            if m.name == model_name:
                model_def = GEMMA4_MODELS.get(model_name, {})
                return {
                    "name": m.name,
                    "family": m.family,
                    "size": m.size,
                    "provider": m.provider,
                    "status": m.status,
                    "disk_required_gb": model_def.get("disk_gb"),
                    "ram_required_gb": model_def.get("ram_gb"),
                    "context_length": model_def.get("context", 8192),
                    "description": model_def.get("description", ""),
                    "modified_at": m.modified_at,
                    "digest": m.digest,
                }

        return {
            "name": model_name,
            "status": "unknown",
            "message": f"Model '{model_name}' not found",
        }

    async def setup_gemma4(self, model_size: str = "12b") -> Dict:
        """
        One-click Gemma 4 setup. Pulls the recommended model.

        Args:
            model_size: '1b', '4b', '12b', or '27b'

        Returns:
            Setup result dict
        """
        model_name = f"gemma4:{model_size}"

        if model_name not in GEMMA4_MODELS:
            return {
                "status": "error",
                "message": f"Unknown Gemma 4 size: {model_size}. Use: 1b, 4b, 12b, 27b",
            }

        # Check if Ollama is available
        if not await self.is_ollama_running():
            return {
                "status": "error",
                "model": model_name,
                "message": "Ollama is not running. Install with: curl -fsSL https://ollama.com/install.sh | sh",
                "setup_steps": [
                    "1. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh",
                    "2. Start Ollama: ollama serve",
                    "3. Pull model: ollama pull gemma4:12b",
                    "4. Test: ollama run gemma4:12b 'Hello'",
                ],
            }

        # Check if already installed
        models = await self.list_ollama_models()
        if any(m.name == model_name for m in models):
            return {
                "status": "already_installed",
                "model": model_name,
                "message": f"{model_name} is already installed and ready to use.",
            }

        # Pull the model
        return await self.pull_model(model_name)

    async def recommend_model(self, available_ram_gb: float = None) -> Dict:
        """
        Recommend the best Gemma 4 model based on available RAM.

        Args:
            available_ram_gb: Available system RAM (auto-detected if None)

        Returns:
            Recommended model info
        """
        if available_ram_gb is None:
            try:
                import psutil
                available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
            except ImportError:
                available_ram_gb = 8  # Conservative default

        recommendations = [
            ("gemma4:27b", 20), ("gemma4:12b", 8),
            ("gemma4:4b", 4), ("gemma4:1b", 2),
        ]

        for model_name, min_ram in recommendations:
            if available_ram_gb >= min_ram:
                model_def = GEMMA4_MODELS[model_name]
                return {
                    "recommended": model_name,
                    "size": model_def["size"],
                    "reason": f"Best fit for {available_ram_gb:.0f}GB available RAM",
                    "available_ram_gb": round(available_ram_gb, 1),
                    "all_options": [
                        {"name": m["name"], "size": m["size"], "ram_gb": m["ram_gb"]}
                        for m in GEMMA4_MODELS.values()
                    ],
                }

        return {"recommended": "gemma4:1b", "reason": "Minimum viable model"}

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


_module_manager: Optional[ModelManager] = None


def get_model_manager() -> ModelManager:
    global _module_manager
    if _module_manager is None:
        _module_manager = ModelManager()
    return _module_manager
