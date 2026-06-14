"""Pin the live recovery_h recompute — the rule that 'bit three times'."""

from __future__ import annotations

from datetime import datetime, timedelta

from coachd.core.recovery import ReadinessRecord, recompute_recovery_h

# Fixed local "now" — all timestamps share this zone (Garmin readiness is local).
NOW = datetime(2026, 6, 14, 12, 0, 0)


def _rec(hours_ago: float, recovery: float | None) -> ReadinessRecord:
    return ReadinessRecord(
        timestamp=NOW - timedelta(hours=hours_ago),
        recovery_time_hours=recovery,
    )


def test_normal_decay():
    # 10h recovery stamped 3h ago → 7h remain
    assert recompute_recovery_h([_rec(3, 10.0)], NOW) == 7.0


def test_picks_newest_record_with_value():
    records = [
        _rec(8, 20.0),   # older
        _rec(2, 6.0),    # newest WITH value → use this
        _rec(1, None),   # newer but no recovery field → skipped
    ]
    # 6h stamped 2h ago → 4h remain
    assert recompute_recovery_h(records, NOW) == 4.0


def test_skips_records_without_recovery_field():
    records = [_rec(1, None), _rec(5, 8.0)]
    # only the 8h-stamped-5h-ago record is usable → 3h remain
    assert recompute_recovery_h(records, NOW) == 3.0


def test_artifact_above_96_is_ignored():
    records = [_rec(1, 120.0), _rec(4, 10.0)]  # 120 is an artifact → ignore
    assert recompute_recovery_h(records, NOW) == 6.0  # falls back to the 10h record


def test_future_timestamp_returns_zero_not_inflated():
    # timestamp 2h in the FUTURE → naive formula would give 10+2=12; rule forces 0
    rec = ReadinessRecord(timestamp=NOW + timedelta(hours=2), recovery_time_hours=10.0)
    assert recompute_recovery_h([rec], NOW) == 0.0


def test_fully_elapsed_clamps_to_zero():
    assert recompute_recovery_h([_rec(20, 5.0)], NOW) == 0.0


def test_no_records_is_zero():
    assert recompute_recovery_h([], NOW) == 0.0


def test_all_records_missing_value_is_zero():
    assert recompute_recovery_h([_rec(1, None), _rec(2, None)], NOW) == 0.0
