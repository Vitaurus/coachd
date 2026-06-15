"""Telegram delivery — chunking (scar tissue) + sendMessage.

Ported from send_telegram.py. The chunking is the scar tissue: Telegram's hard
limit is 4096, so messages are split at <=4000 with headroom, preferring the last
newline before the limit (a hard cut only when there is no newline), and leading
newlines are stripped from each continuation. Plain text, no parse_mode, for
reliability.

``chunk_message`` is pure (tested at the 3999/4000/4001 boundaries). The HTTP
post is injected so the messenger is tested without the network.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

LIMIT = 4000  # Telegram hard limit is 4096; leave headroom


def chunk_message(text: str, limit: int = LIMIT) -> list[str]:
    """Split text into <=limit-char chunks, preferring the last newline."""
    chunks: list[str] = []
    msg = text
    while msg:
        if len(msg) <= limit:
            chunks.append(msg)
            break
        cut = msg.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(msg[:cut])
        msg = msg[cut:].lstrip("\n")
    return chunks


def _urllib_post(url: str, data: bytes) -> None:
    with urllib.request.urlopen(url, data=data, timeout=30) as r:
        r.read()


class TelegramMessenger:
    """Sends to one chat. ``post(url, data)`` is injected (defaults to urllib)."""

    def __init__(
        self,
        bot_token: str,
        chat_id: int | str,
        *,
        limit: int = LIMIT,
        post: Callable[[str, bytes], None] = _urllib_post,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = str(chat_id)
        self._limit = limit
        self._post = post

    def send(self, text: str) -> int:
        """Send ``text`` as one or more chunks. Returns the number of chunks sent
        (0 for empty text). Per-chunk HTTP errors are surfaced as exceptions by
        the injected ``post``; the caller decides how to handle delivery failure."""
        if not text.strip():
            return 0
        chunks = chunk_message(text, self._limit)
        for c in chunks:
            data = urllib.parse.urlencode({
                "chat_id": self._chat_id,
                "text": c,
                "disable_web_page_preview": "true",
            }).encode()
            self._post(self._url, data)
        return len(chunks)
