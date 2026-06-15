"""Pin the Garmin tool allowlists (security scar tissue) and the MCP wiring."""

from __future__ import annotations

from coachd.adapters.garmin_provider import (
    FORBIDDEN_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    GarminProvider,
)


def _provider(tmp_path):
    return GarminProvider(tmp_path / "garmin")


def test_mcp_server_config_shape(tmp_path):
    p = _provider(tmp_path)
    servers = p.mcp_servers()
    assert set(servers) == {"garmin"}
    cfg = servers["garmin"]
    assert cfg["type"] == "stdio"
    assert cfg["command"] == "garmin-mcp"          # pinned/pre-baked, not git-pull
    assert cfg["env"]["GARMINTOKENS"] == str(tmp_path / "garmin")


def test_mcp_command_is_configurable_for_prebake(tmp_path):
    p = GarminProvider(tmp_path / "g", command="/opt/mcp/garmin-mcp", args=("--pinned",))
    cfg = p.mcp_servers()["garmin"]
    assert cfg["command"] == "/opt/mcp/garmin-mcp"
    assert cfg["args"] == ["--pinned"]


def test_read_tools_qualified_and_complete(tmp_path):
    p = _provider(tmp_path)
    read = p.read_tools()
    assert all(t.startswith("mcp__garmin__") for t in read)
    assert len(read) == len(READ_TOOLS) == 43
    # a few load-bearing ones the report/chat depend on
    for t in ("get_sleep_summary", "get_training_readiness", "get_hrv_data"):
        assert f"mcp__garmin__{t}" in read


def test_write_tools_are_exactly_the_five_safe_ones(tmp_path):
    p = _provider(tmp_path)
    write = p.write_tools()
    assert write == [
        "mcp__garmin__create_walk_run_workout",
        "mcp__garmin__create_strength_workout",
        "mcp__garmin__upload_workout",
        "mcp__garmin__schedule_workout",
        "mcp__garmin__upload_course",
    ]


def test_no_destructive_tool_is_ever_exposed(tmp_path):
    p = _provider(tmp_path)
    exposed = set(p.read_tools()) | set(p.write_tools())
    for bad in FORBIDDEN_TOOLS:
        assert f"mcp__garmin__{bad}" not in exposed, f"destructive {bad} leaked into allowlist"


def test_read_and_write_sets_are_disjoint(tmp_path):
    p = _provider(tmp_path)
    assert set(p.read_tools()).isdisjoint(p.write_tools())


def test_allowlists_have_no_overlap_with_forbidden_constants():
    # guard at the constant level too (independent of qualification)
    assert set(READ_TOOLS).isdisjoint(FORBIDDEN_TOOLS)
    assert set(WRITE_TOOLS).isdisjoint(FORBIDDEN_TOOLS)
    assert set(READ_TOOLS).isdisjoint(WRITE_TOOLS)


def test_system_prompt_fragment_describes_source(tmp_path):
    frag = _provider(tmp_path).system_prompt_fragment()
    assert "garmin" in frag.lower()
    assert "get_sleep_summary" in frag  # tool-level gotcha preserved
