"""STT port: transcribe spoken audio bytes to text.

The core/bot depend on this Protocol, not on faster-whisper. The local
faster-whisper adapter lives in ../adapters/faster_whisper_stt.py; swapping STT
engines (e.g. to a hosted API) means a new adapter, not a core change.

``transcribe`` is intentionally SYNCHRONOUS: it is a blocking CPU op, so the
caller (the bot) runs it via ``asyncio.to_thread`` — exactly like the sync
``telegram.download_file``. Putting the threading at the event-loop owner keeps
the adapter honest (no fake ``async`` over a CPU-bound call).
"""

from __future__ import annotations

from typing import Protocol


class TranscriptionError(Exception):
    """Speech-to-text failed for one clip (decode error, model failure).

    The bot surfaces this as a friendly "couldn't transcribe, please type it"
    line — never a traceback — and the turn ends cleanly.
    """


class TranscriberPort(Protocol):
    def transcribe(self, audio: bytes, *, language: str | None = None) -> str: ...
    # ``audio`` = raw container bytes (a Telegram voice note is OGG/Opus).
    # ``language`` is the hint coachd passes (COACH_LANG); None lets the engine
    # autodetect. Returns the transcript — possibly "" when nothing intelligible
    # was heard (the bot turns "" into a "didn't catch that" nudge).
