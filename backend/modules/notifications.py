"""
Desktop Notification System
============================
Cross-platform desktop notifications for mission alerts,
security warnings, and system events. macOS-first with
fallback to terminal output.

Supports:
- Native macOS notifications via osascript
- Urgency levels
- Clickable notifications with actions
- Notification history in database
"""

import os
import sys
import subprocess
import json
import time
import hashlib
import asyncio
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from enum import Enum

from backend.core.database import get_db_cursor


class Urgency(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Notification:
    id: str
    title: str
    message: str
    urgency: Urgency = Urgency.NORMAL
    category: str = "general"
    action_url: str = ""
    action_label: str = ""
    timestamp: float = field(default_factory=time.time)
    delivered: bool = False

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'title': self.title, 'message': self.message,
            'urgency': self.urgency.value, 'category': self.category,
            'action_url': self.action_url, 'timestamp': self.timestamp,
            'delivered': self.delivered,
        }


class Notifier:
    """
    Cross-platform desktop notification dispatcher.
    macOS: uses osascript for native notifications
    Linux: uses notify-send
    Fallback: prints to console
    """

    def __init__(self, app_name: str = "Jambubrowser"):
        self.app_name = app_name
        self._history: List[Notification] = []
        self._max_history = 100

    def _is_macos(self) -> bool:
        return sys.platform == 'darwin'

    def _is_linux(self) -> bool:
        return sys.platform.startswith('linux')

    def _send_macos(self, notification: Notification) -> bool:
        try:
            title = notification.title.replace('"', '\\"')
            message = notification.message.replace('"', '\\"')
            subtitle = f"[{notification.urgency.value.upper()}] {notification.category}"

            script = f'display notification "{message}" with title "{title}" subtitle "{subtitle}" sound name "default"'

            result = subprocess.run(
                ['osascript', '-e', script], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _send_linux(self, notification: Notification) -> bool:
        try:
            urgency_map = {Urgency.LOW: 'low', Urgency.NORMAL: 'normal', Urgency.HIGH: 'critical', Urgency.CRITICAL: 'critical'}
            subprocess.run(
                ['notify-send', '-u', urgency_map.get(notification.urgency, 'normal'),
                 '-a', self.app_name, notification.title, notification.message],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    def _send_terminal(self, notification: Notification):
        prefix_map = {Urgency.LOW: '🔵', Urgency.NORMAL: '📢', Urgency.HIGH: '⚠️', Urgency.CRITICAL: '🚨'}
        prefix = prefix_map.get(notification.urgency, '📢')
        print(f"\n{prefix} [{notification.category}] {notification.title}")
        print(f"   {notification.message}")
        if notification.action_url:
            print(f"   Action: {notification.action_label} → {notification.action_url}")

    async def send(self, title: str, message: str, urgency: Urgency = Urgency.NORMAL,
                   category: str = "general", action_url: str = "",
                   action_label: str = "", persist: bool = True) -> Notification:
        nid = hashlib.md5(f"{title}{message}{time.time()}".encode()).hexdigest()[:12]

        notification = Notification(id=nid, title=title, message=message, urgency=urgency,
                                    category=category, action_url=action_url, action_label=action_label)

        success = False
        if self._is_macos():
            success = self._send_macos(notification)
        elif self._is_linux():
            success = self._send_linux(notification)

        if not success:
            self._send_terminal(notification)

        notification.delivered = True

        self._history.append(notification)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        if persist:
            try:
                with get_db_cursor() as cursor:
                    cursor.execute(
                        """CREATE TABLE IF NOT EXISTS notification_history (
                            id TEXT PRIMARY KEY, title TEXT, message TEXT,
                            urgency TEXT, category TEXT, action_url TEXT,
                            timestamp REAL, delivered INTEGER
                        )"""
                    )
                    cursor.execute(
                        """INSERT OR IGNORE INTO notification_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (nid, title, message, urgency.value, category,
                         action_url, notification.timestamp, int(success)),
                    )
            except Exception:
                pass

        return notification

    async def send_mission_alert(self, mission_name: str, finding: str, url: str = ""):
        message = finding[:200] + ('...' if len(finding) > 200 else '')
        return await self.send(title=f"Mission Finding: {mission_name}", message=message,
                               urgency=Urgency.NORMAL, category="mission",
                               action_url=url, action_label="Open Source")

    async def send_security_alert(self, url: str, risk_type: str, details: str):
        return await self.send(title=f"Security Alert: {risk_type}",
                               message=f"{details}\nBlocked URL: {url}",
                               urgency=Urgency.HIGH, category="security")

    async def send_system_notification(self, title: str, message: str):
        return await self.send(title=title, message=message, urgency=Urgency.LOW, category="system")

    def get_history(self, category: str = None, limit: int = 50) -> List[dict]:
        filtered = self._history
        if category:
            filtered = [n for n in filtered if n.category == category]
        return [n.to_dict() for n in filtered[-limit:]]


_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier


async def send_notification(title: str, message: str, urgency: str = "normal",
                            category: str = "general", action_url: str = "") -> Notification:
    urgency_map = {'low': Urgency.LOW, 'normal': Urgency.NORMAL, 'high': Urgency.HIGH, 'critical': Urgency.CRITICAL}
    notifier = get_notifier()
    return await notifier.send(title=title, message=message,
                               urgency=urgency_map.get(urgency, Urgency.NORMAL),
                               category=category, action_url=action_url)
