"""
MLX VLM OpenAI-Compatible Server
=================================
Custom FastAPI server that wraps mlx_vlm for Gemma 4 and other VLM models.
Exposes OpenAI-compatible chat completions endpoint.

Usage:
    python3 backend/scripts/mlx_vlm_server.py --model mlx-community/gemma-4-12B-it-4bit --port 8080
"""

import argparse
import json
import os
import time
from typing import Optional, List, Dict

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

os.environ["TOKENIZERS_PARALLELISM"] = "false"

app = FastAPI(title="MLX VLM Server")

_model = None
_processor = None
_model_path = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    max_tokens: int = 500
    temperature: float = 0.3
    top_p: float = 0.95
    stream: bool = False


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict]
    usage: UsageInfo


def load_model(model_path: str):
    """Load the VLM model."""
    global _model, _processor, _model_path

    from mlx_vlm import load
    print(f"[MLX] Loading model: {model_path}")
    start = time.time()
    _model, _processor = load(model_path)
    _model_path = model_path
    elapsed = time.time() - start
    mem = _get_memory_usage()
    print(f"[MLX] Model loaded in {elapsed:.1f}s | Peak memory: {mem:.1f} GB")


def _get_memory_usage() -> float:
    """Get current memory usage."""
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / (1024 ** 3)
    except Exception:
        return 0.0


@app.get("/v1/models")
async def list_models():
    """List available models."""
    return {
        "object": "list",
        "data": [
            {
                "id": _model_path or "unknown",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mlx-vlm",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """OpenAI-compatible chat completion endpoint."""
    global _model, _processor

    if _model is None or _processor is None:
        raise HTTPException(status_code=500, detail="Model not loaded")

    from mlx_vlm import generate

    # Build messages in VLM format
    messages = []
    for msg in req.messages:
        if msg.role == "system":
            messages.append({"role": "system", "content": msg.content})
        elif msg.role == "user":
            messages.append({"role": "user", "content": [{"type": "text", "text": msg.content}]})
        elif msg.role == "assistant":
            messages.append({"role": "assistant", "content": [{"type": "text", "text": msg.content}]})

    # Apply chat template
    prompt = _processor.tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    # Generate
    start = time.time()
    result = generate(
        _model, _processor,
        prompt=prompt,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
    )
    elapsed = time.time() - start

    response_text = result.text if hasattr(result, 'text') else str(result)
    completion_tokens = result.generation_tokens if hasattr(result, 'generation_tokens') else len(
        _processor.tokenizer.encode(response_text)
    )
    prompt_tokens = len(_processor.tokenizer.encode(prompt))

    response = ChatCompletionResponse(
        id=f"chatcmpl-{int(time.time())}",
        created=int(time.time()),
        model=req.model,
        choices=[
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        usage=UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )

    return response


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "model": _model_path,
        "memory_gb": round(_get_memory_usage(), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="MLX VLM OpenAI-Compatible Server")
    parser.add_argument("--model", default="mlx-community/gemma-4-12B-it-4bit",
                        help="HuggingFace model path (default: mlx-community/gemma-4-12B-it-4bit)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8080, help="Bind port")
    parser.add_argument("--preload", action="store_true", default=True,
                        help="Preload model on startup")
    args = parser.parse_args()

    if args.preload:
        load_model(args.model)

    print(f"[MLX] Server starting on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
