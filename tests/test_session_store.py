"""Pin bounded persistent chat history (decision #5)."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from coachd.core.session_store import SessionStore

KYIV = ZoneInfo("Europe/Kyiv")

_TS = iter(f"2026-06-15T07:{i:02d}:00+00:00" for i in range(100))


def _store(tmp_path, **kw):
    return SessionStore(tmp_path / "sessions.json", now=lambda: next(_TS), **kw)


def test_append_and_history(tmp_path):
    s = _store(tmp_path)
    s.append(123, "user", "привіт")
    s.append(123, "assistant", "вітаю")
    hist = s.history(123)
    assert [t.role for t in hist] == ["user", "assistant"]
    assert hist[0].text == "привіт"


def test_bounded_to_max_turns(tmp_path):
    s = _store(tmp_path, max_turns=3)
    for i in range(5):
        s.append(1, "user", f"m{i}")
    hist = s.history(1)
    assert len(hist) == 3
    assert [t.text for t in hist] == ["m2", "m3", "m4"]  # newest kept


def test_per_chat_isolation(tmp_path):
    s = _store(tmp_path)
    s.append(1, "user", "a")
    s.append(2, "user", "b")
    assert len(s.history(1)) == 1 and len(s.history(2)) == 1
    assert s.history(1)[0].text == "a"


def test_survives_restart(tmp_path):
    path = tmp_path / "sessions.json"
    s1 = SessionStore(path, now=lambda: "t")
    s1.append(7, "user", "памʼ'ятай")
    s2 = SessionStore(path)  # reload (restart)
    assert s2.history(7)[0].text == "памʼ'ятай"


def test_malformed_file_starts_empty(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("not json", encoding="utf-8")
    assert SessionStore(path).history(1) == []


def test_clear(tmp_path):
    s = _store(tmp_path)
    s.append(1, "user", "x")
    s.clear(1)
    assert s.history(1) == []


# --- turns_on: the day's whole conversation for the digest (all chats) ------- #
def test_turns_on_aggregates_all_chats_sorted_by_ts(tmp_path):
    s = _store(tmp_path)  # ts = 2026-06-15T07:0x:00Z → 10:0x Kyiv → the 15th
    s.append(1, "user", "a")        # earliest ts
    s.append(2, "assistant", "b")   # later ts, different chat (household)
    out = s.turns_on(date(2026, 6, 15), KYIV)
    assert [t.text for t in out] == ["a", "b"]      # both chats, ts-ordered
    assert s.turns_on(date(2026, 6, 14), KYIV) == []


def test_turns_on_uses_local_calendar_day(tmp_path):
    # 22:30 UTC = 01:30 Kyiv the next day — the turn belongs to the LOCAL day
    s = SessionStore(tmp_path / "sessions.json", now=lambda: "2026-06-14T22:30:00+00:00")
    s.append(1, "user", "late night")
    assert [t.text for t in s.turns_on(date(2026, 6, 15), KYIV)] == ["late night"]
    assert s.turns_on(date(2026, 6, 14), KYIV) == []
