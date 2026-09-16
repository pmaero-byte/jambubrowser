"""Model management and MLX endpoints."""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["models"])


# ── Models ──


@router.get("/models/available")
async def list_available_models():
    """List available Gemma 3 models with specs."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return mgr.list_available()


@router.get("/models/installed")
async def list_installed_models():
    """List installed models across all providers."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return {"models": mgr.list_installed()}


@router.get("/models/status")
async def model_status(model: str = None):
    """Get status of a specific model."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return mgr.get_status(model)


@router.post("/models/pull")
async def pull_model(model: str = None):
    """Pull a model via Ollama."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return await mgr.pull(model)


@router.get("/models/recommend")
async def recommend_model():
    """Recommend a model based on system RAM."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return mgr.recommend()


@router.post("/models/setup")
async def setup_gemma3(model_size: str = "12b"):
    """One-click model setup."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return await mgr.setup(model_size)


@router.get("/models/providers")
async def check_providers():
    """Check which LLM providers are available."""
    from backend.modules.model_manager import get_model_manager
    mgr = get_model_manager()
    return mgr.check_providers()


# ── MLX ──


@router.get("/mlx/status")
async def mlx_status():
    """Get MLX provider status: server running, available models, cached models."""
    from backend.modules.mlx_provider import get_provider_info
    return get_provider_info()


@router.post("/mlx/server/start")
async def mlx_start(model: str = "gemma3:12b", port: int = 8080):
    """Start the MLX LM server."""
    from backend.modules.mlx_provider import mlx_start_server
    return await mlx_start_server(model=model, port=port)


@router.post("/mlx/server/stop")
async def mlx_stop():
    """Stop the MLX LM server."""
    from backend.modules.mlx_provider import mlx_stop_server
    return mlx_stop_server()


@router.get("/mlx/models")
async def mlx_models():
    """List available MLX models (definitions + cached)."""
    from backend.modules.mlx_provider import (
        MLX_DEFAULT_MODEL,
        get_available_mlx_models,
        mlx_list_cached_models,
    )
    return {
        "models": get_available_mlx_models(),
        "cached": await mlx_list_cached_models(),
        "default_model": MLX_DEFAULT_MODEL,
    }


@router.post("/mlx/models/download")
async def mlx_download(model_id: str = "gemma3:12b"):
    """Download an MLX model from HuggingFace."""
    from backend.modules.mlx_provider import mlx_download_model
    return await mlx_download_model(model_id)


@router.post("/mlx/generate")
async def mlx_generate_endpoint(
    prompt: str,
    system: Optional[str] = None,
    model: str = "gemma3:12b",
    max_tokens: int = 500,
    temperature: float = 0.7,
):
    """Generate text using MLX local LLM."""
    from backend.modules.mlx_provider import mlx_generate
    text, usage = await mlx_generate(
        prompt, system, model=model, max_tokens=max_tokens, temperature=temperature
    )
    return {"text": text, "usage": usage}
