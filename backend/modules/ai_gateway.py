"""
AI Language Model Gateway
=========================
This module is the 'Translator' between our code and the AI.
It handles asking questions to the AI and getting back understandable 
answers. It can talk to both local models (Gemma) and cloud models (OpenAI).
"""

import httpx
from typing import List, Dict

try:
    from backend.core.socks import make_async_client
except ImportError:
    make_async_client = httpx.AsyncClient

async def ask_ai(prompt: str, config: Dict, system_msg: str = "You are a helpful assistant.") -> str:
    """
    Sends a message to the AI and returns the text response.
    - prompt: What you want to ask.
    - config: The AI settings (Base URL, API Key, Model ID).
    - system_msg: Gives the AI its 'Personality'.
    """
    base_url = config.get("baseUrl", "http://localhost:8080/v1")
    model_id = config.get("modelId", "gemma-3-12b")
    api_key = config.get("apiKey", "")
    
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt}
        ],
        "temperature": config.get("temperature", 0.7)
    }
    
    async with make_async_client() as client:
        try:
            resp = await client.post(endpoint, headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            else:
                return f"AI Error: Received status {resp.status_code}"
        except Exception as e:
            return f"Network Error contacting AI: {str(e)}"

async def generate_hypothesis(query: str, config: Dict) -> str:
    """Predicts what we might find before we even start searching."""
    prompt = f"Briefly hypothesize what we will find for the query: '{query}'"
    return await ask_ai(prompt, config, "You are a research assistant.")
