"""Split the coach's raw LLM output into prose (→ Telegram) and a metrics block
(→ journal), and pin the canonical metrics schema.

Ported near-verbatim from the legacy ``journal.py`` (``extract_metrics_line`` /
``cmd_record`` split). The fence-stripping and "first non-empty line after the
marker" rules exist because the model occasionally wraps the JSON in a
```json fence despite being told not to — losing that handling silently drops
every fenced metrics block on the floor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# The line that separates the human-facing prose from the machine metrics block.
MARKER = "===METRICS==="

# Frozen canonical metrics keys. The schema must NOT drift — synonyms are
# forbidden — so journal records stay comparable across versions. Ported exactly
# from the legacy morning/evening prompts (coach.sh). A test pins these tuples;
# changing them is a deliberate, reviewed schema migration, never an accident.
CANONICAL_KEYS: dict[str, tuple[str, ...]] = {
    "morning": (
        "day_worn", "age", "sleep_h", "sleep_score", "deep_pct", "rem_pct",
        "hrv_night", "hrv_status", "rhr", "rhr_prev", "body_battery_charged",
        "readiness_status", "recovery_h", "verdict",
    ),
    "evening": (
        "day_worn", "age", "trained", "main_activity", "training_effect",
        "steps", "stress_avg", "body_battery_now", "rhr", "readiness_status",
        "acwr", "verdict",
    ),
}


@dataclass(frozen=True)
class SplitOutput:
    """Result of splitting raw LLM output on the ``===METRICS===`` marker."""

    prose: str                  # everything before the marker, stripped (→ Telegram)
    metrics_line: str | None    # first non-empty line after the marker, fences stripped
    had_marker: bool            # whether the marker was present at all


def extract_metrics_line(after_lines: list[str]) -> str | None:
    """First non-empty line after the marker, with optional ```json / ``` fences
    stripped. Returns ``None`` if there is no usable line.

    A bare fence line (``` or ```json with nothing else) is skipped to the next
    line; a trailing fence on the JSON line is trimmed.
    """
    for ln in after_lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("```"):
            s = s.lstrip("`").strip()
            if s.lower().startswith("json"):
                s = s[4:].strip()
            if not s:            # a bare fence line -> skip to the next line
                continue
        if s.endswith("```"):
            s = s[:-3].strip()
        return s
    return None


def split_output(raw: str) -> SplitOutput:
    """Split ``raw`` on the first line equal to ``===METRICS===``.

    With no marker, the whole (stripped) text is prose and there is no metrics
    line — the caller decides the fallback. Prose is always computed so Telegram
    delivery is never blocked on metrics parsing.
    """
    lines = raw.split("\n")

    marker_idx: int | None = None
    for i, ln in enumerate(lines):
        if ln.strip() == MARKER:
            marker_idx = i
            break

    if marker_idx is None:
        return SplitOutput(prose=raw.strip(), metrics_line=None, had_marker=False)

    prose = "\n".join(lines[:marker_idx]).strip()
    metrics_line = extract_metrics_line(lines[marker_idx + 1:])
    return SplitOutput(prose=prose, metrics_line=metrics_line, had_marker=True)


def parse_metrics_dict(metrics_line: str | None) -> dict | None:
    """Parse a metrics line into a dict, or ``None`` on any failure.

    Fault tolerant by design: a non-dict JSON value, a parse error, or ``None``
    input all return ``None`` so the caller can fall back without crashing.
    """
    if metrics_line is None:
        return None
    try:
        parsed = json.loads(metrics_line)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
