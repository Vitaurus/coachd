"""Bounded, persistent chat history (architecture decision #5).

Replaces the legacy global HIST/OFFS dict. Kept SEPARATE from the reports journal
(different concern, different lifetime). Persisted so the coach does not forget
the conversation across a container restart, and bounded per chat so the context
(and token cost) stays capped.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Turn:
    role: str   # "user" | "assistant"
    text: str
    ts: str


class SessionStore:
    """Per-chat history at ``path``, capped to the last ``max_turns`` entries."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_turns: int = 20,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._max = max_turns
        self._now = now
        self._data: dict[str, list[Turn]] = self._load()

    def _load(self) -> dict[str, list[Turn]]:
        out: dict[str, list[Turn]] = {}
        if not self.path.exists():
            return out
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return out  # malformed → start empty, never crash
        if not isinstance(raw, dict):
            return out
        for chat, turns in raw.items():
            try:
                out[str(chat)] = [Turn(**t) for t in turns]
            except Exception:
                continue
        return out

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {c: [asdict(t) for t in turns] for c, turns in self._data.items()},
                f, ensure_ascii=False,
            )
        os.replace(tmp, self.path)

    def append(self, chat_id: object, role: str, text: str) -> None:
        key = str(chat_id)
        turns = self._data.get(key, [])
        turns.append(Turn(role=role, text=text, ts=self._now()))
        # keep only the most recent max_turns
        self._data[key] = turns[-self._max:]
        self._save()

    def history(self, chat_id: object) -> list[Turn]:
        return list(self._data.get(str(chat_id), []))

    def clear(self, chat_id: object) -> None:
        if str(chat_id) in self._data:
            del self._data[str(chat_id)]
            self._save()
