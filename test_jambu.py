import asyncio
import httpx
import sys
import os

# Set PYTHONPATH to find backend modules
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Re-implementing tests for the rebranded Jambubrowser
async def test_research():
    print("\n--- Testing Jambu Swarm Research ---")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post("http://localhost:8001/research", json={
                "query": "Future of Jambubrowser and Sovereign AI 2026",
                "top_n": 1,
                "client_id": "test_script"
            }, timeout=60.0)
            print(f"Status: {resp.status_code}")
            print(f"Result: {resp.json().get('summary', 'No summary')}")
        except Exception as e:
            print(f"Error: {e}")

async def test_memory():
    print("\n--- Testing Jambu Semantic Memory ---")
    async with httpx.AsyncClient() as client:
        try:
            # Note: Port 8001 was our engine port
            resp = await client.get("http://localhost:8001/health")
            print(f"Engine Health: {resp.json()}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_research())
    asyncio.run(test_memory())
