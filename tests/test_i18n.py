"""i18n catalog integrity + Strings behaviour (pure, no I/O)."""

from __future__ import annotations

import re
import string

import pytest

from coachd.core.i18n import (
    CATALOG,
    DEFAULT,
    LANGUAGE_NAMES,
    SUPPORTED,
    TODAY_MARKER,
    Strings,
)


def _placeholders(text: str) -> set[str]:
    # the field names referenced by str.format ({tool}, {sched}, …); ignore {{}}
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_key_present_in_every_language():
    # a missing translation is the bug the catalog exists to prevent
    for key, variants in CATALOG.items():
        for lang in SUPPORTED:
            assert lang in variants, f"{key!r} missing {lang!r}"
            assert variants[lang].strip(), f"{key!r}[{lang!r}] is empty"


def test_placeholder_parity_across_languages():
    # {sched} in en but not uk → .format breaks on one language only
    for key, variants in CATALOG.items():
        per_lang = {lang: _placeholders(variants[lang]) for lang in SUPPORTED}
        first = per_lang[SUPPORTED[0]]
        for lang, ph in per_lang.items():
            assert ph == first, f"{key!r} placeholder mismatch: {lang}={ph} vs {first}"


def test_default_language_is_supported():
    assert DEFAULT in SUPPORTED


def test_language_names_cover_supported():
    for lang in SUPPORTED:
        assert lang in LANGUAGE_NAMES and LANGUAGE_NAMES[lang]


def test_get_returns_selected_language():
    assert Strings("uk").get("ack") == "⏳ дивлюсь дані…"
    assert Strings("en").get("ack") == "⏳ checking your data…"


def test_get_formats_placeholders():
    out = Strings("en").get("exec_done", tool="upload_workout")
    assert out == "✓ Done: upload_workout."
    out_uk = Strings("uk").get("exec_created_scheduled", sched="2026-06-17")
    assert "2026-06-17" in out_uk


def test_unknown_language_falls_back_to_default():
    # an out-of-set lang collapses to DEFAULT at construction
    s = Strings("fr")
    assert s.lang == DEFAULT
    assert s.get("ack") == CATALOG["ack"][DEFAULT]


def test_missing_key_in_lang_falls_back_to_default(monkeypatch):
    # simulate a half-translated catalog: uk missing → en used, no KeyError
    monkeypatch.setitem(CATALOG, "_probe", {"en": "english only"})
    assert Strings("uk").get("_probe") == "english only"


def test_today_marker_is_english_and_nonempty():
    # the date marker is part of the prompt contract — must be ASCII English
    assert TODAY_MARKER == "Today:"
    assert re.fullmatch(r"[\x00-\x7f]+", TODAY_MARKER)
