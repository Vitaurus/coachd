"""The coach's persistent memory: one JSON object per line, deduplicated per
(date, mode), written idempotently and atomically.

Ported near-verbatim from the legacy ``journal.py``. Semantics preserved exactly
(each one closed a real production bug):

  * malformed lines are skipped on read, never crash the run;
  * writes are atomic (temp file + ``os.replace``) and minified with
    ``ensure_ascii=False`` so Cyrillic prose round-trips;
  * re-recording the same ``(date, mode)`` REPLACES the prior row — the morning
    job can run twice without duplicating;
  * a valid metrics object lands as FLAT top-level keys; the fallback lands as
    ``{"metrics": {}, "verdict": ...}`` — ``tail`` reads BOTH shapes, and the
    old on-disk journal contains both, so the difference is preserved on purpose;
  * ``probe`` is the anti-empty-sync guard: Garmin often syncs the night AFTER
    the morning timer fires, so a row of nulls must never be written — probe says
    EMPTY and the caller retries instead.

The legacy module was ``__file__``-relative and stdout-driven; here the path is
explicit and ``record`` RETURNS the prose (caller delivers it) so the core stays
pure and testable. Behaviour is otherwise identical.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .parsing import parse_metrics_dict, split_output

# Fields the journal owns; everything else in a record is a real metric.
RESERVED = {"ts", "date", "mode", "verdict", "metrics"}

# Core metrics that prove the fetch returned real data for each mode. If none of
# these is present, the sync hasn't landed yet → EMPTY → retry, don't persist.
CORE_KEYS: dict[str, tuple[str, ...]] = {
    "morning": ("sleep_h", "rhr", "body_battery_charged"),
    "evening": ("steps", "body_battery_now", "rhr"),
}


def _fmt_val(v: object) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, float):
        return "%g" % v
    return str(v)


def _local_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class Journal:
    """A journal file at ``path``. ``now`` is injected for deterministic tests."""

    def __init__(self, path: str | Path, *, now: Callable[[], str] = _local_now_iso) -> None:
        self.path = Path(path)
        self._now = now

    # --- storage ---------------------------------------------------------- #
    def read_records(self) -> list[dict]:
        recs: list[dict] = []
        if not self.path.exists():
            return recs
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        continue  # skip malformed lines, never crash
        except Exception:
            return recs
        return recs

    def write_records(self, records: list[dict]) -> None:
        """Atomically rewrite the whole journal (minified JSON lines)."""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, self.path)

    # --- helpers ---------------------------------------------------------- #
    @staticmethod
    def _ts(r: dict) -> datetime:
        """A record's ts as a comparable aware datetime; missing/bad sorts oldest."""
        ts = r.get("ts")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)

    @classmethod
    def dedup_latest(cls, records: list[dict]) -> list[dict]:
        """Newest record per (date, mode), sorted chronologically by ts."""
        best: dict[tuple, dict] = {}
        for r in records:
            key = (r.get("date"), r.get("mode"))
            if key not in best or cls._ts(r) >= cls._ts(best[key]):
                best[key] = r
        return sorted(best.values(), key=cls._ts)

    # --- commands --------------------------------------------------------- #
    def record(self, date: str, mode: str, raw: str) -> str:
        """Split ``raw``, persist the metrics row idempotently, return the prose.

        The prose is computed and returned first so delivery is never lost to a
        metrics-parsing problem. A valid metrics object is stored flat; any
        failure falls back to ``{"metrics": {}, "verdict": <first prose line>}``.
        """
        split = split_output(raw)
        prose = split.prose

        parsed = parse_metrics_dict(split.metrics_line)
        rec: dict | None = dict(parsed) if parsed is not None else None

        if rec is None:
            first = next((ln.strip() for ln in prose.split("\n") if ln.strip()), "")
            rec = {"metrics": {}, "verdict": first[:120]}

        rec["ts"] = self._now()
        rec["date"] = date
        rec["mode"] = mode

        # idempotent: drop any existing (date, mode) row, then append the new one
        try:
            kept = [
                r for r in self.read_records()
                if (r.get("date"), r.get("mode")) != (date, mode)
            ]
            kept.append(rec)
            self.write_records(kept)
        except Exception:
            pass  # prose is already computed; never fail delivery over a write

        return prose

    def probe(self, mode: str, raw: str) -> bool:
        """True if at least one core metric for ``mode`` is non-empty, else False.

        Non-empty means not in ``(None, "", [])``. False ⇒ EMPTY ⇒ retry.
        """
        split = split_output(raw)
        metrics = parse_metrics_dict(split.metrics_line) or {}
        core = CORE_KEYS.get(mode, ())
        return any(metrics.get(k) not in (None, "", []) for k in core)

    def tail(self, n: int) -> list[str]:
        """Last ``n`` deduplicated records as compact one-line strings."""
        recs = self.dedup_latest(self.read_records())
        out: list[str] = []
        for r in recs[-n:]:
            date = r.get("date", "?")
            mode = r.get("mode", "?")
            verdict = r.get("verdict") or ""
            items = [(k, v) for k, v in r.items() if k not in RESERVED]
            m = r.get("metrics")
            if isinstance(m, dict):
                items.extend(m.items())
            kvs = " ".join("%s=%s" % (k, _fmt_val(v)) for k, v in items[:6])
            line = "%s %s: %s" % (date, mode, verdict)
            if kvs:
                line += "  [%s]" % kvs
            out.append(line)
        return out

    def compact(self) -> tuple[int, int]:
        """Rewrite keeping only the newest row per (date, mode). Returns (before, after)."""
        recs = self.read_records()
        before = len(recs)
        deduped = self.dedup_latest(recs)
        self.write_records(deduped)
        return before, len(deduped)
