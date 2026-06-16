"""In-process (SDK) MCP tools that coachd exposes to the chat agent.

Why this exists
---------------
garmin-mcp's ``create_walk_run_workout`` only uploads to the LIBRARY; scheduling
on a calendar date is a separate ``schedule_workout(id, date)`` call that needs the
new workout's id — which the write-guard hides until the user confirms. We tried
smuggling a ``schedule_date`` through the create tool's args, but a schema-aware
model will NOT pass an argument the tool's declared schema doesn't list, so it was
silently dropped every time.

The fix: declare OUR OWN tool whose schema DOES include ``schedule_date``. The
model sees it as a first-class parameter and fills it reliably. The guard parks
the call like any write; on confirm ``GarminMcpExecutor`` decomposes it into the
real garmin calls (create → parse id → schedule). The in-process handler here is
never actually run — the guard denies the tool before the SDK would invoke it —
so it just carries the schema (and a defensive sentinel body).

Constants are import-light (no SDK) so the executor and tests can reference the
tool name without the SDK present. ``build_composite_server`` imports the SDK
lazily, matching ``anthropic_agent``.
"""

from __future__ import annotations

from typing import Annotated

from ..core.i18n import TODAY_MARKER

SERVER_NAME = "coachd"
CREATE_AND_SCHEDULE_RUN = "create_and_schedule_run"

# Fully-qualified names the model and the write-guard see (mcp__{server}__{tool}).
COMPOSITE_TOOLS: tuple[str, ...] = (f"mcp__{SERVER_NAME}__{CREATE_AND_SCHEDULE_RUN}",)

_RUN_DESC = (
    "Create a running / walk-run workout AND immediately schedule it for a specific "
    "Garmin calendar date — with one confirmation. Use THIS tool (not "
    "create_walk_run_workout) when the user asks to schedule a run on a day "
    "(\"for tomorrow\", \"on Wednesday\", \"on June 16\"). schedule_date is a required "
    f"YYYY-MM-DD date; count relative days from the '{TODAY_MARKER}' line in the message."
)

_RUN_SCHEMA = {
    "name": Annotated[str, "Workout name, e.g. 'Easy Z2 Run'"],
    "run_seconds": Annotated[int, "Duration of one running interval, seconds"],
    "walk_seconds": Annotated[int, "Duration of walking/recovery, seconds (0 for a continuous run)"],
    "repeats": Annotated[int, "Number of repeats of the run/walk block (1 for a steady run)"],
    "warmup_min": Annotated[int, "Warm-up, minutes"],
    "cooldown_min": Annotated[int, "Cool-down, minutes"],
    "hr_zone": Annotated[str, "Target HR zone: Z1, Z2, Z3, Z4 or Z5"],
    "schedule_date": Annotated[str, "Calendar date YYYY-MM-DD to schedule the workout for"],
}


async def _parked(args: dict) -> dict:  # pragma: no cover - never invoked (guard denies first)
    """Sentinel handler. The write-guard parks+denies this tool before the SDK
    would call it; the confirmed write runs in GarminMcpExecutor against garmin-mcp."""
    return {"content": [{"type": "text", "text": "queued for confirmation"}],
            "is_error": True}


def build_composite_server():
    """Build the in-process SDK MCP server carrying coachd's composite tools.

    SDK imported lazily so importing this module (for its constants) needs neither
    the SDK package internals nor the bundled CLI."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    run_tool = tool(CREATE_AND_SCHEDULE_RUN, _RUN_DESC, _RUN_SCHEMA)(_parked)
    return create_sdk_mcp_server(SERVER_NAME, tools=[run_tool])
