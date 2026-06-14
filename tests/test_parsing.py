"""Pin the prose/metrics split and the frozen canonical schema."""

from __future__ import annotations

from coachd.core.parsing import (
    CANONICAL_KEYS,
    MARKER,
    parse_metrics_dict,
    split_output,
)

_MORNING = (
    "Доброго ранку! Готовність висока, можна якісне тренування.\n"
    "Сон 7.5 год, HRV у нормі.\n"
    f"{MARKER}\n"
    '{"sleep_h":7.5,"rhr":48,"hrv_night":62,"verdict":"якісне ОК"}'
)


def test_split_prose_and_metrics():
    out = split_output(_MORNING)
    assert out.had_marker is True
    assert out.prose.startswith("Доброго ранку")
    assert MARKER not in out.prose  # marker line never leaks into Telegram prose
    m = parse_metrics_dict(out.metrics_line)
    assert m == {"sleep_h": 7.5, "rhr": 48, "hrv_night": 62, "verdict": "якісне ОК"}


def test_no_marker_is_all_prose():
    out = split_output("просто текст без метрик")
    assert out.had_marker is False
    assert out.prose == "просто текст без метрик"
    assert out.metrics_line is None
    assert parse_metrics_dict(out.metrics_line) is None


def test_strips_json_fence():
    raw = f"вердикт\n{MARKER}\n```json\n" '{"steps":8000}\n' "```"
    out = split_output(raw)
    assert parse_metrics_dict(out.metrics_line) == {"steps": 8000}


def test_strips_inline_fence_on_same_line():
    raw = f"x\n{MARKER}\n" '```json {"steps":8000}```'
    out = split_output(raw)
    assert parse_metrics_dict(out.metrics_line) == {"steps": 8000}


def test_ignores_trailing_text_after_marker_uses_first_nonempty_line():
    raw = f"x\n{MARKER}\n\n" '{"steps":1}\n' "якийсь хвіст\n"
    out = split_output(raw)
    # first non-empty line after the marker wins
    assert parse_metrics_dict(out.metrics_line) == {"steps": 1}


def test_parse_metrics_fault_tolerant():
    assert parse_metrics_dict(None) is None
    assert parse_metrics_dict("not json") is None
    assert parse_metrics_dict("[1,2,3]") is None        # JSON array, not an object
    assert parse_metrics_dict('"a string"') is None     # JSON scalar


def test_canonical_keys_are_frozen():
    """Schema drift guard: these exact tuples are load-bearing for journal
    comparability. Changing them is a deliberate migration, never incidental."""
    assert CANONICAL_KEYS["morning"] == (
        "day_worn", "age", "sleep_h", "sleep_score", "deep_pct", "rem_pct",
        "hrv_night", "hrv_status", "rhr", "rhr_prev", "body_battery_charged",
        "readiness_status", "recovery_h", "verdict",
    )
    assert CANONICAL_KEYS["evening"] == (
        "day_worn", "age", "trained", "main_activity", "training_effect",
        "steps", "stress_avg", "body_battery_now", "rhr", "readiness_status",
        "acwr", "verdict",
    )
    # verdict closes every mode; no duplicate keys within a mode
    for mode, keys in CANONICAL_KEYS.items():
        assert keys[-1] == "verdict"
        assert len(keys) == len(set(keys)), f"duplicate key in {mode}"
