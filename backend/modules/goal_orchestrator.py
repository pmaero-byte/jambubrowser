"""
Goal Orchestrator — Autonomous Goal-Oriented Agent
===================================================
Injects the browser's sovereign goal into every user query,
guides the local model toward goal achievement, tracks
approaches tried, and generates fallback strategies when
current approaches fail.

Architecture:
- Goal Manager: Define, persist, retrieve goals
- Goal Injector: Augments user queries with goal context
- Approach Tracker: Records what was tried, what worked, what failed
- Fallback Engine: Generates alternative strategies when stuck
- RAG Integration: All documentation feeds back into learning loop
"""

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from backend.core.database import get_db_cursor


# ---- Data Models ----

@dataclass
class Goal:
    """A sovereign goal for the browser to pursue."""
    id: str
    title: str
    description: str
    status: str = "active"  # active, achieved, blocked, abandoned
    priority: int = 3  # 1-5
    created_at: str = ""
    updated_at: str = ""
    success_criteria: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    approaches_tried: int = 0
    approaches_succeeded: int = 0


@dataclass
class Approach:
    """A single approach attempted toward a goal."""
    id: str
    goal_id: str
    iteration: int
    strategy: str  # Description of the approach
    hypothesis: str = ""  # Why this should work
    result: str = "pending"  # pending, success, falsified, partial
    evidence: str = ""  # What happened
    learning: str = ""  # What was learned
    next_target: str = ""  # What to try next
    started_at: str = ""
    completed_at: str = ""


