"""Composition root: the whole object graph wires and the report flow runs e2e."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from coachd.app import build_app, load_methodology
from coachd.config import ServiceConfig
from coachd.core.resilience import RunState

_VALID = (
    "Доброго ранку! Готовність висока.\n"
    "===METRICS===\n"
    '{"sleep_h":7.5,"rhr":48,"body_battery_charged":80,"verdict":"ОК"}'
)


def _config(tmp_path):
    return ServiceConfig.from_env({
        "TG_BOT_TOKEN": "123:abc",
        "TG_CHAT_ID": "12345",
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "USER_NAME": "Віталій",
        "WORN_START": "2026-06-08",
        "TZ": "Europe/Kyiv",
        "GARMINTOKENS": str(tmp_path / "garmin"),
    })


def _fakes():
    def fake_options(**kw):
        return SimpleNamespace(**kw)

    async def fake_query(*, prompt, options):
        yield SimpleNamespace(content=[SimpleNamespace(text=_VALID)], error=None)
        yield SimpleNamespace(
            total_cost_usd=0.02, result=_VALID, usage=None,
            is_error=False, api_error_status=None, errors=None,
        )

    return fake_query, fake_options


def test_load_methodology_reads_packaged_file():
    text = load_methodology()
    assert "Методологія" in text  # the real methodology.md ships and loads


def test_build_app_report_flow_end_to_end(tmp_path):
    fake_query, fake_options = _fakes()
    posts: list = []
    app = build_app(
        _config(tmp_path),
        methodology="RULES",
        query_fn=fake_query,
        options_cls=fake_options,
        post=lambda url, data: posts.append((url, data)),
    )

    out = asyncio.run(app.engine.run_report("morning", date(2026, 6, 15), "2026-06-15 07:00"))
    assert out.state is RunState.OK
    assert "Готовність висока" in out.message

    sent = app.messenger.send(out.message)
    assert sent == 1 and len(posts) == 1  # delivered through the wired messenger

    # journal persisted on the data volume (parent of the token store)
    assert (tmp_path / "journal.jsonl").exists()


def test_report_agent_is_readonly_chat_agent_is_guarded(tmp_path):
    fake_query, fake_options = _fakes()
    app = build_app(
        _config(tmp_path), methodology="RULES",
        query_fn=fake_query, options_cls=fake_options, post=lambda u, d: None,
    )

    # report agent: read-only, no guard
    rep = app.engine._llm._build_options()
    assert not any("upload_workout" in t for t in rep.allowed_tools)
    assert rep.can_use_tool is None

    # chat agent: reads auto-approved, writes routed through the guard.
    # SECURITY REGRESSION: the SDK skips can_use_tool for anything in
    # allowed_tools, so a write listed there auto-executes and BYPASSES the
    # guard (this exact mistake shipped a workout with no confirmation). Writes
    # must be ABSENT from allowed_tools — MCP keeps them callable, and the guard
    # (can_use_tool) parks them. allowed_tools must equal the read set, no more.
    chat = app.chat_agent._build_options()
    assert chat.allowed_tools == rep.allowed_tools          # reads only, == report agent
    assert not any("upload_workout" in t for t in chat.allowed_tools)
    assert not any("create_walk_run_workout" in t for t in chat.allowed_tools)
    assert not any("schedule_workout" in t for t in chat.allowed_tools)
    assert chat.can_use_tool is not None                    # the guard is wired


def test_owner_gate_wired(tmp_path):
    fake_query, fake_options = _fakes()
    app = build_app(
        _config(tmp_path), methodology="RULES",
        query_fn=fake_query, options_cls=fake_options, post=lambda u, d: None,
    )
    assert app.owner_gate.allows(12345) is True
    assert app.owner_gate.allows(999) is False
