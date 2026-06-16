"""Confirmed-write executor: pure helpers + the create→schedule chain (_run).

The live MCP call is runtime glue; _run is exercised with an injected ``call``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from coachd.adapters.garmin_mcp_client import (
    GarminMcpExecutor,
    bare_tool,
    extract_tool_text,
    parse_workout_id,
)
from coachd.core.i18n import Strings
from coachd.core.pending import PendingAction


def test_bare_tool_strips_qualifier():
    assert bare_tool("mcp__garmin__upload_workout") == "upload_workout"
    assert bare_tool("mcp__garmin__create_walk_run_workout") == "create_walk_run_workout"


def test_extract_tool_text_joins_blocks():
    result = SimpleNamespace(content=[
        SimpleNamespace(text="workout created"),
        SimpleNamespace(text="id=42"),
    ])
    assert extract_tool_text(result) == "workout created\nid=42"


def test_extract_tool_text_empty_is_ok():
    assert extract_tool_text(SimpleNamespace(content=[])) == "ok"
    assert extract_tool_text(SimpleNamespace(content=None)) == "ok"


# --- parse_workout_id (pure) ------------------------------------------------ #
def test_parse_workout_id_curated_and_raw():
    assert parse_workout_id('{"workout_id": 1602148957}') == 1602148957  # curated
    assert parse_workout_id('{"workoutId": 7, "x": 1}') == 7             # raw fallback
    assert parse_workout_id('{"workout_id": "42"}') == 42                # coerced to int


def test_parse_workout_id_unparseable_returns_none():
    assert parse_workout_id("not json") is None
    assert parse_workout_id('["a","b"]') is None        # not a dict
    assert parse_workout_id('{"name": "x"}') is None     # no id key
    assert parse_workout_id('{"workout_id": null}') is None
    assert parse_workout_id('{"workout_id": "abc"}') is None  # non-numeric


# --- _run: the create→schedule chain ---------------------------------------- #
class _Call:
    """Injected MCP ``call(name, args)``: records calls, returns canned create text."""

    def __init__(self, *, create_text='{"workout_id": 42}', schedule_raises=None):
        self.log: list[tuple[str, dict]] = []
        self._create_text = create_text
        self._schedule_raises = schedule_raises

    async def __call__(self, name, args):
        self.log.append((name, dict(args)))
        if name == "schedule_workout":
            if self._schedule_raises:
                raise self._schedule_raises
            return "scheduled"
        return self._create_text


def _action(tool, **inp):
    # qualifier prefix is cosmetic — bare_tool keeps only the last __ segment
    return PendingAction(nonce="N", tool=f"mcp__x__{tool}", input=inp, created_ts="t")


def _run(action, call):
    # uk so the assertions below can pin the Ukrainian status lines
    return asyncio.run(GarminMcpExecutor({}, Strings("uk"))._run(action, call))


_COMPOSITE = "create_and_schedule_run"   # the coachd composite tool (bare name)


def test_run_ordinary_garmin_write_single_call():
    # a plain garmin write (not a composite) runs as proposed — no scheduling
    call = _Call()
    msg = _run(_action("create_walk_run_workout", name="x"), call)
    assert call.log == [("create_walk_run_workout", {"name": "x"})]
    assert msg == "✓ Виконано: create_walk_run_workout."


def test_run_composite_chains_real_create_then_schedule():
    call = _Call(create_text='{"workout_id": 42}')
    msg = _run(_action(_COMPOSITE, name="r", hr_zone="Z2", schedule_date="2026-06-16"), call)
    assert call.log == [
        # composite maps to the REAL garmin create; schedule_date popped off
        ("create_walk_run_workout", {"name": "r", "hr_zone": "Z2"}),
        ("schedule_workout", {"workout_id": 42, "calendar_date": "2026-06-16"}),
    ]
    assert msg == "✓ Створено і заплановано на 2026-06-16."


def test_run_composite_raw_id_key_also_schedules():
    call = _Call(create_text='{"workoutId": 7}')
    _run(_action(_COMPOSITE, schedule_date="2026-06-16"), call)
    assert ("schedule_workout", {"workout_id": 7, "calendar_date": "2026-06-16"}) in call.log


def test_run_composite_partial_fail_schedule_raises_reports_not_raises():
    call = _Call(schedule_raises=RuntimeError("boom"))
    msg = _run(_action(_COMPOSITE, schedule_date="2026-06-16"), call)
    assert call.log[0][0] == "create_walk_run_workout"   # create still happened
    assert "не вдалося запланувати" in msg and "boom" in msg
    assert msg.startswith("⚠️")


def test_run_composite_id_unparsed_skips_schedule_loudly():
    call = _Call(create_text="created, no json here")
    msg = _run(_action(_COMPOSITE, schedule_date="2026-06-16"), call)
    assert [n for n, _ in call.log] == ["create_walk_run_workout"]  # no schedule attempted
    assert "не вдалося визначити його id" in msg and msg.startswith("⚠️")


def test_run_composite_missing_date_rejected_before_create():
    call = _Call()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _run(_action(_COMPOSITE, name="r"), call)        # composite without schedule_date
    assert call.log == []                                # no orphan library workout


def test_run_composite_bad_date_rejected_before_create():
    call = _Call()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _run(_action(_COMPOSITE, schedule_date="16/06/2026"), call)
    assert call.log == []
