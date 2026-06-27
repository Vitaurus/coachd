"""Composition root: the whole object graph wires and the report flow runs e2e."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from coachd.__main__ import _load_voice_model
from coachd.adapters.faster_whisper_stt import FasterWhisperTranscriber
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
        "USER_NAME": "Олекса",
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
    assert "methodology" in text.lower()  # the real methodology.md ships and loads


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

    # composite scheduling tool: available ONLY to the chat agent (in-process SDK
    # MCP server), never to the read-only report agent
    assert "coachd" in app.chat_agent._mcp_servers
    assert "coachd" not in app.engine._llm._mcp_servers

    # and the guard PARKS the composite (it's in the write set, NOT auto-approved)
    verdict = asyncio.run(app.chat_agent._can_use_tool(
        "mcp__coachd__create_and_schedule_run", {"schedule_date": "2026-06-16"}, None))
    assert getattr(verdict, "behavior", None) == "deny"


def test_daily_digest_wired_with_toolless_summarizer(tmp_path):
    fake_query, fake_options = _fakes()
    app = build_app(
        _config(tmp_path), methodology="RULES",
        query_fn=fake_query, options_cls=fake_options, post=lambda u, d: None,
    )
    from coachd.core.daily_digest import DailyDigest

    assert isinstance(app.digest, DailyDigest)
    # the summarizer agent is TOOL-FREE (no Garmin reads/writes) and unguarded —
    # it only condenses text, so it never needs the MCP tools or the write guard.
    opts = app.digest._llm._build_options()
    assert opts.allowed_tools == []
    assert opts.can_use_tool is None
    # it must read the SAME stores the chat agent writes, and the SAME journal the
    # report reads — otherwise the row would never reach the evening report.
    assert app.digest._pending is app.pending
    assert app.digest._sessions is app.session_store
    assert app.digest._journal is app.engine._journal


def test_owner_gate_wired(tmp_path):
    fake_query, fake_options = _fakes()
    app = build_app(
        _config(tmp_path), methodology="RULES",
        query_fn=fake_query, options_cls=fake_options, post=lambda u, d: None,
    )
    assert app.owner_gate.allows(12345) is True
    assert app.owner_gate.allows(999) is False


# --- voice/STT wiring ----------------------------------------------------- #
def test_build_app_constructs_unloaded_transcriber_when_voice_enabled(tmp_path):
    fake_query, fake_options = _fakes()
    app = build_app(
        _config(tmp_path), methodology="RULES",
        query_fn=fake_query, options_cls=fake_options, post=lambda u, d: None,
    )
    # VOICE_ENABLED defaults to true → a transcriber is constructed but UNLOADED
    # (the heavy model fetch is deferred to load(), kept out of pure build_app).
    assert isinstance(app.transcriber, FasterWhisperTranscriber)
    assert app.transcriber.ready is False
    assert app.transcriber._model_size == app.config.whisper_model
    # and it is NOT yet wired into the bot — serve's background loader does that
    # once the model is ready, so text serves immediately while voice loads.
    assert app.bot._transcriber is None
    assert app.bot._max_voice_seconds == app.config.max_voice_seconds
    # voice is enabled → the bot is in the "pending" state so it sends the
    # transient "warming up" line (not the permanent off line) during the load.
    assert app.bot._voice_pending is True


def test_build_app_no_transcriber_when_voice_disabled(tmp_path):
    cfg = ServiceConfig.from_env({
        "TG_BOT_TOKEN": "123:abc", "TG_CHAT_ID": "12345",
        "ANTHROPIC_API_KEY": "sk-ant-x", "USER_NAME": "Олекса",
        "WORN_START": "2026-06-08", "TZ": "Europe/Kyiv",
        "GARMINTOKENS": str(tmp_path / "garmin"),
        "VOICE_ENABLED": "false",
    })
    fake_query, fake_options = _fakes()
    app = build_app(
        cfg, methodology="RULES",
        query_fn=fake_query, options_cls=fake_options, post=lambda u, d: None,
    )
    assert app.transcriber is None
    assert app.bot._transcriber is None
    assert app.bot._voice_pending is False  # voice off → never promises "warming up"


def _voice_app(transcriber, *, set_calls, unavailable_calls):
    """A minimal stand-in for App that _load_voice_model touches: .transcriber,
    .bot.{set_transcriber,mark_voice_unavailable}, .config.{whisper_model,whisper_compute}."""
    bot = SimpleNamespace(
        set_transcriber=lambda t: set_calls.append(t),
        mark_voice_unavailable=lambda: unavailable_calls.append(True),
    )
    config = SimpleNamespace(whisper_model="small", whisper_compute="int8")
    return SimpleNamespace(transcriber=transcriber, bot=bot, config=config)


def test_load_voice_model_enables_voice_on_success():
    loaded: list = []
    set_calls: list = []
    unavailable_calls: list = []
    transcriber = SimpleNamespace(load=lambda: loaded.append(True))
    app = _voice_app(transcriber, set_calls=set_calls, unavailable_calls=unavailable_calls)

    asyncio.run(_load_voice_model(app))

    assert loaded == [True]               # the model was loaded (off the loop)
    assert set_calls == [transcriber]     # …and voice was enabled on the bot
    assert unavailable_calls == []        # success path never marks it unavailable


def test_load_voice_model_degrades_quietly_on_load_failure():
    set_calls: list = []
    unavailable_calls: list = []

    def _boom():
        raise RuntimeError("no model file")

    transcriber = SimpleNamespace(load=_boom)
    app = _voice_app(transcriber, set_calls=set_calls, unavailable_calls=unavailable_calls)

    # a load failure must NOT crash serve and must leave voice disabled — the
    # exception is swallowed (logged loudly), set_transcriber is never called, and
    # the bot is told to drop "warming up" (mark_voice_unavailable) so it stops
    # promising a model that will never arrive.
    asyncio.run(_load_voice_model(app))

    assert set_calls == []
    assert unavailable_calls == [True]
