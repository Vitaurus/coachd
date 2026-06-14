"""Pin journal idempotency, the anti-empty probe, dedup, and both record shapes."""

from __future__ import annotations

import json

from coachd.core.journal import Journal

_COUNTER = {"n": 0}


def _fixed_clock():
    """Monotonic fake ts so 'newest' is deterministic without real time."""
    _COUNTER["n"] += 1
    return f"2026-06-14T10:{_COUNTER['n']:02d}:00+03:00"


def _journal(tmp_path):
    _COUNTER["n"] = 0
    return Journal(tmp_path / "journal.jsonl", now=_fixed_clock)


def _morning(verdict="ОК", sleep_h=7.5):
    return (
        f"Ранковий вердикт: {verdict}.\n"
        "===METRICS===\n"
        f'{{"sleep_h":{sleep_h},"rhr":48,"body_battery_charged":80,"verdict":"{verdict}"}}'
    )


def test_record_returns_prose_and_persists(tmp_path):
    j = _journal(tmp_path)
    prose = j.record("2026-06-14", "morning", _morning())
    assert prose.startswith("Ранковий вердикт")
    assert "===METRICS===" not in prose
    recs = j.read_records()
    assert len(recs) == 1
    # valid metrics land FLAT (top-level keys), not nested
    assert recs[0]["sleep_h"] == 7.5
    assert recs[0]["date"] == "2026-06-14"
    assert recs[0]["mode"] == "morning"


def test_record_is_idempotent_per_date_mode(tmp_path):
    j = _journal(tmp_path)
    j.record("2026-06-14", "morning", _morning(verdict="перший", sleep_h=7.0))
    j.record("2026-06-14", "morning", _morning(verdict="другий", sleep_h=8.0))
    recs = j.read_records()
    assert len(recs) == 1, "re-recording same (date,mode) must REPLACE, not duplicate"
    assert recs[0]["sleep_h"] == 8.0
    assert recs[0]["verdict"] == "другий"


def test_different_modes_coexist(tmp_path):
    j = _journal(tmp_path)
    j.record("2026-06-14", "morning", _morning())
    j.record(
        "2026-06-14", "evening",
        'Вечір.\n===METRICS===\n{"steps":9000,"body_battery_now":40,"rhr":50,"verdict":"добре"}',
    )
    assert len(j.read_records()) == 2


def test_fallback_shape_when_no_metrics(tmp_path):
    j = _journal(tmp_path)
    j.record("2026-06-14", "morning", "Лише проза, без блоку метрик")
    rec = j.read_records()[0]
    # fallback shape: nested empty metrics + verdict from first prose line
    assert rec["metrics"] == {}
    assert rec["verdict"] == "Лише проза, без блоку метрик"


def test_probe_ok_when_core_metric_present(tmp_path):
    j = _journal(tmp_path)
    assert j.probe("morning", _morning()) is True


def test_probe_empty_when_core_metrics_blank(tmp_path):
    j = _journal(tmp_path)
    raw = 'x\n===METRICS===\n{"sleep_h":null,"rhr":"","body_battery_charged":[]}'
    assert j.probe("morning", raw) is False  # all core metrics empty → EMPTY → retry


def test_probe_empty_when_no_metrics_block(tmp_path):
    j = _journal(tmp_path)
    assert j.probe("evening", "просто текст") is False


def test_read_skips_malformed_lines(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"date":"2026-06-14","mode":"morning","verdict":"ok"}\n'
        "this is not json\n"
        '{"date":"2026-06-13","mode":"evening","verdict":"prev"}\n',
        encoding="utf-8",
    )
    j = Journal(path)
    recs = j.read_records()
    assert len(recs) == 2  # malformed line skipped, not a crash


def test_tail_dedups_and_reads_both_shapes(tmp_path):
    j = _journal(tmp_path)
    # flat-shape record (valid metrics)
    j.record("2026-06-14", "morning", _morning(verdict="ранок"))
    # fallback nested-shape record
    j.record("2026-06-13", "evening", "Вечірня проза")
    lines = j.tail(10)
    assert any("morning: ранок" in ln for ln in lines)
    assert any("evening: Вечірня проза" in ln for ln in lines)


def test_compact_removes_duplicates(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"ts":"2026-06-14T10:01:00+03:00","date":"2026-06-14","mode":"morning","verdict":"old"}\n'
        '{"ts":"2026-06-14T10:05:00+03:00","date":"2026-06-14","mode":"morning","verdict":"new"}\n',
        encoding="utf-8",
    )
    j = Journal(path)
    before, after = j.compact()
    assert (before, after) == (2, 1)
    assert j.read_records()[0]["verdict"] == "new"  # newest kept


def test_write_is_minified_and_unicode(tmp_path):
    j = _journal(tmp_path)
    j.record("2026-06-14", "morning", _morning(verdict="Готовність"))
    raw = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    assert "Готовність" in raw           # ensure_ascii=False — Cyrillic round-trips
    assert ", " not in raw and ": " not in raw  # minified separators
    json.loads(raw.strip().split("\n")[0])  # valid JSON line
