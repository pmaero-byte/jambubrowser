"""
Step verification — after each tool call, ask the LLM to judge whether the
step advanced the goal. If not, the agent loop will replan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from backend.llm import ChatMessage, Role, get_default

from .plan import PlanStep


@dataclass
class StepVerdict:
    advanced: bool
    confidence: float = 0.0
    feedback: str = ""
    suggested_next_action: str = ""

    def to_dict(self) -> dict:
        return {
            "advanced": self.advanced,
            "confidence": self.confidence,
            "feedback": self.feedback,
            "suggested_next_action": self.suggested_next_action,
        }


VERIFY_PROMPT = """You are a verification module. Given:
- The user's goal
- A step that was just executed (description + tool + result)
- The remaining plan

Decide whether the step meaningfully advanced the goal, OR was a dead end.

Respond with JSON (no markdown):
{{
  "advanced": true | false,
  "confidence": 0.0-1.0,
  "feedback": "<why it did or did not advance>",
  "suggested_next_action": "<what to do next, or empty>"
}}

Goal: {goal}
Step: {step}
Result: {result}
Remaining: {remaining}
"""


async def verify_step(
    goal: str,
    step: PlanStep,
    result: dict,
    remaining_steps: list[PlanStep],
) -> StepVerdict:
    """Use the LLM to judge whether a step advanced the goal."""
    # Quick heuristics first — avoid an LLM call when obvious
    if not result.get("success", True):
        return StepVerdict(
            advanced=False,
            confidence=0.95,
            feedback=f"Step failed: {result.get('error', 'unknown error')}",
            suggested_next_action="try a different tool or approach",
        )
    if step.tool == "final_answer":
        return StepVerdict(advanced=True, confidence=1.0, feedback="Final answer delivered")

    # LLM-based verification
    prompt = VERIFY_PROMPT.format(
        goal=goal,
        step=step.to_dict(),
        result=result if isinstance(result, dict) else {"data": str(result)[:2000]},
        remaining=[s.to_dict() for s in remaining_steps[:3]],
    )
    try:
        llm = get_default()
        resp = await llm.chat(
            [ChatMessage(role=Role.USER, content=prompt)],
            temperature=0.1,
            max_tokens=400,
        )
        return _parse_verdict(resp.content)
    except Exception as e:
        # On verification failure, default to "advanced=True" with low confidence
        return StepVerdict(advanced=True, confidence=0.4, feedback=f"verification error: {e}")


def _parse_verdict(text: str) -> StepVerdict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return StepVerdict(advanced=True, confidence=0.5, feedback="could not parse verdict")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return StepVerdict(advanced=True, confidence=0.5, feedback="could not parse verdict")
    return StepVerdict(
        advanced=bool(obj.get("advanced", True)),
        confidence=float(obj.get("confidence", 0.5)),
        feedback=str(obj.get("feedback", "")),
        suggested_next_action=str(obj.get("suggested_next_action", "")),
    )
