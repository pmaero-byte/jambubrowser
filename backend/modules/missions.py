"""
Mission Scheduler & Autonomous Monitor
=======================================
Persistent background research missions with cron scheduling,
trigger conditions, deduplication, and priority management.

Replaces the simple mission_monitor() loop in engine.py with
a full-featured scheduler.
"""

import asyncio
import time
import hashlib
import json
import re
from typing import Optional, List, Dict, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.core.database import get_db_cursor


# ---- Cron Expression Parser ----

CRON_PATTERN = re.compile(
    r'^(\*(/\d+)?|\d+(-\d+)?(/\d+)?)(\s+(\*(/\d+)?|\d+(-\d+)?(/\d+)?)){4}$'
)

CRON_MINUTE = 0
CRON_HOUR = 1
CRON_DAY = 2
CRON_MONTH = 3
CRON_DOW = 4


def _parse_cron_field(field: str, min_val: int, max_val: int) -> set:
    """Parse a single cron field into a set of valid values."""
    if field == '*':
        return set(range(min_val, max_val + 1))

    result = set()
    parts = field.split(',')
    for part in parts:
        step = 1
        if '/' in part:
            part, step_str = part.split('/')
            step = int(step_str)

        if part == '*':
            for v in range(min_val, max_val + 1, step):
                result.add(v)
        elif '-' in part:
            start, end = part.split('-')
            for v in range(int(start), int(end) + 1, step):
                if min_val <= v <= max_val:
                    result.add(v)
        else:
            v = int(part)
            if min_val <= v <= max_val:
                result.add(v)
    
    return result


def parse_cron(cron_expr: str) -> Optional[Dict[str, set]]:
    """
    Parse a 5-field cron expression into sets of valid values.

    Args:
        cron_expr: e.g. "0 */6 * * *" (every 6 hours)

    Returns:
        Dict with keys: minute, hour, day, month, dow
        Returns None if invalid.
    """
    if not cron_expr or cron_expr == "none":
        return None

    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return None

    try:
        return {
            'minute': _parse_cron_field(fields[0], 0, 59),
            'hour': _parse_cron_field(fields[1], 0, 23),
            'day': _parse_cron_field(fields[2], 1, 31),
            'month': _parse_cron_field(fields[3], 1, 12),
            'dow': _parse_cron_field(fields[4], 0, 7),  # 0 and 7 both mean Sunday
        }
    except (ValueError, IndexError):
        return None


def get_next_run(cron_expr: str, from_time: float = None) -> Optional[float]:
    """
    Calculate the next run time for a cron expression.

    Args:
        cron_expr: 5-field cron expression
        from_time: Base timestamp (default: now)

    Returns:
        Unix timestamp of next run, or None if invalid/never.
    """
    if not cron_expr or cron_expr == "none":
        return None

    parsed = parse_cron(cron_expr)
    if not parsed:
        return None

    now = datetime.fromtimestamp(from_time or time.time())
    current = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Search forward up to 2 years (safety limit)
    for _ in range(365 * 24 * 60 * 2):
        if (current.minute in parsed['minute']
                and current.hour in parsed['hour']
                and current.day in parsed['day']
                and current.month in parsed['month']
                and (current.weekday() + 1) % 7 in {d % 7 for d in parsed['dow']}):
            return current.timestamp()

        current += timedelta(minutes=1)

    return None


# ---- Trigger Conditions ----

@dataclass
class TriggerCondition:
    """A condition that triggers mission execution."""
    field: str  # 'source_domain', 'keyword', 'sentiment'
    operator: str  # 'contains', 'matches', 'equals', 'gt', 'lt'
    value: str

    def evaluate(self, context: dict) -> bool:
        """Evaluate this condition against a context dict."""
        if self.field not in context:
            return False

        context_val = str(context[self.field]).lower()
        target_val = self.value.lower()

        if self.operator == 'contains':
            return target_val in context_val
        elif self.operator == 'matches':
            return re.search(target_val, context_val) is not None
        elif self.operator == 'equals':
            return context_val == target_val
        elif self.operator == 'gt':
            try:
                return float(context_val) > float(target_val)
            except (ValueError, TypeError):
                return False
        elif self.operator == 'lt':
            try:
                return float(context_val) < float(target_val)
            except (ValueError, TypeError):
                return False

        return False


