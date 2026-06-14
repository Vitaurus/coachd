"""Live recompute of remaining recovery hours from Garmin readiness records.

This is the rule the legacy author flagged as having "bit three times". The
subtlety that makes it bug-prone, captured here so the rewrite cannot lose it:

  * Garmin emits SEVERAL readiness records per day (a wake-up reset, post-exercise
    resets). Use the NEWEST record that actually has a recovery_time_hours value;
    skip records without one.
  * recovery_time_hours is the remaining hours AT that record's timestamp. The
    watch counts it down ~1:1 with wall-clock, so the live value is
    ``max(0, recovery_time_hours - hours_since_timestamp)``.
  * A FUTURE timestamp (clock skew / timezone bug → negative elapsed) is an
    anomaly. The naive formula would INFLATE recovery; the rule instead forces
    recovery to 0. This explicit override is the actual scar tissue — without it
    a TZ glitch silently reports a huge bogus recovery window.
  * A value > 96h is a Garmin artifact: ignore that record entirely.
  * No usable record ⇒ recovery is complete (0.0), not null.

Timestamps and ``now`` MUST be in the same timezone (Garmin readiness timestamps
are local). Presentation (≤1h → "complete ~0", >1h → "≈N h left") stays in the
prompt/methodology, not here — this returns the raw number only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

# Above this, recovery_time_hours is treated as a Garmin artifact and ignored.
ARTIFACT_MAX_HOURS = 96.0


@dataclass(frozen=True)
class ReadinessRecord:
    """One Garmin training-readiness record (already mapped from the API shape)."""

    timestamp: datetime           # local time, same zone as `now`
    recovery_time_hours: float | None = None


def recompute_recovery_h(records: Iterable[ReadinessRecord], now: datetime) -> float:
    """Remaining recovery hours right now, per the legacy methodology.

    Returns 0.0 when there is no usable record, when the chosen record's
    timestamp is in the future, or when recovery has fully elapsed.
    """
    usable = [
        r for r in records
        if r.recovery_time_hours is not None
        and 0 <= r.recovery_time_hours <= ARTIFACT_MAX_HOURS
    ]
    if not usable:
        return 0.0

    newest = max(usable, key=lambda r: r.timestamp)
    elapsed_h = (now - newest.timestamp).total_seconds() / 3600.0
    if elapsed_h < 0:
        # timestamp in the future → anomaly → recovery already done (NOT inflated)
        return 0.0
    return max(0.0, newest.recovery_time_hours - elapsed_h)
