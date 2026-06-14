"""Pin the retry + anti-empty guard loop (Garmin sync-lag behaviour)."""

from __future__ import annotations

from coachd.core.resilience import RetryPolicy, RunState, classify, run_with_retry

_FAST = RetryPolicy(max_tries=3, retry_wait_s=240.0)


def _has_core(output: str) -> bool:
    return "DATA" in output


# --- classify --------------------------------------------------------------- #
def test_classify_error_on_nonzero_rc():
    assert classify(1, "DATA here", has_core_data=_has_core) is RunState.ERROR


def test_classify_error_on_blank_output():
    assert classify(0, "   \n ", has_core_data=_has_core) is RunState.ERROR


def test_classify_empty_when_no_core_data():
    assert classify(0, "prose only, sync not landed", has_core_data=_has_core) is RunState.EMPTY


def test_classify_ok():
    assert classify(0, "prose + DATA", has_core_data=_has_core) is RunState.OK


# --- run_with_retry --------------------------------------------------------- #
def test_ok_on_first_attempt_no_sleep():
    sleeps: list[float] = []
    state, out = run_with_retry(
        run=lambda: (0, "DATA ready"),
        has_core_data=_has_core,
        policy=_FAST,
        sleep=sleeps.append,
    )
    assert state is RunState.OK
    assert sleeps == []  # success first try → never sleeps


def test_empty_then_ok_sleeps_once():
    outputs = iter([(0, "empty prose"), (0, "now DATA")])
    sleeps: list[float] = []
    state, out = run_with_retry(
        run=lambda: next(outputs),
        has_core_data=_has_core,
        policy=_FAST,
        sleep=sleeps.append,
    )
    assert state is RunState.OK
    assert out == "now DATA"
    assert sleeps == [240.0]  # one pause between the two attempts


def test_all_empty_exhausts_tries_no_sleep_after_last():
    sleeps: list[float] = []
    attempts: list[tuple[int, RunState]] = []
    state, out = run_with_retry(
        run=lambda: (0, "still syncing"),
        has_core_data=_has_core,
        policy=_FAST,
        sleep=sleeps.append,
        on_attempt=lambda n, s: attempts.append((n, s)),
    )
    assert state is RunState.EMPTY
    assert len(attempts) == 3
    assert sleeps == [240.0, 240.0]  # 3 tries → 2 sleeps, none after the last


def test_error_state_propagates():
    state, out = run_with_retry(
        run=lambda: (137, ""),
        has_core_data=_has_core,
        policy=RetryPolicy(max_tries=1, retry_wait_s=1.0),
        sleep=lambda _s: None,
    )
    assert state is RunState.ERROR
