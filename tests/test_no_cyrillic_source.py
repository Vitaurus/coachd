"""Boil-the-ocean completeness guard: the source is English-only.

After localization, every user-facing/model-facing string must live either in the
i18n catalog (translated) or as the English prompt base — never as a stray
hardcoded Ukrainian literal. This test proves the extraction is COMPLETE: if any
future edit drops a Cyrillic string into the source (a forgotten button label,
an un-cataloged status line), CI goes red instead of shipping a half-localized
build. The catalog itself (core/i18n.py) is the one allowed exception.
"""

from __future__ import annotations

import re
from pathlib import Path

import coachd

# Cyrillic Unicode block (covers Ukrainian letters + ё/ъ etc.)
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

# the ONLY module allowed to carry non-English text — it IS the translation table
_ALLOWLIST = {"i18n.py"}

_PKG_ROOT = Path(coachd.__file__).parent


def _sources() -> list[Path]:
    return [p for p in _PKG_ROOT.rglob("*.py") if p.name not in _ALLOWLIST]


def test_source_modules_are_english_only():
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _CYRILLIC.search(line):
                rel = path.relative_to(_PKG_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Cyrillic found in source (move it to the i18n catalog or translate to "
        "English):\n" + "\n".join(offenders)
    )


def test_guard_actually_scans_something():
    # a guard that scans zero files would pass vacuously — pin the package was found
    assert len(_sources()) > 5
