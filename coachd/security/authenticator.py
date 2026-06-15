"""Owner gate — the single auth boundary for the Telegram bot.

A Telegram bot token is effectively public (anyone who finds the bot can message
it), so this allowlist is the ONLY thing standing between a stranger and the
owner's Garmin data and watch. Ported from the legacy chatbot's single-OWNER
filter, widened to an allowlist (a household can authorise 1..N chat ids).
"""

from __future__ import annotations

from typing import Iterable


class OwnerGate:
    def __init__(self, allowed_chat_ids: Iterable[int]) -> None:
        self._allowed = {int(c) for c in allowed_chat_ids}
        if not self._allowed:
            raise ValueError("OwnerGate needs at least one allowed chat id")

    def allows(self, chat_id: object) -> bool:
        """True only for an explicitly allowlisted chat id. Anything unparseable
        or absent is denied."""
        try:
            return int(chat_id) in self._allowed
        except (TypeError, ValueError):
            return False
