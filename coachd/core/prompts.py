"""Build the system prompt (static, cacheable) and the per-turn report prompt.

Ported from the legacy coach.sh morning/evening prompts. Split deliberately:

  * SYSTEM prompt = methodology + the provider's tool fragment. Static across
    turns → it is the cacheable prefix the cache spike (#2.5) targets.
  * USER prompt = journal tail + mode focus + date/now/window context. Dynamic.

The load-bearing scar tissue preserved verbatim: the honesty rules ("don't fake
trends, baseline is still accumulating"), the 250-word plain-text-no-markdown
constraint, the one-sided-message instruction, and the exact ===METRICS=== block
contract (canonical keys pulled from parsing.CANONICAL_KEYS so the prompt and the
parser can never drift apart).

The corpus is authored in English as the neutral base; the OUTPUT language is set
by the ``language`` arg (the human name of COACH_LANG — "English"/"Ukrainian"),
which fills the "Respond in {language}" instruction. The model is multilingual:
it reads this English methodology and answers in the requested language. Defaults
to English so callers that don't yet thread the language stay correct.
"""

from __future__ import annotations

from datetime import date, timedelta

from .parsing import CANONICAL_KEYS, MARKER

_VALID_MODES = ("morning", "evening")


def build_system_prompt(
    methodology: str, provider_fragment: str, *, language: str = "English"
) -> str:
    """Static system prompt: pinned methodology + the data-source tool fragment.

    Carries the output-language instruction so BOTH agents honour it — the report
    user-prompt reinforces it in _common_tail, but the CHAT agent has no such tail,
    so without this line a chat turn would drift to the language of the (English)
    methodology instead of COACH_LANG."""
    return (
        "You are a personal training and recovery coach. Follow the methodology "
        "below as STRICT rules.\n\n"
        "=== DATA SOURCE ===\n"
        f"{provider_fragment}\n\n"
        "=== METHODOLOGY ===\n"
        f"{methodology}\n\n"
        f"Respond in {language}."
    )


def _metrics_block(mode: str) -> str:
    keys = ", ".join(CANONICAL_KEYS[mode])
    return (
        f"After the main text add EXACTLY one technical block (the user does NOT "
        f"see it — it is stripped into the journal): a separate line {MARKER} , and "
        f"under it ONE line of minified JSON with today's key metrics + a "
        f'"verdict" field (one sentence — the gist of the advice). No markdown '
        f"fences, write nothing after the JSON. Canonical keys for {MARKER} (use "
        f"EXACTLY these names, no synonyms, so the schema can't drift): {keys}."
    )


def _journal_block(journal_tail: list[str]) -> str:
    body = "\n".join(journal_tail) if journal_tail else "(journal empty — this is the first entry)"
    return (
        "YOUR JOURNAL (most recent entries, for advice continuity):\n"
        f"{body}\n"
        "Lean on the journal: was past advice followed, what changed, don't repeat it verbatim."
    )


def _common_tail(user_name: str, worn_start: date, day_worn: int, language: str) -> str:
    return (
        f"IMPORTANT about the short history: the watch has only been worn since "
        f"{worn_start.isoformat()}, so today is roughly day {day_worn}. If there is "
        f"less than ~7 days of data — do NOT draw fake trends; honestly write "
        f"'baseline still accumulating (day {day_worn})' and give an assessment from "
        f"what's available. NEVER invent numbers; if a metric for today is missing — "
        f"skip it. Prefer trend/weekly/summary endpoints over heavy per-day cycles. "
        f"Respond in {language} for {user_name}, up to 250 words, in plain text "
        f"WITHOUT markdown formatting, in a friendly but businesslike tone. Don't ask "
        f"questions — this is a one-way message."
    )


def build_report_prompt(
    mode: str,
    on_date: date,
    now_str: str,
    journal_tail: list[str],
    *,
    user_name: str,
    worn_start: date,
    language: str = "English",
) -> str:
    """Build the per-turn report user prompt for ``morning`` or ``evening``."""
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")

    day_worn = (on_date - worn_start).days + 1
    d = on_date.isoformat()
    d7 = (on_date - timedelta(days=7)).isoformat()
    d14 = (on_date - timedelta(days=14)).isoformat()
    d28 = (on_date - timedelta(days=28)).isoformat()
    tail = _common_tail(user_name, worn_start, day_worn, language)

    if mode == "morning":
        focus = (
            f"Today is {d}. Current moment: {now_str}. Morning focus: body "
            f"READINESS, assessed as a DEVIATION from the norm, not absolute numbers.\n\n"
            f"Data for TODAY ({d}): sleep (get_sleep_summary), HRV (get_hrv_data), "
            f"readiness (get_training_readiness/get_morning_training_readiness), "
            f"Body Battery (get_body_battery), resting heart rate (get_rhr_day).\n"
            f"BASELINE ~7 days (from {d7} to {d}): get_hrv_trend, get_vo2max_trend, "
            f"RHR/sleep over recent days. Assess today's HRV vs the 7-day average, the "
            f"direction of RHR/VO2max.\n"
            f"Conclusion: 1) readiness accounting for the trend; 2) a concrete plan for "
            f"the day (intensity or rest); 3) key numbers, briefly."
        )
    else:  # evening
        focus = (
            f"Today is {d}. Current moment: {now_str}. Evening focus: the day's LOAD "
            f"in the context of the week/month.\n\n"
            f"Data for TODAY ({d}): activities (get_activities_by_date from {d} to {d}), "
            f"daily summary (get_user_summary), stress (get_stress_data), training "
            f"status (get_training_status).\n"
            f"TRENDS: get_training_load_trend; get_progress_summary_between_dates over "
            f"28 days (from {d28} to {d}); get_weekly_*; activities over 14 days (from "
            f"{d14}) for the pattern.\nACWR: acute(7d)/chronic(28d) — >1.3 risk, <0.8 "
            f"detraining, 0.8–1.3 optimal. If there isn't a full 28 days — do NOT "
            f"compute it, say so.\n"
            f"Conclusion: 1) the day's summary in the context of the week; 2) workout "
            f"quality (HR zones), if there was one; 3) advice for tomorrow + a "
            f"recommended bedtime."
        )

    return (
        f"{_journal_block(journal_tail)}\n\n"
        f"{focus}\n\n"
        f"{tail}\n\n"
        f"{_metrics_block(mode)}"
    )
