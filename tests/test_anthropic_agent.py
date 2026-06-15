"""Tests for the Claude Agent SDK adapter — fully offline (fake query, no CLI)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from coachd.adapters.anthropic_agent import (
    CONTEXT_1M_BETA,
    AnthropicAgent,
    extract_result,
)
from coachd.ports.llm import LLMError


def _assistant(*texts, error=None):
    return SimpleNamespace(content=[SimpleNamespace(text=t) for t in texts], error=error)


def _result(*, result=None, cost=None, is_error=False, api_error_status=None, usage=None):
    # presence of total_cost_usd is how extract_result recognises a result message
    return SimpleNamespace(
        total_cost_usd=cost, result=result, usage=usage,
        is_error=is_error, api_error_status=api_error_status, errors=None,
    )


# --- extract_result --------------------------------------------------------- #
def test_prefers_result_text_over_assistant_blocks():
    msgs = [_assistant("partial..."), _result(result="FINAL VERDICT", cost=0.02, usage={"in": 100})]
    res = extract_result(msgs)
    assert res.text == "FINAL VERDICT"
    assert res.cost_usd == 0.02
    assert res.usage == {"in": 100}


def test_falls_back_to_joined_assistant_text():
    msgs = [_assistant("line one", "line two"), _result(result=None, cost=0.01)]
    res = extract_result(msgs)
    assert res.text == "line one\nline two"
    assert res.cost_usd == 0.01


def test_assistant_error_raises_llmerror_with_code():
    with pytest.raises(LLMError) as ei:
        extract_result([_assistant(error="authentication_failed")])
    assert ei.value.code == "authentication_failed"
    assert ei.value.retryable is False


def test_rate_limit_is_retryable():
    with pytest.raises(LLMError) as ei:
        extract_result([_assistant("x", error="rate_limit")])
    assert ei.value.code == "rate_limit"
    assert ei.value.retryable is True


def test_api_error_429_maps_to_rate_limit_not_unknown():
    with pytest.raises(LLMError) as ei:
        extract_result([_result(result="x", api_error_status=429, is_error=True)])
    assert ei.value.code == "rate_limit"  # specific code wins over generic is_error


# --- run_turn wiring -------------------------------------------------------- #
def test_run_turn_builds_options_and_returns_result():
    captured: dict = {}

    def fake_options(**kw):
        captured.update(kw)
        return SimpleNamespace(**kw)

    async def fake_query(*, prompt, options):
        # can_use_tool is set → the SDK demands streaming mode, so the adapter
        # must hand us an AsyncIterable, not a string. Drain it to capture the
        # one user message it yields.
        captured["prompt_type"] = type(prompt).__name__
        captured["stream"] = [m async for m in prompt]
        for m in (_assistant("partial"), _result(result="verdict", cost=0.03)):
            yield m

    guard = lambda name, inp, ctx: None  # noqa: E731 (stand-in can_use_tool)
    agent = AnthropicAgent(
        model="opus[1m]",
        system_prompt="METHODOLOGY",
        mcp_servers={"garmin": {"command": "garmin-mcp"}},
        allowed_tools=["mcp__garmin__get_sleep_summary"],
        can_use_tool=guard,
        max_budget_usd=1.5,
        use_1m_context=True,
        query_fn=fake_query,
        options_cls=fake_options,
    )

    res = asyncio.run(agent.run_turn("today's prompt"))

    assert res.text == "verdict"
    assert res.cost_usd == 0.03
    # the write-guard chokepoint is wired into the SDK options
    assert captured["can_use_tool"] is guard
    assert captured["betas"] == [CONTEXT_1M_BETA]
    assert captured["max_budget_usd"] == 1.5
    assert captured["model"] == "opus[1m]"
    assert captured["allowed_tools"] == ["mcp__garmin__get_sleep_summary"]
    # streaming mode: a single user message in the SDK's expected shape
    assert captured["stream"] == [{
        "type": "user",
        "message": {"role": "user", "content": "today's prompt"},
        "parent_tool_use_id": None,
        "session_id": "default",
    }]


def test_run_turn_no_1m_sends_empty_betas():
    captured: dict = {}

    def fake_options(**kw):
        captured.update(kw)
        return SimpleNamespace(**kw)

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        yield _result(result="ok", cost=0.0)

    agent = AnthropicAgent(
        model="opus",
        system_prompt="SP",
        mcp_servers={},
        allowed_tools=[],
        use_1m_context=False,
        query_fn=fake_query,
        options_cls=fake_options,
    )
    asyncio.run(agent.run_turn("p"))
    assert captured["betas"] == []
    # no can_use_tool → simpler string mode (the read-only report path)
    assert captured["prompt"] == "p"
