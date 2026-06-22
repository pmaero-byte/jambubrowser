"""
MLX LM Provider
===============
Apple Silicon-native LLM provider using MLX framework.
Supports direct inference (via mlx_lm) and server mode (OpenAI-compatible API).

Models are cached locally at ~/.cache/huggingface/hub/ and loaded
directly via mlx_lm for maximum performance on Apple Silicon.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List, Tuple, AsyncGenerator

import httpx

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient

# ---------------------------------------------------------------------------
# MLX Model Registry
# ---------------------------------------------------------------------------

MLX_GEMMA4_MODELS = {
    "gemma4:12b": {
        "hf_path": "mlx-community/gemma-4-12B-it-4bit",
        "name": "Gemma 4 12B",
        "size": "12B",
        "quant": "4-bit",
        "disk_gb": 7.0,
        "ram_gb": 8,
        "description": "Recommended Gemma 4 via MLX - 4-bit quantized instruction-tuned",
    },
    "gemma4:12b-mxfp4": {
        "hf_path": "mlx-community/gemma-4-12B-mxfp4",
        "name": "Gemma 4 12B MXFP4",
        "size": "12B",
        "quant": "MXFP4",
        "disk_gb": 7.5,
        "ram_gb": 8,
        "description": "Gemma 4 12B with MXFP4 quantization via MLX",
    },
    "gemma4:12b-6bit": {
        "hf_path": "mlx-community/gemma-4-12B-6bit",
        "name": "Gemma 4 12B 6-bit",
        "size": "12B",
        "quant": "6-bit",
        "disk_gb": 11.0,
        "ram_gb": 12,
        "description": "Gemma 4 12B with 6-bit quantization (higher quality, more RAM)",
    },
    "gemma4:12b-8bit": {
        "hf_path": "mlx-community/gemma-4-12B-it-8bit",
        "name": "Gemma 4 12B 8-bit",
        "size": "12B",
        "quant": "8-bit",
        "disk_gb": 12.7,
        "ram_gb": 16,
        "description": "Gemma 4 12B with 8-bit quantization (highest quality)",
    },
}

MLX_DEFAULT_MODEL = "gemma4:12b"
MLX_DEFAULT_PORT = 8080
MLX_SERVER_HOST = "127.0.0.1"


# ---------------------------------------------------------------------------
# Availability Check
# ---------------------------------------------------------------------------

def is_mlx_available() -> bool:
    """Check if mlx-lm is installed."""
    try:
        import mlx_lm
        return True
    except ImportError:
        return False


def is_mlx_server_running(port: int = MLX_DEFAULT_PORT) -> bool:
    """Check if MLX LM server is running on the given port."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        return result == 0
    except Exception:
        return False


def get_available_mlx_models() -> List[Dict]:
    """Get list of all available MLX Gemma 4 model definitions."""
    return [
        {
            "id": mid,
            "hf_path": info["hf_path"],
            "name": info["name"],
            "size": info["size"],
            "quant": info["quant"],
            "disk_gb": info["disk_gb"],
            "ram_gb": info["ram_gb"],
            "description": info["description"],
        }
        for mid, info in MLX_GEMMA4_MODELS.items()
    ]


# ---------------------------------------------------------------------------
# Model Cache Discovery
# ---------------------------------------------------------------------------

def find_local_mlx_models() -> List[Dict]:
    """Find MLX models that are already cached locally."""
    import huggingface_hub
    cache_dir = Path(huggingface_hub.constants.HF_HUB_CACHE)
    models = []
    if not cache_dir.exists():
        return models
    
    for entry in cache_dir.iterdir():
        if not entry.name.startswith("models--"):
            continue
        parts = entry.name.split("--")
        if len(parts) >= 2:
            org = parts[1]
            repo_name = "--".join(parts[2:]) if len(parts) > 2 else ""
            full_name = f"{org}/{repo_name}" if repo_name else org
            
            snapshots_dir = entry / "snapshots"
            if snapshots_dir.exists() and any(snapshots_dir.iterdir()):
                models.append({
                    "hf_path": full_name,
                    "local_path": str(entry),
                    "cached": True,
                })
    return models


def resolve_mlx_model_path(model_id: str) -> str:
    """Resolve a model ID or HF path to a local cache path."""
    info = MLX_GEMMA4_MODELS.get(model_id)
    if info:
        return info["hf_path"]
    return model_id  # Treat as raw HF path


