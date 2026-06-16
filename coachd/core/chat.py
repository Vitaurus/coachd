"""ChatEngine — one interactive chat turn over the write-guarded agent.

The chat agent has the read + write tools and the write-guard, so when the model
decides to create/upload a workout the guard PARKS it (deny) instead of executing.
run_chat detects which pending actions were newly parked this turn (by diffing the
pending store) and returns them so the bot can ask the user to confirm.

Conversation context comes from the bounded SessionStore (decision #5). The
methodology + tool fragment live in the agent's system prompt (set at build time),
so this only renders the recent dialogue + the new message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..ports.llm import LLMError, LLMPort
from .i18n import TODAY_MARKER
from .pending import PendingAction, PendingStore
from .session_store import SessionStore

# Weekday names (Mon=0) — model-facing prompt context, so English (the prompt
# base), not the output language. Explicit map, no locale dependency.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class ChatReply:
    text: str
    pending: list[PendingAction]   # actions parked this turn, awaiting confirmation
    cost_usd: float | None = None


class ChatEngine:
    def __init__(
        self,
        *,
        chat_agent: LLMPort,
        sessions: SessionStore,
        pending: PendingStore,
        history_turns: int = 10,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._agent = chat_agent
        self._sessions = sessions
        self._pending = pending
        self._history_turns = history_turns
        # tz-aware clock so "завтра"/"на середу" resolve to a real schedule_date;
        # injected by the composition root (config.tz) and in tests.
        self._now = now or datetime.now

    def _today_line(self) -> str:
        d = self._now()
        return f"{TODAY_MARKER} {d.date().isoformat()} ({_WEEKDAYS[d.weekday()]})."

    def _render(self, chat_id: object, text: str) -> str:
        turns = self._sessions.history(chat_id)[-self._history_turns:]
        lines = []
        for t in turns:
            who = "User" if t.role == "user" else "Coach"
            lines.append(f"{who}: {t.text}")
        history = "\n".join(lines)
        today = self._today_line()
        if history:
            return f"{today}\n\nPrevious conversation:\n{history}\n\nUser: {text}"
        return f"{today}\n\nUser: {text}"

    async def run_chat(self, chat_id: object, text: str) -> ChatReply:
        before = {a.nonce for a in self._pending.list_pending()}

        prompt = self._render(chat_id, text)
        self._sessions.append(chat_id, "user", text)

        try:
            result = await self._agent.run_turn(prompt)
            reply = (result.text or "").strip() or "Готово."
            cost = result.cost_usd
        except LLMError as exc:
            reply = "Не вдалося обробити запит зараз. Спробуй ще раз."
            cost = None
            # surface nothing parked on hard failure
            self._sessions.append(chat_id, "assistant", reply)
            return ChatReply(reply, [], cost)

        new = [a for a in self._pending.list_pending() if a.nonce not in before]
        self._sessions.append(chat_id, "assistant", reply)
        return ChatReply(reply, new, cost)
