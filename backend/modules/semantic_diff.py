"""
Semantic diffing for UI changes.

Pixel diffs flag *any* change; reviewers want to know *what* changed. This
module produces a human-readable, noise-tolerant summary of two UI states:

- :func:`semantic_diff_elements` — structural diff over element catalogs
  (added / removed / renamed / state changes), deterministic and dependency-free.
- :func:`explain_diff` — optional LLM rewrite of that summary (falls back to
  the deterministic text when no provider is configured).
- :func:`semantic_diff_images` — pixel change percentage (via visual_diff)
  plus an optional vision description of the "after" frame.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("jambu.semantic_diff")


def _index(elements: list[dict]) -> dict:
    return {e.get("ref"): e for e in (elements or []) if e.get("ref")}


def _name(e: dict) -> str:
    return (e.get("name") or "").strip()


def semantic_diff_elements(before: list[dict], after: list[dict]) -> dict:
    """Structural semantic diff of two element catalogs."""
    b, a = _index(before), _index(after)
    added, removed, renamed, state_changed = [], [], [], []

    for ref in a:
        if ref not in b:
            added.append({"ref": ref, "name": _name(a[ref])})
    for ref in b:
        if ref not in a:
            removed.append({"ref": ref, "name": _name(b[ref])})

    # Match by position in document order for rename/state detection.
    b_list = [e for e in (before or []) if e.get("ref")]
    a_list = [e for e in (after or []) if e.get("ref")]
    for old, new in zip(b_list, a_list):
        if old.get("ref") in a and new.get("ref") in b:
            continue  # same element, no positional change to compare here
        same_role = (old.get("role") or old.get("tag")) == (new.get("role") or new.get("tag"))
        if same_role and _name(old) and _name(new) and _name(old) != _name(new):
            renamed.append({"ref": new.get("ref"), "from": _name(old), "to": _name(new)})

    for ref in set(a) & set(b):
        for key, label in (("checked", "checked"), ("disabled", "disabled"),
                           ("visible", "visibility"), ("value", "value")):
            if b[ref].get(key) != a[ref].get(key):
                state_changed.append({
                    "ref": ref, "name": _name(a[ref]), "property": label,
                    "from": b[ref].get(key), "to": a[ref].get(key),
                })

    summary = _compose_summary(added, removed, renamed, state_changed)
    return {
        "added": added,
        "removed": removed,
        "renamed": renamed,
        "state_changed": state_changed,
        "counts": {
            "added": len(added), "removed": len(removed),
            "renamed": len(renamed), "state_changed": len(state_changed),
        },
        "changed": bool(added or removed or renamed or state_changed),
        "summary": summary,
    }


def _compose_summary(added, removed, renamed, state_changed) -> str:
    parts = []
    if added:
        parts.append(f"{len(added)} element(s) appeared: "
                     + ", ".join(f"'{x['name']}'" for x in added[:3]))
    if removed:
        parts.append(f"{len(removed)} element(s) disappeared: "
                     + ", ".join(f"'{x['name']}'" for x in removed[:3]))
    if renamed:
        parts.append(f"{len(renamed)} label(s) changed: "
                     + ", ".join(f"'{x['from']}' → '{x['to']}'" for x in renamed[:3]))
    if state_changed:
        parts.append(f"{len(state_changed)} state change(s): "
                     + ", ".join(f"'{x['name']}' {x['property']} "
                                 f"{x['from']}→{x['to']}" for x in state_changed[:3]))
    return "; ".join(parts) if parts else "no semantic change"


def explain_diff(diff: dict, provider: str = "") -> str:
    """Optionally rewrite a deterministic diff as prose via the LLM layer."""
    fallback = diff.get("summary", "no semantic change")
    try:
        from backend.llm import get_registry  # type: ignore

        registry = get_registry()
        prompt = (
            "Describe this UI change in one or two plain sentences for a "
            f"developer, focusing on user-visible impact: {fallback}. "
            f"Details: {diff}"
        )
        result = registry.complete(prompt, provider=provider or None)  # type: ignore
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return (text or fallback).strip()
    except Exception:
        log.debug("LLM diff explanation unavailable", exc_info=True)
        return fallback


async def semantic_diff_images(before_b64: str, after_b64: str, *,
                               use_vision: bool = False, provider: str = "") -> dict:
    """Pixel diff (always) plus an optional vision description of the new frame."""
    out: dict = {"method": "pixel"}
    try:
        from backend.modules.visual_diff import compute_visual_change

        change = compute_visual_change(before_b64, after_b64)
        out.update(change if isinstance(change, dict) else {"change_pct": change})
    except Exception:
        out["error"] = "pixel diff unavailable"

    if use_vision:
        try:
            from backend.modules.multimodal_input import process_image

            result = await process_image(
                after_b64, prompt="Describe the current UI in one sentence.",
            )
            if result:
                out["description"] = result.get("text") or result.get("description")
                out["method"] = "pixel+vision"
        except Exception:
            log.debug("vision description unavailable", exc_info=True)
    return out
