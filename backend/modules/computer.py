"""
Screen-level computer control (macOS only).

Mouse control uses Quartz CoreGraphics events (pyobjc), which drive the
real cursor without extra binaries. Keyboard input is text via osascript
(handled in the routes) or named key presses via this module.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Dict

# macOS virtual key codes for the named keys the API accepts.
KEY_CODES: Dict[str, int] = {
    "return": 36,
    "enter": 76,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "escape": 53,
    "cmd": 55,
    "shift": 56,
    "alt": 58,
    "ctrl": 59,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
    "f5": 96,
}


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Computer control is only available on macOS")


def _load_quartz():
    try:
        import Quartz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Mouse control requires pyobjc "
            "(pip install pyobjc-framework-Quartz)"
        ) from exc
    return Quartz


async def mouse_action(action: str, x: int, y: int, button: str = "left") -> dict:
    """Move/click/drag the real mouse cursor.

    Runs the blocking Quartz calls in a worker thread so the FastAPI
    event loop stays responsive.
    """
    return await asyncio.to_thread(_mouse_action_sync, action, x, y, button)


def _mouse_action_sync(action: str, x: int, y: int, button: str) -> dict:
    _require_macos()
    Quartz = _load_quartz()

    right = button == "right"
    point = Quartz.CGPointMake(float(x), float(y))
    button_type = Quartz.kCGMouseButtonRight if right else Quartz.kCGMouseButtonLeft

    action = (action or "click").lower()
    if action in ("move", "mousemove"):
        _post(Quartz, Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, point, button_type))
    elif action == "click":
        _post(Quartz, Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventRightMouseDown if right else Quartz.kCGEventLeftMouseDown,
            point, button_type))
        _post(Quartz, Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventRightMouseUp if right else Quartz.kCGEventLeftMouseUp,
            point, button_type))
    elif action == "down":
        _post(Quartz, Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventRightMouseDown if right else Quartz.kCGEventLeftMouseDown,
            point, button_type))
    elif action == "up":
        _post(Quartz, Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventRightMouseUp if right else Quartz.kCGEventLeftMouseUp,
            point, button_type))
    elif action == "drag":
        _post(Quartz, Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventRightMouseDragged if right else Quartz.kCGEventLeftMouseDragged,
            point, button_type))
    else:
        raise ValueError(f"Unsupported mouse action: {action}")

    return {"status": "ok", "action": action, "x": x, "y": y, "button": button}


def _post(Quartz, event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


async def press_key(key: str) -> dict:
    """Press a named key (see KEY_CODES) via AppleScript."""
    _require_macos()
    code = KEY_CODES.get((key or "").lower())
    if code is None:
        raise ValueError(f"Unsupported key: {key}")
    subprocess.run(
        ["osascript", "-e", f'tell application "System Events" to key code {code}'],
        capture_output=True, timeout=5, check=True,
    )
    return {"status": "ok", "key": key, "code": code}
