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
"""

from __future__ import annotations

from datetime import date, timedelta

from .parsing import CANONICAL_KEYS, MARKER

_VALID_MODES = ("morning", "evening")


def build_system_prompt(methodology: str, provider_fragment: str) -> str:
    """Static system prompt: pinned methodology + the data-source tool fragment."""
    return (
        "Ти — персональний коуч з тренувань і відновлення. Дотримуйся методології "
        "нижче як ЖОРСТКИХ правил.\n\n"
        "=== ДЖЕРЕЛО ДАНИХ ===\n"
        f"{provider_fragment}\n\n"
        "=== МЕТОДОЛОГІЯ ===\n"
        f"{methodology}"
    )


def _metrics_block(mode: str) -> str:
    keys = ", ".join(CANONICAL_KEYS[mode])
    return (
        f"Після основного тексту додай РІВНО технічний блок (користувач його НЕ "
        f"побачить — він вирізається в журнал): окремий рядок {MARKER} , під ним ОДИН "
        f"рядок мінімізованого JSON з ключовими метриками сьогодні + поле \"verdict\" "
        f"(одне речення — суть поради). Без markdown-фенсів, нічого після JSON не пиши. "
        f"Канонічні ключі {MARKER} (використовуй САМЕ ці імена, без синонімів, щоб схема "
        f"не пливла): {keys}."
    )


def _journal_block(journal_tail: list[str]) -> str:
    body = "\n".join(journal_tail) if journal_tail else "(журнал порожній — це перший запис)"
    return (
        "ТВІЙ ЖУРНАЛ (останні записи, для тяглості порад):\n"
        f"{body}\n"
        "Спирайся на журнал: чи дотримано минулих порад, що змінилось, не повторюйся дослівно."
    )


def _common_tail(user_name: str, worn_start: date, day_worn: int) -> str:
    return (
        f"ВАЖЛИВО про коротку історію: годинник носиться лише з {worn_start.isoformat()}, "
        f"тобто сьогодні приблизно день {day_worn}. Якщо даних менше ~7 днів — НЕ малюй "
        f"фейкові тренди; чесно напиши 'baseline ще набирається (день {day_worn})' і давай "
        f"оцінку по наявному. НІКОЛИ не вигадуй цифри; якщо метрики за сьогодні нема — "
        f"пропусти її. Перевага trend/weekly/summary-ендпоінтам над важкими поденними "
        f"циклами. Відповідай українською для {user_name}, до 250 слів, чистим текстом "
        f"БЕЗ markdown-розмітки, дружнім але діловим тоном. Не став запитань — це "
        f"одностороннє повідомлення."
    )


def build_report_prompt(
    mode: str,
    on_date: date,
    now_str: str,
    journal_tail: list[str],
    *,
    user_name: str,
    worn_start: date,
) -> str:
    """Build the per-turn report user prompt for ``morning`` or ``evening``."""
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")

    day_worn = (on_date - worn_start).days + 1
    d = on_date.isoformat()
    d7 = (on_date - timedelta(days=7)).isoformat()
    d14 = (on_date - timedelta(days=14)).isoformat()
    d28 = (on_date - timedelta(days=28)).isoformat()
    tail = _common_tail(user_name, worn_start, day_worn)

    if mode == "morning":
        focus = (
            f"Сьогодні {d}. Поточний момент: {now_str}. Фокус ранку: ГОТОВНІСТЬ "
            f"організму, оцінена як ВІДХИЛЕННЯ від норми, а не абсолютні цифри.\n\n"
            f"Дані за СЬОГОДНІ ({d}): сон (get_sleep_summary), HRV (get_hrv_data), "
            f"готовність (get_training_readiness/get_morning_training_readiness), "
            f"Body Battery (get_body_battery), пульс спокою (get_rhr_day).\n"
            f"BASELINE ~7 днів (з {d7} по {d}): get_hrv_trend, get_vo2max_trend, RHR/сон "
            f"за останні дні. Оцінюй HRV сьогодні vs 7-денне середнє, напрям RHR/VO2max.\n"
            f"Висновок: 1) готовність з урахуванням тренду; 2) конкретний план дня "
            f"(інтенсивність чи відпочинок); 3) ключові цифри коротко."
        )
    else:  # evening
        focus = (
            f"Сьогодні {d}. Поточний момент: {now_str}. Фокус вечора: НАВАНТАЖЕННЯ дня "
            f"в контексті тижня/місяця.\n\n"
            f"Дані за СЬОГОДНІ ({d}): активності (get_activities_by_date з {d} по {d}), "
            f"денне зведення (get_user_summary), стрес (get_stress_data), статус "
            f"тренувань (get_training_status).\n"
            f"ТРЕНДИ: get_training_load_trend; get_progress_summary_between_dates за 28 "
            f"днів (з {d28} по {d}); get_weekly_*; активності за 14 днів (з {d14}) для "
            f"патерну.\nACWR: гостре(7д)/хронічне(28д) — >1.3 ризик, <0.8 детренування, "
            f"0.8–1.3 оптимум. Якщо нема повних 28 днів — НЕ рахуй, так і скажи.\n"
            f"Висновок: 1) підсумок дня в контексті тижня; 2) якість тренування (HR-зони), "
            f"якщо було; 3) поради на завтра + рекомендований час відбою."
        )

    return (
        f"{_journal_block(journal_tail)}\n\n"
        f"{focus}\n\n"
        f"{tail}\n\n"
        f"{_metrics_block(mode)}"
    )
