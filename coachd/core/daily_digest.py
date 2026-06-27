"""DailyDigest — the cross-agent daily memory.

The report agent and the chat agent live in separate stores and never see each
other, so the evening report can scold the user for a workout the chat coach
itself prescribed. This unit closes that gap: once a day (before the evening
report) it condenses the day's confirmed write-actions + chat into ONE line and
writes it to the journal as a ``(date, "interactions")`` row. Both reports then
read it through the EXISTING journal tail — no new read path, no engine change.

Confirmed actions are pulled DETERMINISTICALLY from the pending store (USED), not
from chat history, so a chatty day can never evict the morning's workout before
the evening report runs. Advice (unlike actions) needs the model to distil it, so
the summary is one cheap, tool-free LLM call.

Never raises: a digest failure must never block the report that consumes it.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from ..ports.llm import LLMPort
from .i18n import LANGUAGE_NAMES, Strings
from .journal import Journal
from .pending import PendingStore
from .prompts import build_digest_prompt
from .session_store import SessionStore

_log = logging.getLogger(__name__)

# The journal tail is one line per record; cap the summary so a chatty day can't
# bloat that line.
_MAX_SUMMARY_CHARS = 200


def _render_actions(actions) -> str:
    """Confirmed write-actions as ground-truth bullet lines for the summarizer."""
    if not actions:
        return "(none)"
    return "\n".join(
        f"- {a.tool} {json.dumps(a.input, ensure_ascii=False, sort_keys=True)}"
        for a in actions
    )


def _render_turns(turns) -> str:
    if not turns:
        return "(none)"
    return "\n".join(f"{t.role}: {t.text}" for t in turns)


class DailyDigest:
    def __init__(
        self,
        *,
        llm: LLMPort,
        pending: PendingStore,
        sessions: SessionStore,
        journal: Journal,
        strings: Strings,
        tz,
    ) -> None:
        self._llm = llm
        self._pending = pending
        self._sessions = sessions
        self._journal = journal
        self._strings = strings
        self._tz = tz

    async def run(self, on_date: date) -> str | None:
        """Summarize ``on_date``'s interactions into a journal 'interactions' row.

        Returns the one-line summary, or ``None`` when nothing material happened
        or the summarizer failed — in which case the report simply runs without
        the extra context (degrade, never block)."""
        actions = self._pending.used_on(on_date, self._tz)
        turns = self._sessions.turns_on(on_date, self._tz)
        if not actions and not turns:
            return None

        prompt = build_digest_prompt(
            _render_actions(actions),
            _render_turns(turns),
            language=LANGUAGE_NAMES[self._strings.lang],
        )
        try:
            result = await self._llm.run_turn(prompt)
        except Exception:
            _log.exception("daily digest summarization failed; skipping row")
            return None

        # collapse newlines/runs of whitespace → one clean line for the tail
        summary = " ".join((result.text or "").split())[:_MAX_SUMMARY_CHARS]
        if not summary:
            return None
        self._journal.record_interactions(on_date.isoformat(), summary)
        return summary
