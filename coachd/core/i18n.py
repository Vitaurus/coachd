"""Localization for coachd — UA + EN, selected per-instance by ``COACH_LANG``.

Two things live here, kept strictly apart from the rest of the codebase:

  * the USER-FACING string CATALOG (ack, confirm captions, executor status,
    report headers, errors) keyed ``key -> {lang -> text}``; and
  * the constants that the MODEL-FACING English prompt corpus needs at runtime
    (``LANGUAGE_NAMES`` for the "Respond in {language}" line, ``TODAY_MARKER``
    single-sourcing the injected date marker so ``chat.py`` and the tool
    fragments can never drift — mirrors ``parsing.MARKER`` for ``===METRICS===``).

This is the ONLY source module allowed to carry non-English (Cyrillic) text; a
regression test enforces that every other ``coachd/**.py`` is English-only, which
is what proves the extraction is complete. Pure, no I/O — a ``Strings(lang)`` is
built once at the composition root and injected wherever a user-facing string is
emitted.
"""

from __future__ import annotations

SUPPORTED: tuple[str, ...] = ("en", "uk")
DEFAULT: str = "en"

# Human names go into the ENGLISH system prompt's "Respond in {language}" line,
# so they are themselves English regardless of the selected output language.
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "uk": "Ukrainian"}

# The date marker injected into the chat prompt (chat.py) and referenced by the
# tool fragments (garmin_provider, composite_tools) for relative-date resolution.
# Single-sourced here so the marker the model is TOLD to read and the marker we
# actually EMIT can never drift apart. Always English (part of the prompt
# contract, not user-facing chrome).
TODAY_MARKER: str = "Today:"

# key -> {lang -> text}. Placeholders ({tool}, {sched}, {nonce}, {exc}, {tries},
# {mode}, {date}) must be IDENTICAL across languages (a parity test enforces it).
CATALOG: dict[str, dict[str, str]] = {
    # --- telegram_bot: ack + callback replies ---------------------------- #
    "ack": {
        "en": "⏳ checking your data…",
        "uk": "⏳ дивлюсь дані…",
    },
    "cb_already_handled": {
        "en": "⏱ This action was already handled or cancelled.",
        "uk": "⏱ Дію вже оброблено або скасовано.",
    },
    "cb_exec_failed": {
        "en": "⚠️ Could not run the action: {exc}",
        "uk": "⚠️ Не вдалося виконати дію: {exc}",
    },
    "cb_cancelled": {
        "en": "✗ Cancelled.",
        "uk": "✗ Скасовано.",
    },
    "cb_cancel_already": {
        "en": "This action was already handled.",
        "uk": "Дію вже оброблено.",
    },
    # --- write_guard: confirm caption (two fragments + an `if sched`) ----- #
    "confirm_needs_approval": {
        "en": "⏸ Action needs confirmation: {tool}{when}\nConfirm or cancel in Telegram (#{nonce}).",
        "uk": "⏸ Дія потребує підтвердження: {tool}{when}\nПідтвердь або скасуй у Telegram (#{nonce}).",
    },
    "confirm_scheduled_suffix": {
        "en": "\n📅 will be scheduled for {sched}",
        "uk": "\n📅 буде заплановано на {sched}",
    },
    # --- garmin_mcp_client: executor status lines ------------------------ #
    "exec_done": {
        "en": "✓ Done: {tool}.",
        "uk": "✓ Виконано: {tool}.",
    },
    "exec_created_scheduled": {
        "en": "✓ Created and scheduled for {sched}.",
        "uk": "✓ Створено і заплановано на {sched}.",
    },
    "exec_created_no_id": {
        "en": (
            "⚠️ Created the workout in the library, but could not determine its id "
            "to schedule it for {sched}. Say “schedule” — I'll schedule it separately."
        ),
        "uk": (
            "⚠️ Створив тренування у бібліотеці, але не вдалося визначити його id, "
            "щоб запланувати на {sched}. Скажи «заплануй» — заплоную окремо."
        ),
    },
    "exec_created_sched_failed": {
        "en": (
            "⚠️ Created the workout in the library, but could not schedule it for "
            "{sched}: {exc}. Say “schedule” to retry."
        ),
        "uk": (
            "⚠️ Створив тренування у бібліотеці, але не вдалося запланувати на "
            "{sched}: {exc}. Скажи «заплануй» щоб повторити."
        ),
    },
    # --- engine: report headers + skip/fail notices ---------------------- #
    "header_morning": {
        "en": "🌅 Garmin morning",
        "uk": "🌅 Garmin ранок",
    },
    "header_evening": {
        "en": "🌙 Garmin evening",
        "uk": "🌙 Garmin вечір",
    },
    "report_empty": {
        "en": (
            "Fresh watch data hasn't synced to Garmin Connect yet ({tries} tries). "
            "Report skipped — sync your watch and run it again."
        ),
        "uk": (
            "Свіжі дані з годинника ще не синхнулись у Garmin Connect ({tries} спроб). "
            "Звіт пропущено — синхронізуй годинник і запусти ще раз."
        ),
    },
    "report_error": {
        "en": "⚠️ Garmin coach ({mode}, {date}): could not get the analysis. Try later.",
        "uk": "⚠️ Garmin coach ({mode}, {date}): не вдалося отримати аналіз. Спробуй пізніше.",
    },
    # --- scheduler: token-expired nag ------------------------------------ #
    "reauth_nudge": {
        "en": (
            "⚠️ Garmin login expired — reports stopped. Re-login:\n"
            "docker compose run --rm coachd login"
        ),
        "uk": (
            "⚠️ Garmin-логін протух — звіти зупинились. Перелогінься:\n"
            "docker compose run --rm coachd login"
        ),
    },
    # --- chat: user-facing reply fallbacks ------------------------------- #
    "chat_done": {
        "en": "Done.",
        "uk": "Готово.",
    },
    "chat_error": {
        "en": "Could not process the request right now. Please try again.",
        "uk": "Не вдалося обробити запит зараз. Спробуй ще раз.",
    },
}


class Strings:
    """A language-bound view over the catalog. Built once per instance from
    ``config.lang`` and injected wherever a user-facing string is emitted.

    ``get`` falls back to the DEFAULT language if the key is missing in the
    selected language, so a half-translated catalog degrades gracefully rather
    than ``KeyError``-ing at runtime. The completeness test makes the fallback
    a belt-and-suspenders guard that never fires in practice."""

    def __init__(self, lang: str) -> None:
        self._lang = lang if lang in SUPPORTED else DEFAULT

    @property
    def lang(self) -> str:
        return self._lang

    def get(self, key: str, **fmt: object) -> str:
        variants = CATALOG[key]
        template = variants.get(self._lang) or variants[DEFAULT]
        return template.format(**fmt) if fmt else template