# ---------------------------------------------------------------------------
# Server Lifecycle Management
# ---------------------------------------------------------------------------

# Path to the custom MLX VLM server script
_MLX_SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "mlx_vlm_server.py")


def _find_mlx_python() -> Optional[str]:
    """Find the Python interpreter with mlx_vlm installed."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "mlx-venv", "bin", "python3"),
        os.path.expanduser("~/.local/bin/python3.11"),
    ]
    for c in candidates:
        c = os.path.abspath(c)
        if os.path.exists(c):
            return c
    return sys.executable


_mlx_server_process: Optional[subprocess.Popen] = None


async def mlx_start_server(
    model: str = MLX_DEFAULT_MODEL,
    port: int = MLX_DEFAULT_PORT,
    host: str = MLX_SERVER_HOST,
    max_tokens: int = 4096,
) -> Dict:
    """Start the custom MLX VLM server as a subprocess."""
    global _mlx_server_process

    if _mlx_server_process and _mlx_server_process.poll() is None:
        return {
            "status": "already_running",
            "port": port,
            "model": model,
            "pid": _mlx_server_process.pid,
        }

    if is_mlx_server_running(port):
        return {
            "status": "port_in_use",
            "port": port,
            "message": f"Port {port} is already in use. Another MLX server may be running.",
        }

    if not is_mlx_available():
        return {
            "status": "error",
            "message": "mlx-lm is not installed. Install with: pip install mlx-lm",
        }

    mlx_python = _find_mlx_python()
    model_path = resolve_mlx_model_path(model)
    server_script = _MLX_SERVER_SCRIPT

    if not os.path.exists(server_script):
        return {
            "status": "error",
            "message": f"Server script not found: {server_script}",
        }

    cmd = [
        mlx_python, server_script,
        "--model", model_path,
        "--host", host,
        "--port", str(port),
    ]

    try:
        _mlx_server_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        # Wait for server to be ready
        start = time.time()
        timeout = 300  # 5 min for model download + load
        while time.time() - start < timeout:
            if _mlx_server_process.poll() is not None:
                stderr = _mlx_server_process.stderr.read().decode() if _mlx_server_process.stderr else ""
                return {
                    "status": "error",
                    "message": f"Server exited prematurely: {stderr[:500]}",
                }
            if is_mlx_server_running(port):
                try:
                    async with make_async_client() as client:
                        r = await client.get(f"http://{host}:{port}/health", timeout=3.0)
                        if r.status_code == 200:
                            data = r.json()
                            return {
                                "status": "started",
                                "port": port,
                                "model": model,
                                "pid": _mlx_server_process.pid,
                                "model_loaded": data.get("model_loaded", False),
                                "elapsed_sec": round(time.time() - start, 1),
                            }
                except Exception:
                    pass
            await asyncio.sleep(1)

        return {
            "status": "timeout",
            "message": f"Server did not become ready within {timeout}s",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }


def mlx_stop_server() -> Dict:
    """Stop the MLX LM server subprocess."""
    global _mlx_server_process
    if _mlx_server_process and _mlx_server_process.poll() is None:
        try:
            _mlx_server_process.terminate()
            try:
                _mlx_server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _mlx_server_process.kill()
                _mlx_server_process.wait(timeout=3)
            pid = _mlx_server_process.pid
            _mlx_server_process = None
            return {"status": "stopped", "pid": pid}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "not_running"}


def mlx_get_server_status() -> Dict:
    """Get status of the MLX LM server."""
    global _mlx_server_process
    if _mlx_server_process and _mlx_server_process.poll() is None:
        return {
            "status": "running",
            "pid": _mlx_server_process.pid,
            "port": MLX_DEFAULT_PORT,
        }
    running = is_mlx_server_running()
    return {
        "status": "running" if running else "stopped",
        "port": MLX_DEFAULT_PORT if running else None,
    }


# ---------------------------------------------------------------------------
# Model Cache
# ---------------------------------------------------------------------------

_mlx_model_cache: dict = {}

def _clear_model_cache():
    """Clear the loaded model cache."""
    global _mlx_model_cache
    _mlx_model_cache = {}


def _is_vlm_model(model_id: str) -> bool:
    """Check if a model ID likely requires mlx_vlm (Gemma 4 is always VLM)."""
    hf_path = resolve_mlx_model_path(model_id).lower()
    vlm_keywords = ["gemma-4", "gemma4", "vlm", "vision"]
    return any(kw in hf_path for kw in vlm_keywords)


# ---------------------------------------------------------------------------
# Direct MLX Inference
# ---------------------------------------------------------------------------

def _mlx_generate_sync(
    model_id: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 500,
    temperature: float = 0.3,
    top_p: float = 0.95,
) -> Tuple[str, Dict]:
    """Synchronous MLX inference. Runs in thread pool to not block event loop."""
    hf_path = resolve_mlx_model_path(model_id)
    use_vlm = _is_vlm_model(model_id)

    import os
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    if use_vlm:
        from mlx_vlm import load, generate
        model, processor = load(hf_path)
        tokenizer = processor.tokenizer
    else:
        from mlx_lm import load, generate
        model, tokenizer = load(hf_path)

    if system or use_vlm:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if use_vlm:
            messages.append({"role": "user", "content": [{"type": "text", "text": prompt}]})
        else:
            messages.append({"role": "user", "content": prompt})
        prompt_text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    else:
        prompt_text = prompt

    start = time.time()
    if use_vlm:
        result = generate(
            model, processor,
            prompt=prompt_text,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        response = result.text if hasattr(result, 'text') else str(result)
        output_tokens = result.generation_tokens if hasattr(result, 'generation_tokens') else len(tokenizer.encode(response))
    else:
        response = generate(
            model, tokenizer,
            prompt=prompt_text,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        output_tokens = len(tokenizer.encode(response))

    elapsed = time.time() - start
    input_tokens = len(tokenizer.encode(prompt_text))

    usage = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "tokens_per_sec": round(output_tokens / elapsed, 2) if elapsed > 0 else 0,
        "elapsed_sec": round(elapsed, 2),
    }

    return response, usage


async def mlx_generate(
    prompt: str,
    system: Optional[str] = None,
    *,
    model: str = MLX_DEFAULT_MODEL,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> Tuple[str, Dict]:
    """Async wrapper around MLX direct inference. Runs in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _mlx_generate_sync,
        model,
        prompt,
        system,
        max_tokens,
        temperature,
    )


