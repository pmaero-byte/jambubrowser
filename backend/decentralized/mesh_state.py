"""
Shared on-disk state locations for the mesh (consensus + federation).
============================================================================

The consensus store and the federation registry used to hard-code
``Path.home() / ".jambubrowser"`` in two different modules. That made two
nodes on one machine impossible to run side by side — they would read and
write the same ``consensus.json`` / ``federation.json``, so a "broadcast"
between them was invisible (both processes shared one store) and any local
experiment contaminated the developer's real state.

``JAMBU_MESH_STATE_DIR`` overrides the directory; the default is unchanged,
so existing installs keep their state where it is.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("jambu.mesh_state")


def state_dir() -> Path:
    """Directory holding ``consensus.json`` and ``federation.json``."""
    override = os.environ.get("JAMBU_MESH_STATE_DIR")
    return Path(override) if override else Path.home() / ".jambubrowser"


def consensus_store_path() -> Path:
    """Path of the consensus proposal store for this node."""
    return state_dir() / "consensus.json"


def federation_path() -> Path:
    """Path of the federation registry (node_id -> node_url) for this node."""
    return state_dir() / "federation.json"


def read_federation() -> Dict[str, dict]:
    """Return ``{node_id: {"node_url": ..., "public_key": ...?}}``.

    An absent or unreadable file yields ``{}`` — a node with no registry is
    a single-node mesh, which is a valid state, not an error.
    """
    path = federation_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("federation registry %s unreadable (%s); treating as empty",
                    path, exc)
        return {}
    nodes = data.get("nodes")
    return dict(nodes) if isinstance(nodes, dict) else {}


def write_federation(nodes: Dict[str, dict]) -> None:
    """Atomically replace the federation registry with ``nodes``.

    Write-to-temp-then-rename so a crash mid-write cannot leave a node with
    a half-parsed registry (the consensus broadcaster would then silently
    broadcast to nobody).
    """
    path = federation_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Optional[Path] = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"nodes": nodes}, indent=2), encoding="utf-8")
    tmp.replace(path)
