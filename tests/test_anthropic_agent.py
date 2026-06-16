"""Tests for the Claude Agent SDK adapter — fully offline (fake query, no CLI)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import base64

from coachd.adapters.anthropic_agent import (
    CONTEXT_1M_BETA,
    AnthropicAgent,
    extract_result,
    image_user_message,
    probe_anthropic_auth,
)
from coachd.ports.llm import LLMError


# --- probe_anthropic_auth (boot fail-fast) ---------------------------------- #
def _fixed_status(code):
    """A status_fn that records the headers it was called with and returns code."""
    seen: dict = {}

    def status_fn(url, headers):
        seen["url"] = url
        seen["headers"] = headers
        return code

    return status_fn, seen


def test_probe_valid_credential_returns_valid():
    status_fn, _ = _fixed_status(200)
    assert probe_anthropic_auth("sk-ant-api-x", "", status_fn=status_fn) == "valid"


def test_probe_rejected_on_401_and_403():
    for code in (401, 403):
        status_fn, _ = _fixed_status(code)
        assert probe_anthropic_auth("sk-ant-api-x", "", status_fn=status_fn) == "rejected"


def test_probe_5xx_is_unreachable_not_rejected():
    # a transient server error must NOT read as a bad key (the EXPIRED-vs-
    # UNREACHABLE lesson) — boot proceeds and retries per-turn
    status_fn, _ = _fixed_status(503)
    assert probe_anthropic_auth("sk-ant-api-x", "", status_fn=status_fn) == "unreachable"


def test_probe_network_error_is_unreachable():
    def boom(url, headers):
        raise OSError("name resolution failed")

    assert probe_anthropic_auth("sk-ant-api-x", "", status_fn=boom) == "unreachable"


def test_probe_no_credential_is_rejected():
    status_fn, seen = _fixed_status(200)
    assert probe_anthropic_auth("", "", status_fn=status_fn) == "rejected"
    assert seen == {}  # short-circuits — never hits the network


def test_probe_oauth_token_uses_bearer_header():
    status_fn, seen = _fixed_status(200)
    probe_anthropic_auth("", "sk-ant-oat-tok", status_fn=status_fn)
    assert seen["headers"]["Authorization"] == "Bearer sk-ant-oat-tok"
    assert "x-api-key" not in seen["headers"]


def test_probe_api_key_uses_x_api_key_header():
    status_fn, seen = _fixed_status(200)
    probe_anthropic_auth("sk-ant-api-key", "", status_fn=status_fn)
    assert seen["headers"]["x-api-key"] == "sk-ant-api-key"
    assert "Authorization" not in seen["headers"]


def test_probe_prefers_oauth_when_both_present():
    # the OAuth token wins (Bearer) — config never sets both, but be deterministic
    status_fn, seen = _fixed_status(200)
    probe_anthropic_auth("sk-ant-api-key", "sk-ant-oat-tok", status_fn=status_fn)
    assert seen["headers"]["Authorization"] == "Bearer sk-ant-oat-tok"
    assert "x-api-key" not in seen["headers"]


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
class _FakeClient:
    """Stand-in for ClaudeSDKClient: an async context manager that keeps the
    "channel" open across query() + receive_response() (the bidirectional shape
    can_use_tool needs). Records into the shared ``captured`` dict."""

    captured: dict = {}

    def __init__(self, *, options):
        type(self).captured["client_options"] = options

    async def __aenter__(self):
        type(self).captured["entered"] = True
        return self

    async def __aexit__(self, *a):
        type(self).captured["exited"] = True
        return False

    async def query(self, prompt, session_id="default"):
        type(self).captured["prompt"] = prompt

    async def receive_response(self):
        for m in (_assistant("partial"), _result(result="verdict", cost=0.03)):
            yield m


def test_run_turn_guarded_goes_through_client_not_oneshot():
    captured: dict = {}
    _FakeClient.captured = captured

    def fake_options(**kw):
        captured.update(kw)
        return SimpleNamespace(**kw)

    async def boom_query(*, prompt, options):  # must NOT be used on the guarded path
        raise AssertionError("guarded turn must use the client, not query()")
        yield  # pragma: no cover

    guard = lambda name, inp, ctx=None: None  # noqa: E731 (stand-in can_use_tool)
    agent = AnthropicAgent(
        model="opus[1m]",
        system_prompt="METHODOLOGY",
        mcp_servers={"garmin": {"command": "garmin-mcp"}},
        allowed_tools=["mcp__garmin__get_sleep_summary"],
        can_use_tool=guard,
        max_budget_usd=1.5,
        use_1m_context=True,
        query_fn=boom_query,
        options_cls=fake_options,
        client_cls=_FakeClient,
    )

    res = asyncio.run(agent.run_turn("today's prompt"))

    assert res.text == "verdict"
    assert res.cost_usd == 0.03
    # the write-guard chokepoint is wired into the SDK options
    assert captured["can_use_tool"] is guard
    assert captured["betas"] == [CONTEXT_1M_BETA]
    assert captured["max_budget_usd"] == 1.5
    assert captured["allowed_tools"] == ["mcp__garmin__get_sleep_summary"]
    # bidirectional client path: opened, prompt sent as a plain string, closed
    assert captured["entered"] and captured["exited"]
    assert captured["prompt"] == "today's prompt"


def _drain(aiter):
    """Collect an async iterator into a list (sync helper for tests)."""
    async def go():
        return [m async for m in aiter]
    return asyncio.run(go())


# --- image input ------------------------------------------------------------ #
def test_image_user_message_builds_text_and_base64_image_blocks():
    raw = b"\xff\xd8\xff fake jpeg"
    msgs = _drain(image_user_message("what is this?", (raw, "image/jpeg")))
    assert len(msgs) == 1
    content = msgs[0]["message"]["content"]
    assert msgs[0]["type"] == "user" and msgs[0]["message"]["role"] == "user"
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1]["type"] == "image"
    src = content[1]["source"]
    assert src["type"] == "base64" and src["media_type"] == "image/jpeg"
    assert base64.b64decode(src["data"]) == raw  # round-trips the exact bytes


def test_run_turn_with_image_sends_iterable_message_through_guarded_client():
    captured: dict = {}
    _FakeClient.captured = captured

    def fake_options(**kw):
        captured.update(kw)
        return SimpleNamespace(**kw)

    guard = lambda name, inp, ctx=None: None  # noqa: E731
    agent = AnthropicAgent(
        model="opus",
        system_prompt="SP",
        mcp_servers={},
        allowed_tools=["mcp__garmin__get_sleep_summary"],
        can_use_tool=guard,
        options_cls=fake_options,
        client_cls=_FakeClient,
    )

    res = asyncio.run(agent.run_turn("describe", image=(b"PNGDATA", "image/png")))

    assert res.text == "verdict"
    # the write-guard stays wired even with an image
    assert captured["can_use_tool"] is guard
    # the query input is the iterable [text, image] message, NOT a plain string
    sent = captured["prompt"]
    assert not isinstance(sent, str)
    blocks = _drain(sent)[0]["message"]["content"]
    assert blocks[0]["text"] == "describe"
    assert base64.b64decode(blocks[1]["source"]["data"]) == b"PNGDATA"
    assert blocks[1]["source"]["media_type"] == "image/png"


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