# ---------------------------------------------------------------------------
# MLX Server API Client (OpenAI-compatible)
# ---------------------------------------------------------------------------

async def mlx_server_chat(
    messages: List[Dict],
    *,
    model: str = MLX_DEFAULT_MODEL,
    max_tokens: int = 500,
    temperature: float = 0.3,
    port: int = MLX_DEFAULT_PORT,
    timeout: float = 60.0,
) -> Tuple[str, Dict]:
    """Send a chat completion request to the MLX server (OpenAI-compatible API)."""
    hf_path = resolve_mlx_model_path(model)
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    
    payload = {
        "model": hf_path,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    
    async with make_async_client() as client:
        resp = await client.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"MLX server error {resp.status_code}: {resp.text[:200]}")
        
        data = resp.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        
        return text, usage


# ---------------------------------------------------------------------------
# Model Download
# ---------------------------------------------------------------------------

async def mlx_download_model(model_id: str) -> Dict:
    """Download an MLX model from HuggingFace Hub."""
    hf_path = resolve_mlx_model_path(model_id)
    
    try:
        from huggingface_hub import snapshot_download
        
        loop = asyncio.get_event_loop()
        local_path = await loop.run_in_executor(
            None,
            lambda: snapshot_download(
                repo_id=hf_path,
                allow_patterns=["*.safetensors", "*.json", "*.py", "tokenizer*"],
            ),
        )
        
        return {
            "status": "downloaded",
            "model_id": model_id,
            "hf_path": hf_path,
            "local_path": local_path,
        }
    except Exception as e:
        return {
            "status": "error",
            "model_id": model_id,
            "message": str(e),
        }


async def mlx_list_cached_models() -> List[Dict]:
    """List all locally cached MLX models."""
    return find_local_mlx_models()


# ---------------------------------------------------------------------------
# Provider info
# ---------------------------------------------------------------------------

def get_provider_info() -> Dict:
    """Get comprehensive MLX provider status."""
    available = is_mlx_available()
    server_running = is_mlx_server_running()
    server_status = mlx_get_server_status()
    
    result = {
        "available": available,
        "server_running": server_running,
        "server_status": server_status,
        "models": get_available_mlx_models(),
        "local_models": find_local_mlx_models(),
        "default_model": MLX_DEFAULT_MODEL,
        "version": "",
    }
    
    if available:
        try:
            import mlx_lm
            result["version"] = getattr(mlx_lm, "__version__", "unknown")
        except Exception:
            pass
    
    return result
