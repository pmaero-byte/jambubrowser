"""WebSocket endpoints for real-time agent state and audit log updates."""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("jambu.ws")

from backend.core.audit import get_audit_logger
from backend.engine_runtime import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    accepted = await manager.connect(client_id, websocket)
    if not accepted:
        try:
            await websocket.close(code=1008, reason="connection rejected")
        except Exception:
            pass
        return
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(client_id)


@router.websocket("/ws/audit")
async def audit_websocket(websocket: WebSocket):
    """WebSocket endpoint for live audit log updates."""
    accepted = await manager.connect("__audit__", websocket)
    if not accepted:
        try:
            await websocket.close(code=1008, reason="connection rejected")
        except Exception:
            pass
        return
    try:
        # Send current audit stats
        audit_logger = get_audit_logger()
        stats = audit_logger.get_statistics()
        await websocket.send_json({"type": "stats", "data": stats})

        # Keep connection alive and send periodic updates
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send updated stats every 30 seconds
                stats = audit_logger.get_statistics()
                await websocket.send_json({"type": "stats", "data": stats})
    except Exception as e:
        log.warning("[ws] audit connection closed: %s", e)
    finally:
        manager.disconnect("__audit__")
