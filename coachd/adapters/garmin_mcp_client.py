"""Direct MCP tool execution for confirmed write-actions.

When the user confirms a parked workout, the write must run exactly as proposed —
no LLM in the loop, so it is deterministic and cannot drift. This calls the
Garmin MCP tool directly over stdio with the stored payload, using the SAME MCP
config the agent uses (so the same pinned server + token store).

One-confirm scheduling
----------------------
``create_*`` tools only upload a workout to the LIBRARY (returning its id);
putting it on a calendar DATE is a separate ``schedule_workout(id, date)`` call.
The write-guard parks the create and the model never sees the real id, so the
create→schedule chain can't happen inside the guarded turn. We expose an
in-process COMPOSITE tool whose schema declares ``schedule_date`` (see
``composite_tools.py`` — a schema-aware model only fills declared params). The
model calls the composite; the guard parks it; on confirm ``_run`` decomposes it
into the real garmin calls — two MCP calls in ONE session, one confirmation::

    mcp__coachd__create_and_schedule_run(..., schedule_date="2026-06-16")  # parked, one ✓
        └─ _run: create_walk_run_workout (library, → id)
                 → schedule_workout(id, 2026-06-16)                         (calendar)

Partial failure (create ok, schedule fails or id unparseable) is REPORTED, not
raised: the workout is in the library and the user recovers with a fresh
"schedule it" turn (the model reads the id via get_workouts, then schedule_workout).

``bare_tool``, ``extract_tool_text``, ``parse_workout_id`` and ``_run`` are pure /
I/O-free (``_run`` takes an injected ``call``), so the two-step chain is unit-tested
without a subprocess. ``execute`` is the thin MCP-session glue around ``_run``.
"""

from __future__ import annotations

import json
import re

from ..core.i18n import Strings
from ..core.pending import PendingAction
from .composite_tools import CREATE_AND_SCHEDULE_RUN

# coachd composite tools (in-process; their schema carries schedule_date, so the
# model reliably fills it) → the real garmin create tool the executor runs on
# confirm before scheduling. The model calls the composite; it is never executed
# as-is — _run decomposes it into create → parse id → schedule_workout.
_COMPOSITE_CREATE: dict[str, str] = {
    CREATE_AND_SCHEDULE_RUN: "create_walk_run_workout",
}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def bare_tool(qualified: str) -> str:
    """'mcp__garmin__upload_workout' -> 'upload_workout' (the MCP's own tool name)."""
    return qualified.rsplit("__", 1)[-1]


def extract_tool_text(result: object) -> str:
    """Join the text content blocks of an MCP CallToolResult."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip() or "ok"


def parse_workout_id(text: str) -> int | None:
    """Pull the new workout id from a create/upload tool's JSON result.

    garmin-mcp's curated result uses ``workout_id``; the raw form uses
    ``workoutId``. Returns None on anything unparseable (not JSON, not a dict,
    id missing or non-numeric) so the caller reports a loud partial-failure
    instead of silently skipping the schedule step."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("workout_id", data.get("workoutId"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class GarminMcpExecutor:
    """Executes a confirmed PendingAction by calling its tool on the Garmin MCP."""

    def __init__(self, mcp_config: dict, strings: Strings) -> None:
        # mcp_config is provider.mcp_servers()["garmin"]: {command, args, env}
        self._cfg = mcp_config
        # language-bound catalog for the user-facing status line we return
        self._strings = strings

    async def execute(self, action: PendingAction) -> str:
        """Open one MCP stdio session and run the (possibly two-step) action.

        Returns a user-facing status line (the bot sends it verbatim)."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self._cfg["command"],
            args=list(self._cfg.get("args", [])),
            env=self._cfg.get("env"),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name: str, args: dict) -> str:
                    return extract_tool_text(await session.call_tool(name, args))

                return await self._run(action, call)

    async def _run(self, action: PendingAction, call) -> str:
        """Orchestrate the confirmed write over an injected ``call(name, args)``.

        I/O-free (``call`` is the only side effect) so the create→schedule chain
        is unit-tested with a fake. See module docstring for the flow.

        A composite tool (mcp__coachd__create_and_schedule_*) is NOT a real MCP
        tool — it is decomposed here into the real garmin create + schedule_workout.
        Everything else is a plain garmin write, run as proposed."""
        tool = bare_tool(action.tool)
        inp = dict(action.input or {})

        real_create = _COMPOSITE_CREATE.get(tool)
        if real_create is None:
            await call(tool, inp)            # ordinary garmin write
            return self._strings.get("exec_done", tool=tool)

        # Composite create+schedule. schedule_date is a declared param of the
        # composite tool's schema, so the model fills it and it rides in input.
        sched = inp.pop("schedule_date", None)
        # Validate BEFORE the create so a bad date can't orphan a library workout.
        # (internal guard, not user-facing — English inline, not the catalog)
        if not sched or not _ISO_DATE.match(str(sched)):
            raise ValueError(f"schedule_date {sched!r} is not in YYYY-MM-DD format")

        text = await call(real_create, inp)  # real garmin create → library, returns id
        wid = parse_workout_id(text)
        if wid is None:
            return self._strings.get("exec_created_no_id", sched=sched)
        try:
            await call("schedule_workout", {"workout_id": wid, "calendar_date": sched})
        except Exception as exc:  # noqa: BLE001 — schedule is best-effort after a real create
            return self._strings.get("exec_created_sched_failed", sched=sched, exc=exc)
        return self._strings.get("exec_created_scheduled", sched=sched)
