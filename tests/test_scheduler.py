"""Pin the scheduled-report on-fire logic: delivery, EMPTY notice, re-auth nudge."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from coachd.auth.garmin_login import TokenState
from coachd.core.engine import ReportOutcome
from coachd.core.resilience import RunState
from coachd.scheduler import REAUTH_NUDGE, ReportScheduler, fire_report, format_now

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 6, 15, 7, 0, tzinfo=KYIV)


class _Engine:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    async def run_report(self, mode, on_date, now_str):
        self.calls.append((mode, on_date, now_str))
        return self._outcome


class _Messenger:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return 1


def _app(outcome):
    return SimpleNamespace(
        config=SimpleNamespace(tz="Europe/Kyiv", tokenstore="/data/garmin"),
        engine=_Engine(outcome),
        messenger=_Messenger(),
    )


def _token_fn(state, *, calls):
    def fn(tokenstore):
        calls.append(tokenstore)
        return state
    return fn


def test_format_now():
    assert format_now(NOW) == "2026-06-15 07:00 EEST"


def test_ok_delivers_report_without_touching_token():
    app = _app(ReportOutcome(RunState.OK, "🌅 звіт\n\nтекст", "текст", 0.02))
    token_calls: list = []
    msg = asyncio.run(fire_report(app, "morning", NOW, token_state_fn=_token_fn(TokenState.VALID, calls=token_calls)))
    assert msg == "🌅 звіт\n\nтекст"
    assert app.messenger.sent == [msg]
    assert token_calls == []                       # happy path never hits Garmin again
    assert app.engine.calls[0][0] == "morning"
    assert app.engine.calls[0][2] == "2026-06-15 07:00 EEST"


def test_empty_keeps_notice_no_token_check():
    app = _app(ReportOutcome(RunState.EMPTY, "не синхнулись", None, None))
    token_calls: list = []
    msg = asyncio.run(fire_report(app, "morning", NOW, token_state_fn=_token_fn(TokenState.EXPIRED, calls=token_calls)))
    assert msg == "не синхнулись"                   # EMPTY is not an auth problem
    assert token_calls == []


def test_error_with_expired_token_becomes_reauth_nudge():
    app = _app(ReportOutcome(RunState.ERROR, "⚠️ не вдалося", None, None))
    msg = asyncio.run(fire_report(app, "evening", NOW, token_state_fn=_token_fn(TokenState.EXPIRED, calls=[])))
    assert msg == REAUTH_NUDGE
    assert app.messenger.sent == [REAUTH_NUDGE]


def test_error_with_valid_token_keeps_error_message():
    app = _app(ReportOutcome(RunState.ERROR, "⚠️ не вдалося", None, None))
    msg = asyncio.run(fire_report(app, "evening", NOW, token_state_fn=_token_fn(TokenState.VALID, calls=[])))
    assert msg == "⚠️ не вдалося"                    # non-auth error → no false re-login nudge


def test_scheduler_registers_two_timezone_jobs():
    async def _t():
        app = SimpleNamespace(config=SimpleNamespace(tz="Europe/Kyiv"))
        sched = ReportScheduler(app, morning="07:30", evening="22:15", token_state_fn=lambda ts: TokenState.VALID)
        s = sched.start()
        jobs = s.get_jobs()
        ids = {j.id for j in jobs}
        s.shutdown(wait=False)
        return ids

    ids = asyncio.run(_t())
    assert ids == {"morning_report", "evening_report"}
