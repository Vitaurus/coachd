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

SERVER_NAME = "coachd"
CREATE_AND_SCHEDULE_RUN = "create_and_schedule_run"

# Fully-qualified names the model and the write-guard see (mcp__{server}__{tool}).
COMPOSITE_TOOLS: tuple[str, ...] = (f"mcp__{SERVER_NAME}__{CREATE_AND_SCHEDULE_RUN}",)

_RUN_DESC = (
    "Створити бігове / walk-run тренування І ОДРАЗУ запланувати його на конкретну "
    "дату календаря Garmin — одним підтвердженням. Використовуй САМЕ цей тул (а не "
    "create_walk_run_workout), коли користувач просить запланувати пробіжку на день "
    "(«на завтра», «на середу», «на 16 червня»). schedule_date — обовʼязкова дата "
    "YYYY-MM-DD; рахуй відносні дні від рядка «Сьогодні:» у повідомленні."
)

_RUN_SCHEMA = {
    "name": Annotated[str, "Назва тренування, напр. 'Easy Z2 Run'"],
    "run_seconds": Annotated[int, "Тривалість одного бігового інтервалу, секунд"],
    "walk_seconds": Annotated[int, "Тривалість ходьби/відновлення, секунд (0 якщо суцільний біг)"],
    "repeats": Annotated[int, "Кількість повторів блоку біг/ходьба (1 для рівного бігу)"],
    "warmup_min": Annotated[int, "Розминка, хвилин"],
    "cooldown_min": Annotated[int, "Заминка, хвилин"],
    "hr_zone": Annotated[str, "Цільова зона ЧСС: Z1, Z2, Z3, Z4 або Z5"],
    "schedule_date": Annotated[str, "Дата календаря YYYY-MM-DD, на яку запланувати тренування"],
}


async def _parked(args: dict) -> dict:  # pragma: no cover - never invoked (guard denies first)
    """Sentinel handler. The write-guard parks+denies this tool before the SDK
    would call it; the confirmed write runs in GarminMcpExecutor against garmin-mcp."""
    return {"content": [{"type": "text", "text": "поставлено в чергу на підтвердження"}],
            "is_error": True}


def build_composite_server():
    """Build the in-process SDK MCP server carrying coachd's composite tools.

    SDK imported lazily so importing this module (for its constants) needs neither
    the SDK package internals nor the bundled CLI."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    run_tool = tool(CREATE_AND_SCHEDULE_RUN, _RUN_DESC, _RUN_SCHEMA)(_parked)
    return create_sdk_mcp_server(SERVER_NAME, tools=[run_tool])
