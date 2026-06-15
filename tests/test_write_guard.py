"""Pin the write-guard: writes are parked + denied, reads pass, nonce persisted."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from coachd.core.pending import PENDING, PendingStore
from coachd.security.write_guard import default_confirm_message, make_write_guard

WRITE_TOOLS = [
    "mcp__garmin__upload_workout",
    "mcp__garmin__create_strength_workout",
]

# fake SDK result builders (no SDK needed)
_ALLOW = lambda: SimpleNamespace(behavior="allow")  # noqa: E731
_DENY = lambda action: SimpleNamespace(behavior="deny", nonce=action.nonce)  # noqa: E731


def _guard(tmp_path):
    store = PendingStore(tmp_path / "pending.json", nonce_factory=lambda: "N1")
    guard = make_write_guard(store, WRITE_TOOLS, allow=_ALLOW, deny=_DENY)
    return store, guard


def test_read_tool_is_allowed(tmp_path):
    store, guard = _guard(tmp_path)
    res = asyncio.run(guard("mcp__garmin__get_sleep_summary", {}, None))
    assert res.behavior == "allow"
    assert store.get("N1") is None  # nothing parked for a read


def test_write_tool_is_parked_and_denied(tmp_path):
    store, guard = _guard(tmp_path)
    res = asyncio.run(guard("mcp__garmin__upload_workout", {"name": "intervals"}, None))
    assert res.behavior == "deny"
    # the proposal was persisted as pending with the exact input
    parked = store.get("N1")
    assert parked is not None
    assert parked.status == PENDING
    assert parked.tool == "mcp__garmin__upload_workout"
    assert parked.input == {"name": "intervals"}
    assert res.nonce == "N1"  # deny carries the nonce for the Telegram message


def test_unknown_or_destructive_tool_not_in_write_set_is_allowed(tmp_path):
    # a tool that is neither a known read nor in the write set still defaults to
    # allow here — the provider's allowlist is what prevents it reaching the agent.
    # The guard's job is only to PARK the explicit write set.
    store, guard = _guard(tmp_path)
    res = asyncio.run(guard("mcp__garmin__get_hrv_data", {}, None))
    assert res.behavior == "allow"


def test_default_confirm_message_mentions_tool_and_nonce(tmp_path):
    store, _ = _guard(tmp_path)
    action = store.put("mcp__garmin__upload_workout", {})
    msg = default_confirm_message(action)
    assert "upload_workout" in msg
    assert action.nonce in msg
