"""Pin the report prompt scar tissue (canonical keys, honesty rules, mode focus)."""

from __future__ import annotations

from datetime import date

import pytest

from coachd.core.parsing import CANONICAL_KEYS, MARKER
from coachd.core.prompts import build_report_prompt, build_system_prompt

WORN = date(2026, 6, 8)
TODAY = date(2026, 6, 15)  # day 8


def test_system_prompt_carries_methodology_and_fragment():
    sp = build_system_prompt("METHOD-RULES", "GARMIN-FRAGMENT")
    assert "METHOD-RULES" in sp
    assert "GARMIN-FRAGMENT" in sp


def test_morning_prompt_has_focus_journal_keys_and_dayworn():
    p = build_report_prompt(
        "morning", TODAY, "2026-06-15 07:00 EEST",
        ["2026-06-14 morning: ОК"],
        user_name="Віталій", worn_start=WORN,
    )
    assert "2026-06-14 morning: ОК" in p          # journal tail injected
    assert "Віталій" in p
    assert "ГОТОВНІСТЬ" in p                       # morning focus
    assert "get_sleep_summary" in p
    assert "день 8" in p                           # day_worn = (15-8)+1
    assert MARKER in p
    for key in CANONICAL_KEYS["morning"]:          # canonical schema pinned in prompt
        assert key in p


def test_evening_prompt_has_acwr_and_evening_keys():
    p = build_report_prompt(
        "evening", TODAY, "2026-06-15 22:00 EEST", [],
        user_name="Віталій", worn_start=WORN,
    )
    assert "НАВАНТАЖЕННЯ" in p
    assert "ACWR" in p
    assert "get_activities_by_date" in p
    assert "(журнал порожній" in p                 # empty-journal phrasing
    for key in CANONICAL_KEYS["evening"]:
        assert key in p


def test_honesty_rule_present():
    p = build_report_prompt("morning", TODAY, "now", [], user_name="X", worn_start=WORN)
    assert "baseline ще набирається" in p
    assert "БЕЗ markdown" in p
    assert "250 слів" in p


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode"):
        build_report_prompt("noon", TODAY, "now", [], user_name="X", worn_start=WORN)
