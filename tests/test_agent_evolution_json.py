"""
Regression tests for ``backend.agent.evolution._call_llm_json``.

The retry helper composes a follow-up user message when the first response
is not parseable JSON. One of its two branches used a name that did not exist
anywhere in the module, so the retry itself raised NameError instead of
recovering — the branch is only reached when the history does not end in a
user message, which no test did.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.agent.evolution import _call_llm_json
from backend.llm.base import ChatResponse, Usage


def _response(content: str, finish_reason: str = "stop") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="test-model",
        provider="test",
        usage=Usage(),
        finish_reason=finish_reason,
    )


class ScriptedProvider:
    """Returns a queued sequence of responses; records every prompt it saw."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[list] = []

    async def chat(self, messages, **kwargs):
        self.prompts.append([(m.role, m.content) for m in messages])
        return self.responses.pop(0)


def test_retry_after_unparseable_response(monkeypatch):
    provider = ScriptedProvider([_response("not json"), _response('{"ok": true}')])
    monkeypatch.setattr("backend.agent.evolution.get_default", lambda: provider)

    from backend.llm import ChatMessage, Role

    data, _resp = asyncio.run(_call_llm_json(
        [ChatMessage(role=Role.USER, content="do the thing")],
        max_retries=1,
    ))

    assert data == {"ok": True}
    # The hint is appended to the existing user turn.
    assert len(provider.prompts) == 2
    assert "valid JSON" in provider.prompts[1][-1][1]


def test_retry_when_history_does_not_end_in_a_user_message(monkeypatch):
    """The branch that used to raise NameError instead of retrying."""
    provider = ScriptedProvider([_response("not json"), _response('{"ok": true}')])
    monkeypatch.setattr("backend.agent.evolution.get_default", lambda: provider)

    from backend.llm import ChatMessage, Role

    data, _resp = asyncio.run(_call_llm_json(
        [ChatMessage(role=Role.ASSISTANT, content="I will think about it")],
        max_retries=1,
    ))

    assert data == {"ok": True}
    # A fresh user turn was appended, carrying the hint on its own.
    assert provider.prompts[1][-1][0] == Role.USER
    assert "valid JSON" in provider.prompts[1][-1][1]


def test_truncated_response_gets_the_stronger_hint(monkeypatch):
    provider = ScriptedProvider([
        _response("", finish_reason="length"),
        _response('{"ok": true}'),
    ])
    monkeypatch.setattr("backend.agent.evolution.get_default", lambda: provider)

    from backend.llm import ChatMessage, Role

    data, _resp = asyncio.run(_call_llm_json(
        [ChatMessage(role=Role.USER, content="do the thing")],
        max_retries=1,
    ))

    assert data == {"ok": True}
    assert "Skip the thinking this time" in provider.prompts[1][-1][1]


def test_exhausted_retries_reraise(monkeypatch):
    provider = ScriptedProvider([_response("nope"), _response("still nope")])
    monkeypatch.setattr("backend.agent.evolution.get_default", lambda: provider)

    from backend.llm import ChatMessage, Role

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(_call_llm_json(
            [ChatMessage(role=Role.USER, content="do the thing")],
            max_retries=1,
        ))