def parse_trigger_conditions(conditions_json: str) -> List[TriggerCondition]:
    """Parse JSON trigger conditions string into TriggerCondition objects."""
    if not conditions_json:
        return []

    try:
        raw = json.loads(conditions_json)
        if isinstance(raw, list):
            return [
                TriggerCondition(
                    field=c.get('field', ''),
                    operator=c.get('operator', 'contains'),
                    value=c.get('value', ''),
                )
                for c in raw if 'field' in c
            ]
    except (json.JSONDecodeError, TypeError):
        pass

    return []


# ---- Mission ----

@dataclass
class Mission:
    """A persistent autonomous research mission."""
    id: str
    query: str
    status: str = 'active'
    priority: int = 1  # 1=low, 2=medium, 3=high, 4=critical
    schedule: str = 'none'  # Cron expression
    trigger_conditions: List[TriggerCondition] = field(default_factory=list)
    last_run: float = 0
    next_run: float = 0
    run_count: int = 0
    success_count: int = 0
    last_result_hash: str = ''
    created_at: float = field(default_factory=time.time)
    callback: Optional[Callable[..., Awaitable]] = None

    def should_run_now(self) -> bool:
        """Check if this mission is due for execution."""
        if self.status != 'active':
            return False
        if self.next_run and time.time() >= self.next_run:
            return True
        if not self.next_run and not self.last_run:
            return True  # Never run before
        return False

    def record_result(self, result_text: str, success: bool = True):
        """Update mission state after a run."""
        self.last_run = time.time()
        self.run_count += 1
        if success:
            self.success_count += 1
        self.last_result_hash = hashlib.md5(
            result_text[:500].encode()
        ).hexdigest()
        
        # Calculate next run from schedule
        if self.schedule and self.schedule != "none":
            self.next_run = get_next_run(self.schedule, self.last_run)

    def is_duplicate_result(self, result_text: str) -> bool:
        """Check if this result is identical to the last one."""
        new_hash = hashlib.md5(result_text[:500].encode()).hexdigest()
        return new_hash == self.last_result_hash

    def check_triggers(self, context: dict) -> bool:
        """Check if any trigger conditions are met by the context."""
        if not self.trigger_conditions:
            return True  # No conditions = always trigger
        return any(c.evaluate(context) for c in self.trigger_conditions)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'query': self.query,
            'status': self.status,
            'priority': self.priority,
            'schedule': self.schedule,
            'last_run': self.last_run,
            'next_run': self.next_run,
            'run_count': self.run_count,
            'success_count': self.success_count,
            'created_at': self.created_at,
        }


# ---- Mission Scheduler ----

