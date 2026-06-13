"""Allow running as: python3 -m tools.mcp.server"""
import asyncio
from .server import main

if __name__ == "__main__":
    asyncio.run(main())
