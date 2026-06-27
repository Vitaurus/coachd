"""Pin the durable pending-action store — the restart-safe single-execute guard."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from coachd.core.pending import CANCELLED, PENDING, USED, PendingStore

KYIV = ZoneInfo("Europe/Kyiv")

_NONCES = iter(f"n{i}" for i in range(1000))


def _store(tmp_path):
    return PendingStore(
        tmp_path / "pending.json",
        nonce_factory=lambda: next(_NONCES),
        now=lambda: "2026-06-15T07:00:00+00:00",
    )


def test_put_and_get(tmp_path):
    s = _store(tmp_path)
    a = s.put("mcp__garmin__upload_workout", {"name": "intervals"})
    assert a.status == PENDING
    assert s.get(a.nonce) == a
    assert a.input == {"name": "intervals"}


def test_confirm_returns_action_and_marks_used(tmp_path):
    s = _store(tmp_path)
    a = s.put("mcp__garmin__upload_workout", {"x": 1})
    confirmed = s.confirm(a.nonce)
    assert confirmed is not None
    assert confirmed.status == USED
    assert confirmed.input == {"x": 1}


def test_confirm_is_idempotent_no_double_execute(tmp_path):
    s = _store(tmp_path)
    a = s.put("mcp__garmin__upload_workout", {"x": 1})
    assert s.confirm(a.nonce) is not None    # first confirm fires
    assert s.confirm(a.nonce) is None        # second is a NO-OP (no phantom write)


def test_confirm_unknown_nonce_is_noop(tmp_path):
    s = _store(tmp_path)
    assert s.confirm("does-not-exist") is None


def test_cancel_then_confirm_is_noop(tmp_path):
    s = _store(tmp_path)
    a = s.put("mcp__garmin__schedule_workout", {})
    assert s.cancel(a.nonce) is True
    assert s.get(a.nonce).status == CANCELLED
    assert s.confirm(a.nonce) is None        # cancelled cannot be confirmed


def test_survives_restart_confirm_after_reload(tmp_path):
    path = tmp_path / "pending.json"
    s1 = PendingStore(path, nonce_factory=lambda: "FIXED", now=lambda: "t")
    s1.put("mcp__garmin__upload_workout", {"payload": 42})
    # simulate a container restart: brand new store from the same file
    s2 = PendingStore(path)
    confirmed = s2.confirm("FIXED")
    assert confirmed is not None and confirmed.input == {"payload": 42}
    # and a stale tap after that confirm still does not re-fire
    s3 = PendingStore(path)
    assert s3.confirm("FIXED") is None


def test_malformed_file_starts_empty(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("{ this is not json", encoding="utf-8")
    s = PendingStore(path)
    assert s.get("anything") is None         # no crash, empty store


def test_purge_resolved_keeps_only_pending(tmp_path):
    s = _store(tmp_path)
    a = s.put("mcp__garmin__upload_workout", {})
    b = s.put("mcp__garmin__schedule_workout", {})
    s.confirm(a.nonce)
    removed = s.purge_resolved()
    assert removed == 1
    assert s.get(b.nonce) is not None and s.get(a.nonce) is None


# --- used_on: the deterministic confirmed-action feed for the daily digest --- #
def test_used_on_excludes_pending_and_cancelled(tmp_path):
    s = _store(tmp_path)  # clock fixed at 2026-06-15T07:00:00+00:00 → 10:00 Kyiv
    s.put("t1", {})                       # stays pending
    c = s.put("t2", {})
    s.cancel(c.nonce)                     # cancelled
    u = s.put("t3", {"name": "5k"})
    s.confirm(u.nonce)                    # used
    out = s.used_on(date(2026, 6, 15), KYIV)
    assert [x.nonce for x in out] == [u.nonce]     # only the USED one
    assert out[0].input == {"name": "5k"}


def test_used_on_uses_local_calendar_day(tmp_path):
    # 22:30 UTC on the 14th is 01:30 Kyiv on the 15th — the digest must file it
    # under the LOCAL day, not the UTC day.
    s = PendingStore(
        tmp_path / "pending.json",
        nonce_factory=lambda: next(_NONCES),
        now=lambda: "2026-06-14T22:30:00+00:00",
    )
    a = s.put("mcp__garmin__upload_workout", {})
    s.confirm(a.nonce)
    assert [x.nonce for x in s.used_on(date(2026, 6, 15), KYIV)] == [a.nonce]
    assert s.used_on(date(2026, 6, 14), KYIV) == []