class MissionScheduler:
    """
    Manages and executes autonomous background research missions.
    Supports cron scheduling, trigger conditions, deduplication,
    and priority-based execution.
    """

    DEFAULT_CHECK_INTERVAL = 60  # seconds

    def __init__(self, check_interval: int = None):
        self._missions: Dict[str, Mission] = {}
        self._check_interval = check_interval or self.DEFAULT_CHECK_INTERVAL
        self._running = False
        self._lock = asyncio.Lock()
        self._on_mission_complete: Optional[Callable] = None
        self._on_new_finding: Optional[Callable] = None
        self._research_fn: Optional[Callable] = None

    def set_research_handler(self, handler: Callable):
        """Set the async function that performs research for missions."""
        self._research_fn = handler

    def set_notification_handler(self, on_complete: Callable = None, on_finding: Callable = None):
        """Set callbacks for notifications."""
        self._on_mission_complete = on_complete
        self._on_new_finding = on_finding

    async def load_from_db(self):
        """Load all active missions from the database."""
        async with self._lock:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT id, query, status, last_run, next_run, schedule FROM missions"
                )
                rows = cursor.fetchall()
                self._missions = {}
                for row in rows:
                    mission = Mission(
                        id=row['id'],
                        query=row['query'],
                        status=row['status'] or 'active',
                        schedule=row['schedule'] or 'none',
                        last_run=row['last_run'] or 0,
                        next_run=row['next_run'] or 0,
                    )
                    self._missions[mission.id] = mission

        return len(self._missions)

    async def add_mission(
        self,
        query: str,
        schedule: str = None,
        priority: int = 1,
        trigger_conditions: str = None,
        mission_id: str = None,
    ) -> Mission:
        """Add a new mission to the scheduler."""
        mid = mission_id or hashlib.md5(
            f"{query}_{time.time()}".encode()
        ).hexdigest()[:12]

        conditions = parse_trigger_conditions(trigger_conditions) if trigger_conditions else []

        mission = Mission(
            id=mid,
            query=query,
            schedule=schedule or 'none',
            priority=priority,
            trigger_conditions=conditions,
        )

        if schedule and schedule != "none":
            mission.next_run = get_next_run(schedule)

        # Persist to DB
        with get_db_cursor() as cursor:
            cursor.execute(
                """INSERT OR REPLACE INTO missions 
                   (id, query, status, last_run, next_run, schedule) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mid, query, 'active', mission.last_run, mission.next_run, schedule),
            )

        async with self._lock:
            self._missions[mid] = mission

        return mission

    async def stop_mission(self, mission_id: str) -> bool:
        """Stop a running mission."""
        async with self._lock:
            if mission_id in self._missions:
                self._missions[mission_id].status = 'stopped'
                with get_db_cursor() as cursor:
                    cursor.execute(
                        "UPDATE missions SET status = 'stopped' WHERE id = ?",
                        (mission_id,),
                    )
                return True
        return False

    async def remove_mission(self, mission_id: str) -> bool:
        """Remove a mission entirely."""
        async with self._lock:
            if mission_id in self._missions:
                del self._missions[mission_id]
                with get_db_cursor() as cursor:
                    cursor.execute("DELETE FROM missions WHERE id = ?", (mission_id,))
                return True
        return False

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        """Get a mission by ID."""
        return self._missions.get(mission_id)

    def list_missions(self, status: str = None) -> List[dict]:
        """List all missions, optionally filtered by status."""
        result = []
        for m in self._missions.values():
            if status and m.status != status:
                continue
            result.append(m.to_dict())
        # Sort by priority (descending)
        result.sort(key=lambda x: x['priority'], reverse=True)
        return result

    async def run_loop(self):
        """
        Main scheduler loop. Checks for due missions and executes them.
        Runs indefinitely as a background task.
        """
        self._running = True

        while self._running:
            try:
                due_missions = []

                async with self._lock:
                    for mission in self._missions.values():
                        if mission.should_run_now():
                            due_missions.append(mission)

                # Sort by priority (highest first)
                due_missions.sort(key=lambda m: m.priority, reverse=True)

                for mission in due_missions:
                    await self._execute_mission(mission)

            except Exception as e:
                print(f"MissionScheduler error: {e}")

            await asyncio.sleep(self._check_interval)

    async def _execute_mission(self, mission: Mission):
        """Execute a single mission's research task."""
        if not self._research_fn:
            mission.status = 'error'
            return

        try:
            result = await self._research_fn(mission.query)

            # Check for duplicate results
            if mission.is_duplicate_result(result):
                mission.record_result(result)
                return

            # New finding detected
            mission.record_result(result)

            # Notify about new findings
            if self._on_new_finding:
                try:
                    await self._on_new_finding(mission, result)
                except Exception:
                    pass

            # Notify mission complete
            if self._on_mission_complete:
                try:
                    await self._on_mission_complete(mission, result)
                except Exception:
                    pass

        except Exception as e:
            mission.record_result(f"Error: {e}", success=False)

    def stop(self):
        """Stop the scheduler loop."""
        self._running = False


# ---- Module-level singleton ----

_scheduler: Optional[MissionScheduler] = None


def get_scheduler() -> MissionScheduler:
    """Get or create the singleton mission scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = MissionScheduler()
    return _scheduler
