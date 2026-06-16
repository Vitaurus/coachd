"""The write-guard: the runtime chokepoint that parks every write for confirmation.

Architecture decision #4 (revised after the T2 cross-model tension). Wired into
the Claude Agent SDK via ``can_use_tool``. On a write tool the guard does NOT
block the turn waiting on a human (that would freeze a subprocess and lose state
on restart). Instead it:

  1. persists the proposed (tool, input) as a durable PendingAction (nonce);
  2. DENIES the tool call, so the write never executes inside this turn;

The user later confirms in Telegram → the engine calls PendingStore.confirm(nonce)
and executes the write as a FRESH, idempotent invocation. A restart between
propose and confirm is safe: the pending record is on disk and confirm fires once.

This module is SDK-agnostic: the allow/deny result builders are injected so the
guard is unit-tested with fakes (no SDK, no CLI). The composition root supplies
the real PermissionResultAllow / PermissionResultDeny builders.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Iterable

from ..core.i18n import Strings
from ..core.pending import PendingAction, PendingStore

# allow() -> SDK PermissionResultAllow ; deny(action) -> SDK PermissionResultDeny
AllowBuilder = Callable[[], object]
DenyBuilder = Callable[[PendingAction], object]


def default_confirm_message(action: PendingAction, strings: Strings) -> str:
    """USER-facing caption shown above the Telegram confirm/cancel buttons.

    Surfaces the schedule_date when present so the user can SEE at confirm time
    whether the workout will land on the calendar or only in the library — turns
    a silent "model forgot to schedule" miss into a visible, cancellable one.

    The conditional scheduled-suffix is composed in code (not duplicated as two
    full message variants) so the shared prefix lives in one catalog entry."""
    sched = (action.input or {}).get("schedule_date")
    when = strings.get("confirm_scheduled_suffix", sched=sched) if sched else ""
    return strings.get(
        "confirm_needs_approval", tool=action.tool, when=when, nonce=action.nonce
    )


def guard_deny_reason(action: PendingAction) -> str:
    """MODEL-facing denial reason (the SDK delivers this as the tool result).

    The write is already parked for out-of-band confirmation, so instruct the
    model to stop trying to write and to summarise its proposal — the user gets
    the confirm buttons separately. Without the "do not retry" steer the model
    would attempt other write tools and park duplicates."""
    return (
        "This action has been queued for the user to confirm in Telegram. "
        "Do NOT call write tools again and don't look for another way to push the data. "
        "Instead, briefly summarise for the user what you're proposing (type, duration, "
        "zones/targets) — they'll see the confirm buttons separately."
    )


def make_write_guard(
    store: PendingStore,
    write_tools: Iterable[str],
    *,
    allow: AllowBuilder,
    deny: DenyBuilder,
) -> Callable[..., Awaitable[object]]:
    """Build a ``can_use_tool`` callback that parks writes and allows reads.

    ``write_tools`` are the fully-qualified names (mcp__garmin__upload_workout,
    ...). Anything not in that set is allowed (reads). The set is closed: a tool
    the provider never exposes can't reach here, and even if it did, only the
    explicit write set is parked — everything else is read-only by construction.
    """
    write_set = set(write_tools)

    async def can_use_tool(tool_name: str, tool_input: dict, ctx: object = None) -> object:
        if tool_name in write_set:
            action = store.put(tool_name, dict(tool_input or {}))
            return deny(action)
        return allow()

    return can_use_tool
