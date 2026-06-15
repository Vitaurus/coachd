"""The in-process composite scheduling tool builds and is named as the executor expects."""

from __future__ import annotations

from coachd.adapters.composite_tools import (
    COMPOSITE_TOOLS,
    CREATE_AND_SCHEDULE_RUN,
    SERVER_NAME,
    build_composite_server,
)


def test_qualified_name_matches_server_and_tool():
    # the guard's write-set and the executor's map both key off this exact name
    assert COMPOSITE_TOOLS == (f"mcp__{SERVER_NAME}__{CREATE_AND_SCHEDULE_RUN}",)
    assert COMPOSITE_TOOLS == ("mcp__coachd__create_and_schedule_run",)


def test_build_composite_server_succeeds():
    # builds the SDK MCP server — catches a malformed schema (which would silently
    # fail to register the tool, so the model would never see it)
    server = build_composite_server()
    # McpSdkServerConfig is dict-like: {"type": "sdk", "name": ..., "instance": ...}
    assert server["type"] == "sdk"
    assert server["name"] == SERVER_NAME
