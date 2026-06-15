"""Pure bits of the confirmed-write executor (the live MCP call is runtime-glue)."""

from __future__ import annotations

from types import SimpleNamespace

from coachd.adapters.garmin_mcp_client import bare_tool, extract_tool_text


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
