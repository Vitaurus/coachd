"""Durable store of pending write-actions awaiting user confirmation.

This closes the outside voice's scariest gap: a workout proposed by the agent,
confirmed by the user minutes later, must execute exactly once — even if the
container restarted in between. So the proposal is persisted to disk keyed by a
nonce, and ``confirm`` is idempotent: confirming a nonce that is already used,
cancelled, or unknown is a NO-OP that returns ``None``. A stale "Confirm" tap
after a restart can therefore never fire a phantom write.

The nonce IS the idempotency key (unique per proposal, single-use). Writes are
atomic (temp + replace) and survive process restart on reload.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

PENDING = "pending"
USED = "used"
CANCELLED = "cancelled"


def _uuid_nonce() -> str:
    return uuid.uuid4().hex[:12]


def _utc_now() -> str:
    # imported lazily-style to keep a single clock seam; injected in tests
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PendingAction:
    nonce: str
    tool: str
    input: dict
    created_ts: str
    status: str = PENDING


class PendingStore:
    """Nonce-keyed pending actions, persisted at ``path``."""

    def __init__(
        self,
        path: str | Path,
        *,
        nonce_factory: Callable[[], str] = _uuid_nonce,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._nonce_factory = nonce_factory
        self._now = now
        self._actions: dict[str, PendingAction] = self._load()

    # --- persistence ------------------------------------------------------ #
    def _load(self) -> dict[str, PendingAction]:
        out: dict[str, PendingAction] = {}
        if not self.path.exists():
            return out
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return out  # malformed → start empty, never crash
        if not isinstance(raw, dict):
            return out
        for nonce, rec in raw.items():
            try:
                out[nonce] = PendingAction(**rec)
            except Exception:
                continue  # skip malformed entry
        return out

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({n: asdict(a) for n, a in self._actions.items()}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    # --- operations ------------------------------------------------------- #
    def put(self, tool: str, tool_input: dict, *, nonce: str | None = None) -> PendingAction:
        """Persist a new pending action and return it."""
        n = nonce or self._nonce_factory()
        action = PendingAction(nonce=n, tool=tool, input=dict(tool_input), created_ts=self._now())
        self._actions[n] = action
        self._save()
        return action

    def get(self, nonce: str) -> PendingAction | None:
        return self._actions.get(nonce)

    def list_pending(self) -> list[PendingAction]:
        """All actions still awaiting confirmation (status == pending)."""
        return [a for a in self._actions.values() if a.status == PENDING]

    def confirm(self, nonce: str) -> PendingAction | None:
        """Mark a pending action used and return it — exactly once.

        Returns ``None`` (no-op) for an unknown, already-used, or cancelled nonce.
        This is the restart-safe single-execute guarantee.
        """
        action = self._actions.get(nonce)
        if action is None or action.status != PENDING:
            return None
        used = replace(action, status=USED)
        self._actions[nonce] = used
        self._save()
        return used

    def cancel(self, nonce: str) -> bool:
        """Mark a pending action cancelled. Returns True if it was pending."""
        action = self._actions.get(nonce)
        if action is None or action.status != PENDING:
            return False
        self._actions[nonce] = replace(action, status=CANCELLED)
        self._save()
        return True

    def purge_resolved(self) -> int:
        """Drop used/cancelled actions; return how many were removed."""
        before = len(self._actions)
        self._actions = {n: a for n, a in self._actions.items() if a.status == PENDING}
        self._save()
        return before - len(self._actions)
