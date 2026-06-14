"""Retry + anti-empty guard for a coach run.

Ported from the legacy ``coach.sh`` retry loop. Garmin Connect frequently syncs
the night's data AFTER the morning timer fires, so the first fetch comes back
EMPTY even though nothing is wrong — the watch just hasn't uploaded yet. The
guard retries with a pause and, if still empty after all tries, reports a clear
"not synced yet" notice WITHOUT writing a row of nulls into the journal.

The classification (ok / empty / error) is pure; the agent call and the sleep
are injected so the loop is fully testable with zero real time elapsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class RunState(str, Enum):
    OK = "ok"        # produced usable output with at least one core metric
    EMPTY = "empty"  # ran, but the metrics block has no core data → sync not landed
    ERROR = "error"  # non-zero exit or blank output


@dataclass(frozen=True)
class RetryPolicy:
    max_tries: int = 3
    retry_wait_s: float = 240.0  # Garmin Connect sync-lag pause between tries


def classify(rc: int, output: str, *, has_core_data: Callable[[str], bool]) -> RunState:
    """ERROR on non-zero rc or blank output; else EMPTY/OK by the core-data probe."""
    if rc != 0 or not output.strip():
        return RunState.ERROR
    return RunState.OK if has_core_data(output) else RunState.EMPTY


def run_with_retry(
    run: Callable[[], tuple[int, str]],
    has_core_data: Callable[[str], bool],
    policy: RetryPolicy = RetryPolicy(),
    *,
    sleep: Callable[[float], None],
    on_attempt: Callable[[int, RunState], None] | None = None,
) -> tuple[RunState, str]:
    """Run up to ``policy.max_tries`` times, pausing between tries, stopping on OK.

    ``run`` returns ``(rc, output)``. Returns the final ``(state, output)`` — the
    output of the last attempt, OK as soon as one succeeds. No sleep after the
    final attempt (or after an OK break).
    """
    state = RunState.ERROR
    output = ""
    for attempt in range(1, policy.max_tries + 1):
        rc, output = run()
        state = classify(rc, output, has_core_data=has_core_data)
        if on_attempt is not None:
            on_attempt(attempt, state)
        if state is RunState.OK:
            break
        if attempt < policy.max_tries:
            sleep(policy.retry_wait_s)
    return state, output
