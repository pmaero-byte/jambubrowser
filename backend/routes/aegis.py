"""AEGIS evolution surface: read-only view of persisted harness configs.

The evolution pipeline (backend/agent/evolution.py, ~1k LOC) mutates
HarnessConfig objects and persists them through HarnessConfigStore, but
until now an operator had no way to see what it had produced. This route
is deliberately read-only: evolving a config changes what the agent does,
so mutation stays behind the library API until it is stable enough to
expose.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(tags=["agent"])


@router.get("/agent/aegis/configs")
async def aegis_configs(tag: str = "", limit: int = Query(20, ge=1, le=100)):
    """Newest persisted harness configs, optionally filtered by tag."""
    from backend.agent.harness import get_config_store

    configs = get_config_store().list_configs(tag=tag or None, limit=limit)
    return {
        "count": len(configs),
        "configs": [
            {
                "config_id": c.config_id[:12],
                "description": c.description,
                "evolution_round": c.evolution_round,
                "success_rate": c.success_rate,
                "tags": c.tags,
                "created_at": c.created_at,
            }
            for c in configs
        ],
    }


@router.get("/agent/aegis/configs/latest")
async def aegis_latest(tag: str = ""):
    """The config the evolution pipeline currently considers its best."""
    from backend.agent.harness import get_config_store

    config = get_config_store().load_latest(tag=tag or None)
    if config is None:
        return {"config": None}
    data = config.to_dict()
    return {"config": {**data, "config_id": config.config_id}}
