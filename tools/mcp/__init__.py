"""tools/mcp — MCP tooling.

The canonical stdio MCP server is ``backend/mcp_server.py`` (52 tools, fully
wired into the engine, with generated docs and a drift test). The older
standalone bridge that used to live here as ``tools/mcp/server.py`` was
retired: it duplicated the tool surface, was untested, and drifted out of
sync. Only the docs generator (``tools/mcp/generate_docs.py``) remains
authoritative in this package.
"""
