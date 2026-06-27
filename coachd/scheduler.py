"""Report scheduler — fire morning/evening reports in the user's timezone.

The on-fire logic (:func:`fire_report`) is separated from the APScheduler plumbing
so it is tested without real time or a running loop. It also wires in the #0
re-auth nudge: a token check is done ONLY when a report errors (not before every
run — pre-checking would add a Garmin round-trip to the happy path and feed the
rate limiter). EMPTY keeps the engine's "not synced yet" notice; a true auth
failure becomes a clear "re-login" nudge instead of a vague error.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Awaitable, Callable, Protocol

from .auth.garmin_login import TokenState
from .core.resilience import RunState

_log = logging.getLogger(__name__)

# token_state_fn(tokenstore) -> TokenState
TokenStateFn = Callable[[str], TokenState]


class _AppLike(Protocol):
    config: object
    engine: object
    messenger: object
    strings: object  # i18n.Strings — the re-auth nudge is localized


def format_now(now: datetime) -> str:
    """Match the legacy NOW format: '2026-06-15 07:00 EEST' (local, with zone)."""
    return now.strftime("%Y-%m-%d %H:%M %Z")


async def fire_report(app: _AppLike, mode: str, now: datetime, *, token_state_fn: TokenStateFn) -> str:
    """Run one scheduled report and deliver it. Returns the delivered message.

    On ERROR, classify the token: EXPIRED/MISSING → replace the vague failure with
    the re-auth nudge so the user knows exactly what to do. Otherwise deliver the
    engine's message (OK report, EMPTY notice, or a non-auth error).

    Before the EVENING report, run the daily digest so the day's confirmed
    actions + chat advice land in the journal FIRST — the report then reads them
    through its journal tail and won't contradict the chat coach. A digest failure
    never blocks delivery.
    """
    if mode == "evening":
        digest = getattr(app, "digest", None)
        if digest is not None:
            try:
                await digest.run(now.date())
            except Exception:
                _log.exception("daily digest failed; delivering the report without it")

    outcome = await app.engine.run_report(mode, now.date(), format_now(now))
    message = outcome.message

    if outcome.state is RunState.ERROR:
        state = token_state_fn(app.config.tokenstore)
        if state in (TokenState.EXPIRED, TokenState.MISSING):
            message = app.strings.get("reauth_nudge")

    app.messenger.send(message)
    return message


def _parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.strip().split(":")
    return int(h), int(m)


class ReportScheduler:
    """Cron-fires morning/evening reports in ``app.config.tz``."""

    def __init__(
        self,
        app: _AppLike,
        *,
        morning: str = "07:00",
        evening: str = "22:00",
        token_state_fn: TokenStateFn,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._app = app
        self._morning = _parse_hhmm(morning)
        self._evening = _parse_hhmm(evening)
        self._token_state_fn = token_state_fn
        self._now_fn = now_fn
        self._scheduler = None

    def _tz(self):
        from zoneinfo import ZoneInfo
        return ZoneInfo(self._app.config.tz)

    def _now(self) -> datetime:
        if self._now_fn is not None:
            return self._now_fn()
        return datetime.now(self._tz())

    async def _run(self, mode: str) -> None:
        await fire_report(self._app, mode, self._now(), token_state_fn=self._token_state_fn)

    def start(self):
        """Register the two cron jobs and start the scheduler. Returns it."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        tz = self._tz()
        self._scheduler = AsyncIOScheduler(timezone=tz)
        self._scheduler.add_job(
            self._run, CronTrigger(hour=self._morning[0], minute=self._morning[1], timezone=tz),
            args=["morning"], id="morning_report",
        )
        self._scheduler.add_job(
            self._run, CronTrigger(hour=self._evening[0], minute=self._evening[1], timezone=tz),
            args=["evening"], id="evening_report",
        )
        self._scheduler.start()
        return self._scheduler
