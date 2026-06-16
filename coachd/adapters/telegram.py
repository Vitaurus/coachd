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

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

LIMIT = 4000  # Telegram hard limit is 4096; leave headroom

# We send plain text (no parse_mode), so any markdown the model emits renders as
# literal characters. Strip the markers the coach actually produces; leave single
# * / _ alone (they show up in legit text like "5*5 sets").
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEADER = re.compile(r"^#{1,6}[ \t]+", re.M)


def strip_markdown(text: str) -> str:
    """Remove **bold**/__bold__, `code`, and leading # headings, keeping content.

    Idempotent and safe on already-plain text (catalog strings, emoji): it only
    touches the specific markdown markers above."""
    text = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_CODE.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    return text


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


def _urllib_get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


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
        chunks = chunk_message(strip_markdown(text), self._limit)
        for c in chunks:
            data = urllib.parse.urlencode({
                "chat_id": self._chat_id,
                "text": c,
                "disable_web_page_preview": "true",
            }).encode()
            self._post(self._url, data)
        return len(chunks)


# --------------------------------------------------------------------------- #
# Telegram Bot API access (shared by the bot daemon and the chat-id command)
# --------------------------------------------------------------------------- #
def make_api(token: str) -> Callable[[str, dict | None], object]:
    """Return an ``api(method, params)`` callable over the Telegram Bot API.

    GET for ``getUpdates`` (long-poll friendly), POST otherwise. Returns the
    decoded ``result`` field. HTTP errors (e.g. 409 conflict, 401 bad token)
    propagate as ``urllib.error.HTTPError`` for the caller to classify.
    """
    base = f"https://api.telegram.org/bot{token}/"

    def api(method: str, params: dict | None = None) -> object:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = base + method
        if method == "getUpdates":
            if params:
                url += "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.loads(r.read().decode("utf-8")).get("result")
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(url, data=data, timeout=40) as r:
            return json.loads(r.read().decode("utf-8")).get("result")

    return api


@dataclass(frozen=True)
class ChatRef:
    id: int
    label: str
    type: str


def _chat_label(chat: dict) -> str:
    """A human label for a chat; falls back to the id when no name field exists."""
    for key in ("first_name", "username", "title"):
        value = chat.get(key)
        if value:
            return str(value)
    return str(chat.get("id", "?"))


def parse_chat_ids(updates: list | None) -> list[ChatRef]:
    """Extract unique chat refs from getUpdates results, first-seen order.

    Walks ``message`` / ``edited_message`` only — a first-time user discovering
    their id sends a text message; ``callback_query`` only fires after onboarding.
    """
    out: list[ChatRef] = []
    seen: set = set()
    for update in updates or []:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        out.append(ChatRef(id=cid, label=_chat_label(chat), type=chat.get("type", "?")))
    return out


def discover_chat_ids(token: str, *, api: Callable[[str, dict | None], object] | None = None) -> list[ChatRef]:
    """deleteWebhook (avoid 409 if a webhook was set) + getUpdates + parse.

    ``api`` is injectable for tests. HTTP errors propagate to the caller, which
    maps 409 (a running bot is already consuming getUpdates) and 401 (bad token)
    to actionable messages.
    """
    api = api or make_api(token)
    api("deleteWebhook", {})
    updates = api("getUpdates", {}) or []
    return parse_chat_ids(updates)


# --------------------------------------------------------------------------- #
# File download (image input) — getFile + binary fetch
# --------------------------------------------------------------------------- #
# Bot API getFile serves files <=20MB; a Telegram photo is typically well under
# 1MB. Cap the bytes BEFORE the binary fetch (the file_size from getFile) so a
# spammed large image costs nothing downstream — base64 + a billed vision turn.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# media_type for the Anthropic image block. Telegram photos are JPEG, so jpeg is
# the right image fallback. (Pixel dimensions are NOT capped here — the claude CLI
# auto-downscales images >2000px, so the byte cap is the only guard we need.)
# Audio entries are for Telegram voice notes (.oga, OGG/Opus): download_file is
# reused for voice, but the voice branch DISCARDS the returned mime (it hands raw
# bytes to whisper), so these only keep the value honest, never load-bearing.
_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}


def _mime_for(file_path: str) -> str:
    dot = file_path.rfind(".")
    ext = file_path[dot:].lower() if dot >= 0 else ""
    return _MIME_BY_EXT.get(ext, "image/jpeg")


def download_file(
    token: str,
    file_id: str,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
    api: Callable[[str, dict | None], object] | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> tuple[bytes, str]:
    """Download a Telegram file by id → ``(bytes, media_type)``.

    getFile → refuse if ``file_size`` exceeds ``max_bytes`` (BEFORE the fetch) →
    download the bytes → return them with a media_type guessed from the extension.
    ``api``/``fetch`` are injectable for tests. HTTP errors (bad token/file_id)
    propagate; the caller surfaces a friendly download-failed message.
    """
    api = api or make_api(token)
    fetch = fetch or _urllib_get_bytes
    info = api("getFile", {"file_id": file_id}) or {}
    if not isinstance(info, dict):
        raise ValueError("getFile returned no file info")
    size = info.get("file_size")
    if isinstance(size, int) and size > max_bytes:
        raise ValueError(f"image too large: {size} bytes > {max_bytes} cap")
    file_path = info.get("file_path")
    if not file_path:
        raise ValueError("getFile returned no file_path")
    data = fetch(f"https://api.telegram.org/file/bot{token}/{file_path}")
    if len(data) > max_bytes:  # belt-and-suspenders when file_size was absent
        raise ValueError(f"image too large: {len(data)} bytes > {max_bytes} cap")
    return data, _mime_for(file_path)
