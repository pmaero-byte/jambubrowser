"""
Jambubrowser - Entry Point (v2.0 Consolidated)
==============================================
Thin wrapper that delegates to the modular backend engine.
All endpoint logic, modules, and agents live in backend/.

Run with: python engine.py
"""

from backend.engine import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
