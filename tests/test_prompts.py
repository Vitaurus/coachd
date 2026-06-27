"""Pin the report prompt scar tissue (canonical keys, honesty rules, mode focus).

The corpus is English (the neutral base); the OUTPUT language is set by the
``language`` arg. Structural tokens (MARKER, canonical keys, Garmin endpoint
names, ACWR) are language-independent and must survive any translation.
"""

from __future__ import annotations

from datetime import date

import pytest

from coachd.core.parsing import CANONICAL_KEYS, MARKER
from coachd.core.prompts import (
    build_digest_prompt,
    build_image_instruction,
    build_report_prompt,
    build_system_prompt,
)

WORN = date(2026, 6, 8)
TODAY = date(2026, 6, 15)  # day 8


def test_system_prompt_carries_methodology_and_fragment():
    sp = build_system_prompt("METHOD-RULES", "GARMIN-FRAGMENT")
    assert "METHOD-RULES" in sp
    assert "GARMIN-FRAGMENT" in sp


def test_image_instruction_classifies_and_has_fallback():
    instr = build_image_instruction()
    low = instr.lower()
    # the four branches the LXC reference defined
    assert "food" in low and "calories and macros" in low
    assert "garmin screenshot" in low
    assert "workout plan" in low
    # the none-of-these fallback (#11) — describe + ask, don't free-associate
    assert "anything else" in low and "ask what" in low
    # writes still go through the guard, not bypassed by the photo path
    assert "confirmed before anything is saved" in low


def test_system_prompt_forbids_markdown_for_both_agents():
    # the chat agent has no report tail — the no-markdown rule must be system-level
    sp = build_system_prompt("M", "F", language="Ukrainian")
    assert "markdown" in sp.lower()
    assert "Respond in Ukrainian" in sp


def test_morning_prompt_has_focus_journal_keys_and_dayworn():
    p = build_report_prompt(
        "morning", TODAY, "2026-06-15 07:00 EEST",
        ["2026-06-14 morning: OK"],
        user_name="Oleksa", worn_start=WORN,
    )
    assert "2026-06-14 morning: OK" in p           # journal tail injected
    assert "Oleksa" in p
    assert "READINESS" in p                         # morning focus
    assert "get_sleep_summary" in p
    assert "day 8" in p                             # day_worn = (15-8)+1
    assert MARKER in p
    for key in CANONICAL_KEYS["morning"]:           # canonical schema pinned in prompt
        assert key in p


def test_evening_prompt_has_acwr_and_evening_keys():
    p = build_report_prompt(
        "evening", TODAY, "2026-06-15 22:00 EEST", [],
        user_name="Oleksa", worn_start=WORN,
    )
    assert "LOAD" in p
    assert "ACWR" in p
    assert "get_activities_by_date" in p
    assert "(journal empty" in p                    # empty-journal phrasing
    for key in CANONICAL_KEYS["evening"]:
        assert key in p


def test_honesty_rule_present():
    p = build_report_prompt("morning", TODAY, "now", [], user_name="X", worn_start=WORN)
    assert "baseline still accumulating" in p
    assert "WITHOUT markdown" in p
    assert "250 words" in p


def test_output_language_line_follows_language_arg():
    # the corpus is English but the OUTPUT language is whatever `language` says
    en = build_report_prompt("morning", TODAY, "now", [], user_name="X", worn_start=WORN)
    assert "Respond in English for X" in en          # default
    uk = build_report_prompt(
        "morning", TODAY, "now", [], user_name="X", worn_start=WORN, language="Ukrainian"
    )
    assert "Respond in Ukrainian for X" in uk


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode"):
        build_report_prompt("noon", TODAY, "now", [], user_name="X", worn_start=WORN)


# --- daily-digest cross-agent memory ---------------------------------------- #
def test_evening_prompt_reconciles_with_interactions_row():
    # the evening report must not scold the user for a workout the coach itself
    # prescribed earlier the same day (visible as an 'interactions' journal row)
    p = build_report_prompt(
        "evening", TODAY, "2026-06-15 22:00 EEST", [], user_name="Oleksa", worn_start=WORN
    )
    low = p.lower()
    assert "interactions" in low
    assert "do not fault" in low


def test_morning_prompt_has_no_reconciliation_line():
    # scope: the reconciliation instruction is evening-only
    p = build_report_prompt(
        "morning", TODAY, "now", [], user_name="X", worn_start=WORN
    )
    assert "do not fault" not in p.lower()


def test_digest_prompt_carries_actions_turns_and_language():
    p = build_digest_prompt(
        '- upload_workout {"name":"5k"}', "user: feeling tired", language="Ukrainian"
    )
    assert "upload_workout" in p          # confirmed action (ground truth) present
    assert "user: feeling tired" in p     # conversation present
    assert "Ukrainian" in p               # output language threaded
    assert "one line" in p.lower()        # single-line contract for the tail


def test_digest_prompt_defaults_to_english():
    assert "English" in build_digest_prompt("(none)", "(none)")