class GoalOrchestrator:
    """
    Manages browser goals, guides the local model, tracks
    approaches, and generates fallback strategies.
    """

    GOALS_DIR = Path.home() / ".jambu" / "goals"

    def __init__(self):
        self.GOALS_DIR.mkdir(parents=True, exist_ok=True)
        self._active_goal: Optional[Goal] = None
        self._goal_context: str = ""

    # ---- Goal Management ----

    def set_goal(self, title: str, description: str,
                  success_criteria: List[str] = None,
                  constraints: List[str] = None,
                  priority: int = 3) -> Goal:
        """Set the browser's primary goal."""
        goal_id = hashlib.md5(f"{title}_{time.time()}".encode()).hexdigest()[:12]
        now = datetime.now().isoformat()

        goal = Goal(
            id=goal_id,
            title=title,
            description=description,
            status="active",
            priority=priority,
            created_at=now,
            updated_at=now,
            success_criteria=success_criteria or [],
            constraints=constraints or [],
        )

        self._active_goal = goal
        self._persist_goal(goal)
        self._build_goal_context()
        self._save_goal_markdown(goal)

        return goal

    def get_active_goal(self) -> Optional[Goal]:
        """Get the currently active goal."""
        if self._active_goal:
            return self._active_goal
        # Try loading from persistence
        return self._load_active_goal()

    def list_goals(self, status: str = None) -> List[Goal]:
        """List all goals, optionally filtered by status."""
        goals = []
        if self.GOALS_DIR.exists():
            for f in sorted(self.GOALS_DIR.glob("goal_*.json"), reverse=True):
                try:
                    data = json.loads(f.read_text())
                    goal = Goal(**data)
                    if status and goal.status != status:
                        continue
                    goals.append(goal)
                except Exception:
                    pass
        return goals

    def achieve_goal(self, goal_id: str = None) -> bool:
        """Mark a goal as achieved."""
        goal = self._find_goal(goal_id)
        if not goal:
            return False
        goal.status = "achieved"
        goal.updated_at = datetime.now().isoformat()
        self._persist_goal(goal)
        if self._active_goal and self._active_goal.id == goal.id:
            self._active_goal = None
            self._goal_context = ""
        return True

    def block_goal(self, goal_id: str = None, reason: str = "") -> bool:
        """Mark a goal as blocked with a reason."""
        goal = self._find_goal(goal_id)
        if not goal:
            return False
        goal.status = "blocked"
        goal.updated_at = datetime.now().isoformat()
        self._persist_goal(goal)
        return True

    # ---- Goal Injection ----

    def inject_goal_context(self, user_query: str) -> str:
        """
        Augment a user query with the active goal's context.
        This guides the local model toward the sovereign goal.

        Returns the augmented prompt.
        """
        goal = self.get_active_goal()
        if not goal:
            return user_query

        context = self._build_goal_context()
        approaches = self._get_recent_approaches(goal.id, limit=3)

        # Build the injected context
        injected = f"""## SOVEREIGN GOAL
**Goal**: {goal.title}
**Description**: {goal.description}
**Status**: {goal.status}
**Priority**: {goal.priority}/5

### Success Criteria
{chr(10).join(f'- {c}' for c in goal.success_criteria) if goal.success_criteria else '- Complete the task'}

### Constraints
{chr(10).join(f'- {c}' for c in goal.constraints) if goal.constraints else '- None specified'}

### Previous Approaches
"""
        if approaches:
            for a in approaches:
                injected += f"- **Iteration {a.iteration}**: {a.strategy[:100]} → {a.result.upper()}\n"
                if a.learning:
                    injected += f"  *Learned*: {a.learning[:200]}\n"
                if a.next_target:
                    injected += f"  *Next*: {a.next_target[:200]}\n"
        else:
            injected += "- No previous approaches yet. This is the first attempt.\n"

        injected += f"""
### Current Approach
The user is asking: "{user_query}"

**Instructions**: 
1. Align your response with the sovereign goal above
2. If the user's query helps progress the goal, prioritize it
3. If the query diverges, gently steer back toward the goal
4. If the current approach seems unattainable, suggest alternatives
5. Document what you learned for the next iteration

### Suggested Next Target
Based on previous attempts and the current goal, identify the most promising next step.
"""

        return injected

    def get_goal_context_for_llm(self) -> str:
        """Get a condensed goal context for LLM system prompts."""
        goal = self.get_active_goal()
        if not goal:
            return "No active sovereign goal set. Use /goal set to define one."

        return (
            f"SOVEREIGN GOAL: {goal.title}. {goal.description} "
            f"Success criteria: {', '.join(goal.success_criteria) or 'task completion'}. "
            f"Approaches tried: {goal.approaches_tried} ({goal.approaches_succeeded} succeeded). "
            f"Status: {goal.status}."
        )

    # ---- Approach Tracking ----

    def record_approach(self, goal_id: str, strategy: str,
                         hypothesis: str = "", iteration: int = None) -> Approach:
        """Record a new approach attempt."""
        goal = self._find_goal(goal_id)
        if not goal:
            goal = self.get_active_goal()

        if iteration is None:
            iteration = (goal.approaches_tried + 1) if goal else 1

        approach = Approach(
            id=hashlib.md5(f"{goal_id}_{iteration}_{time.time()}".encode()).hexdigest()[:12],
            goal_id=goal_id or (goal.id if goal else "unknown"),
            iteration=iteration,
            strategy=strategy,
            hypothesis=hypothesis,
            started_at=datetime.now().isoformat(),
        )

        if goal:
            goal.approaches_tried += 1
            goal.updated_at = datetime.now().isoformat()
            self._persist_goal(goal)

        self._persist_approach(approach)
        return approach

    def update_approach(self, approach_id: str, result: str,
                         evidence: str = "", learning: str = "",
                         next_target: str = "") -> bool:
        """Update an approach with results and learning."""
        approach = self._load_approach(approach_id)
        if not approach:
            return False

        approach.result = result
        approach.evidence = evidence
        approach.learning = learning
        approach.next_target = next_target
        approach.completed_at = datetime.now().isoformat()

        if result == "success":
            goal = self._find_goal(approach.goal_id)
            if goal:
                goal.approaches_succeeded += 1
                self._persist_goal(goal)

        self._persist_approach(approach)

        # Log to iteration markdown
        self._log_iteration_markdown(approach)

        return True

    def get_approaches(self, goal_id: str = None, limit: int = 20) -> List[Approach]:
        """Get approaches for a goal."""
        goal_id = goal_id or (self._active_goal.id if self._active_goal else None)
        if not goal_id:
            return []

        approaches = []
        pattern = f"approach_{goal_id}_*.json"
        for f in sorted(self.GOALS_DIR.glob(pattern)):
            try:
                data = json.loads(f.read_text())
                approaches.append(Approach(**data))
            except Exception:
                pass

        return approaches[-limit:]

    # ---- Fallback Engine ----

    def generate_fallback(self, goal_id: str = None) -> str:
        """
        Generate a fallback strategy when the current approach
        is blocked or has failed multiple times.
        """
        goal = self._find_goal(goal_id) or self.get_active_goal()
        if not goal:
            return "No active goal to generate fallback for."

        approaches = self.get_approaches(goal.id)
        failed = [a for a in approaches if a.result == "falsified"]

        if not failed:
            return "No failed approaches yet. Continue with current strategy."

        # Build fallback analysis
        analysis = f"""## Fallback Analysis — {goal.title}
**Failed approaches**: {len(failed)}

### What didn't work:
"""
        for a in failed[-5:]:
            analysis += f"- **Iteration {a.iteration}**: {a.strategy[:150]}\n"
            if a.learning:
                analysis += f"  *Why it failed*: {a.learning[:200]}\n"

        # Generate alternative strategies
        analysis += """
### Alternative Strategies to Try:
"""
        # Strategy 1: Decompose into smaller sub-goals
        analysis += (
            "1. **Decompose**: Break the goal into smaller sub-goals. "
            "Instead of tackling everything at once, solve one piece at a time.\n"
        )

        # Strategy 2: Change tools/approach
        analysis += (
            "2. **Tool Swap**: Try different tools or APIs. "
            "If Crawl4AI failed, try Playwright or direct HTTP. "
            "If local model struggles, try cloud models via Harness bridge.\n"
        )

        # Strategy 3: Expand knowledge
        analysis += (
            "3. **Knowledge First**: Before attempting again, research more context. "
            "Use /research with brain_only=False to gather external data, then retry.\n"
        )

        # Strategy 4: Constraint relaxation
        if goal.constraints:
            analysis += (
                f"4. **Relax Constraints**: Current constraints may be too strict: "
                f"{', '.join(goal.constraints[:3])}. Consider which can be relaxed.\n"
            )

        # Strategy 5: Ask for help / swarm
        analysis += (
            "5. **Swarm It**: Delegate to the Harness multi-agent swarm. "
            "Different AI models may find paths the local model missed.\n"
        )

        analysis += f"""
### Recommended Next Iteration:
Based on the analysis above, the recommended approach for iteration {goal.approaches_tried + 1} is:

**Try {analysis.split(chr(10))[7].strip() if len(analysis.split(chr(10))) > 7 else 'decomposition'} first.**
If that fails, escalate to swarm or relax constraints.

*Save this analysis to your local vault for the RAG learning loop.*
"""

        return analysis

    # ---- Persistence ----

    def _persist_goal(self, goal: Goal):
        """Save goal to JSON file."""
        fpath = self.GOALS_DIR / f"goal_{goal.id}.json"
        fpath.write_text(json.dumps({
            'id': goal.id, 'title': goal.title, 'description': goal.description,
            'status': goal.status, 'priority': goal.priority,
            'created_at': goal.created_at, 'updated_at': goal.updated_at,
            'success_criteria': goal.success_criteria,
            'constraints': goal.constraints,
            'approaches_tried': goal.approaches_tried,
            'approaches_succeeded': goal.approaches_succeeded,
        }, indent=2))

    def _persist_approach(self, approach: Approach):
        """Save approach to JSON file."""
        fpath = self.GOALS_DIR / f"approach_{approach.goal_id}_{approach.iteration}.json"
        fpath.write_text(json.dumps({
            'id': approach.id, 'goal_id': approach.goal_id,
            'iteration': approach.iteration, 'strategy': approach.strategy,
            'hypothesis': approach.hypothesis, 'result': approach.result,
            'evidence': approach.evidence, 'learning': approach.learning,
            'next_target': approach.next_target,
            'started_at': approach.started_at,
            'completed_at': approach.completed_at,
        }, indent=2))

    def _find_goal(self, goal_id: str = None) -> Optional[Goal]:
        """Find a goal by ID or return active goal."""
        if goal_id:
            fpath = self.GOALS_DIR / f"goal_{goal_id}.json"
            if fpath.exists():
                return Goal(**json.loads(fpath.read_text()))
            return None
        return self.get_active_goal()

    def _load_active_goal(self) -> Optional[Goal]:
        """Load the most recent active goal."""
        goals = self.list_goals(status="active")
        if goals:
            self._active_goal = goals[0]
            self._build_goal_context()
            return self._active_goal
        return None

    def _load_approach(self, approach_id: str) -> Optional[Approach]:
        """Load an approach by ID."""
        for f in self.GOALS_DIR.glob("approach_*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get('id') == approach_id:
                    return Approach(**data)
            except Exception:
                pass
        return None

    def _get_recent_approaches(self, goal_id: str, limit: int = 3) -> List[Approach]:
        """Get the most recent approaches for a goal."""
        return self.get_approaches(goal_id, limit)

    def _build_goal_context(self) -> str:
        """Build the goal context string for prompt injection."""
        goal = self._active_goal
        if not goal:
            return ""
        self._goal_context = (
            f"Goal: {goal.title} | Priority: {goal.priority} | "
            f"Attempts: {goal.approaches_tried}/{goal.approaches_succeeded} | "
            f"Status: {goal.status}"
        )
        return self._goal_context

    def _save_goal_markdown(self, goal: Goal):
        """Save goal as markdown for user visibility."""
        md_path = self.GOALS_DIR / f"goal_{goal.id}.md"
        content = f"""# Goal: {goal.title}

**Status**: {goal.status}
**Priority**: {goal.priority}/5
**Created**: {goal.created_at}

## Description
{goal.description}

## Success Criteria
{chr(10).join(f'- {c}' for c in goal.success_criteria) if goal.success_criteria else '- Not specified'}

## Constraints
{chr(10).join(f'- {c}' for c in goal.constraints) if goal.constraints else '- None'}

## Progress
- Approaches tried: {goal.approaches_tried}
- Approaches succeeded: {goal.approaches_succeeded}

## Iteration Log
See individual approach files for detailed iteration documentation.
"""
        md_path.write_text(content)

    def _log_iteration_markdown(self, approach: Approach):
        """Log an iteration to a markdown file for user tracking."""
        goal = self._find_goal(approach.goal_id)
        goal_title = goal.title if goal else "Unknown"
        safe_title = re.sub(r'[^\w\s-]', '', goal_title)[:50]

        md_path = self.GOALS_DIR / f"iterations_{safe_title}.md"

        # Append or create
        if md_path.exists():
            content = md_path.read_text()
        else:
            content = f"# Iteration Log — {goal_title}\n\n"

        entry = f"""
## Iteration {approach.iteration} — {datetime.now().strftime('%Y-%m-%d %H:%M')}

### Strategy
{approach.strategy}

### Hypothesis
{approach.hypothesis or 'No hypothesis recorded'}

### Result: {approach.result.upper()}

### Evidence
{approach.evidence or 'No evidence recorded'}

### Learning
{approach.learning or 'No learning recorded'}

### Next Target
{approach.next_target or 'Not determined'}

---
"""
        md_path.write_text(content + entry)

        # Index into RAG for self-evolution
        self._index_into_rag(approach, goal_title)


    def _index_into_rag(self, approach: Approach, goal_title: str):
        """Index iteration learning into the RAG knowledge vault."""
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np

            model = SentenceTransformer("all-MiniLM-L6-v2")
            text = (
                f"[Goal: {goal_title}] [Iteration {approach.iteration}] "
                f"Strategy: {approach.strategy}. "
                f"Result: {approach.result}. "
                f"Learning: {approach.learning[:500]}"
            )

            chash = hashlib.sha256(text.encode()).hexdigest()

            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT embedding FROM embedding_cache WHERE hash = ?",
                    (chash,),
                )
                row = cursor.fetchone()
                emb_bytes = row[0] if row else model.encode(text).astype(np.float32).tobytes()

                if not row:
                    cursor.execute(
                        "INSERT OR IGNORE INTO embedding_cache VALUES (?, ?)",
                        (chash, emb_bytes),
                    )

                cursor.execute(
                    "INSERT INTO documents (url, text) VALUES (?, ?)",
                    (f"goal://{approach.goal_id}/iteration/{approach.iteration}", text),
                )
                cursor.execute(
                    "INSERT INTO vec_documents (id, embedding) VALUES (?, ?)",
                    (cursor.lastrowid, emb_bytes),
                )
        except ImportError:
            pass  # RAG indexing is optional


# Module-level singleton
_orchestrator: Optional[GoalOrchestrator] = None


def get_goal_orchestrator() -> GoalOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = GoalOrchestrator()
    return _orchestrator
