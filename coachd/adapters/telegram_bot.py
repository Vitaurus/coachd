"""Telegram chat bot: long-poll, owner-gate, chat turns, confirm/cancel buttons.

Update handling (handle_update / _handle_callback) is separated from the polling
I/O and unit-tested with an injected ``api``. The ``run`` loop ports the legacy
scar tissue: deleteWebhook (else getUpdates 409), swallow the backlog on first
start (don't answer old messages), persist the offset (survive restart). Blocking
API calls run in a thread so they never stall the shared event loop (the report
scheduler runs on the same loop).

Confirmed writes execute deterministically via the injected executor (a direct
MCP call) — no LLM in the confirm path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.chat import ChatEngine
from ..core.i18n import Strings
from ..core.pending import PendingStore
from ..security.authenticator import OwnerGate
from ..security.write_guard import default_confirm_message
from .telegram import chunk_message, download_file, make_api, strip_markdown

if TYPE_CHECKING:  # type-only — the bot never imports the heavy whisper adapter
    from ..ports.stt import TranscriberPort


class TelegramBot:
    def __init__(
        self,
        *,
        token: str,
        owner_gate: OwnerGate,
        chat_engine: ChatEngine,
        pending: PendingStore,
        executor,
        offset_path: str | Path,
        strings: Strings,
        api: Callable[[str, dict], object] | None = None,
        download: Callable[[str], tuple[bytes, str]] | None = None,
        transcriber: "TranscriberPort | None" = None,
        max_voice_seconds: int = 300,
        voice_pending: bool = False,
    ) -> None:
        self._owner_gate = owner_gate
        self._chat = chat_engine
        self._pending = pending
        self._executor = executor
        self._offset_path = Path(offset_path)
        # the language-bound catalog: ack, confirm caption, callback replies
        self._strings = strings
        self._api = api or make_api(token)
        # download a photo/voice file by file_id → (bytes, media_type). Reuses the
        # bot's api for getFile (one HTTP path); the binary fetch uses urllib.
        # Injectable so the photo/voice branches are unit-tested fully offline.
        self._download = download or (lambda file_id: download_file(token, file_id, api=self._api))
        # voice/STT: None → voice off (model not loaded yet, load failed, or
        # disabled). The composition root's background loader calls set_transcriber
        # once the model is ready, so text serves immediately while voice warms up.
        self._transcriber = transcriber
        self._max_voice_seconds = max_voice_seconds
        # voice_pending distinguishes "still loading" from "off": True when voice
        # is enabled and the model is on its way (the background loader will call
        # set_transcriber or mark_voice_unavailable). It lets _handle_voice send a
        # transient "warming up, retry" line instead of the permanent off line —
        # the gap a user hits with a slow first-boot download (e.g. the medium model).
        self._voice_pending = voice_pending

    def set_transcriber(self, transcriber: "TranscriberPort") -> None:
        """Enable voice once the model has finished loading (called by the
        background loader). A plain attribute write — safe because the same single
        event-loop thread reads it in handle_update; no lock needed."""
        self._transcriber = transcriber
        self._voice_pending = False  # loaded → no longer pending

    def mark_voice_unavailable(self) -> None:
        """Give up on voice (called by the background loader when load fails): clear
        the pending flag so _handle_voice sends the permanent off line, not the
        transient "still loading" one. The transcriber stays None (voice off)."""
        self._voice_pending = False

    # --- sending --------------------------------------------------------- #
    def _send(self, chat_id: object, text: str) -> None:
        # strip markdown the model may emit — Telegram is plain text, so **bold**
        # would show as literal asterisks (chat replies route through here)
        for c in chunk_message(strip_markdown(text)):
            self._api("sendMessage", {
                "chat_id": chat_id, "text": c, "disable_web_page_preview": "true",
            })

    def _send_ack(self, chat_id: object, key: str = "ack") -> object:
        """Send the transient ack (⏳ for text, 🖼 for a photo) and return its
        ``message_id`` so it can be removed once the real reply lands (None if the
        API gave no id — then we skip the delete). One short line, no chunking."""
        result = self._api("sendMessage", {"chat_id": chat_id, "text": self._strings.get(key)})
        return result.get("message_id") if isinstance(result, dict) else None

    def _delete(self, chat_id: object, message_id: object) -> None:
        """Best-effort delete of the transient ack. Telegram lets a bot delete
        only its own recent messages, so a failure (too old / already gone) is
        cosmetic — never let it break the turn."""
        if message_id is None:
            return
        try:
            self._api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:  # noqa: BLE001 — deletion is cosmetic, swallow any failure
            pass

    def _send_confirm(self, chat_id: object, action) -> None:
        keyboard = {"inline_keyboard": [[
            {"text": self._strings.get("btn_confirm"), "callback_data": f"confirm:{action.nonce}"},
            {"text": self._strings.get("btn_cancel"), "callback_data": f"cancel:{action.nonce}"},
        ]]}
        self._api("sendMessage", {
            "chat_id": chat_id,
            "text": default_confirm_message(action, self._strings),
            "reply_markup": json.dumps(keyboard),
        })

    # --- dispatch (unit-tested) ------------------------------------------ #
    async def handle_update(self, update: dict) -> None:
        cb = update.get("callback_query")
        if cb:
            chat_id = cb.get("message", {}).get("chat", {}).get("id")
            if self._owner_gate.allows(chat_id):
                await self._handle_callback(chat_id, cb.get("data", ""))
            self._api("answerCallbackQuery", {"callback_query_id": cb.get("id")})
            return

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg.get("chat", {}).get("id")
        if not self._owner_gate.allows(chat_id):
            return  # SECURITY: only the owner is answered

        # A photo is handled BEFORE the text guard below: an uncaptioned photo has
        # no `text`, so `if not text: return` would silently eat the most common
        # input (snap a meal, no caption). The caption (if any) rides as the message.
        photo = msg.get("photo")
        if photo:
            await self._handle_photo(chat_id, photo, (msg.get("caption") or "").strip())
            return

        # A voice note also has no `text`, so it MUST be handled before the guard
        # below — same reason as photo. The transcript becomes the turn's text.
        voice = msg.get("voice")
        if voice:
            await self._handle_voice(chat_id, voice)
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return  # text + photo + voice only (no documents/video)

        ack_id = self._send_ack(chat_id)  # "received, working on it" — the turn is slow
        reply = await self._chat.run_chat(chat_id, text)
        await self._deliver(chat_id, reply, ack_id)

    async def _handle_photo(self, chat_id: object, photo: list, caption: str) -> None:
        """Download the largest photo size and run a guarded image chat turn.

        Largest (``photo[-1]``) reads small text best (calorie labels, screenshot
        figures). The blocking download runs in a thread so it never stalls the
        shared poll loop. A download failure is surfaced as one friendly line —
        never a traceback — and the turn ends cleanly."""
        ack_id = self._send_ack(chat_id, "photo_ack")
        try:
            file_id = photo[-1]["file_id"]
            image = await asyncio.to_thread(self._download, file_id)
        except Exception as exc:  # noqa: BLE001 — download/oversize failures are user-visible, not crashes
            self._delete(chat_id, ack_id)
            self._send(chat_id, self._strings.get("photo_download_failed"))
            print(f"coachd bot: photo download failed: {exc}", flush=True)
            return
        reply = await self._chat.run_chat(chat_id, caption, image=image)
        await self._deliver(chat_id, reply, ack_id)

    async def _handle_voice(self, chat_id: object, voice: dict) -> None:
        """Transcribe a Telegram voice note and run it as a normal chat turn.

        The transcript is plain TEXT, so it rides the SAME guarded pipeline as a
        typed message (``run_chat``) — a voice-issued write parks for confirmation
        exactly like a typed one. Friendly lines cover every failure (unavailable /
        too-long / download / empty / STT) — never a traceback.

        Blocking work (download, transcribe) runs in a thread so the shared poll
        loop is never stalled. The loop processes updates SEQUENTIALLY
        (``for u in ups: await handle_update``), so two transcribes cannot overlap
        — no single-flight lock is needed unless a future change dispatches updates
        concurrently (then add one)."""
        if self._transcriber is None:
            # voice_pending tells "still warming up" (transient — retry) apart from
            # "load failed / disabled" (permanent — type instead). Without it a slow
            # first-boot model download looks identical to voice being off.
            key = "voice_loading" if self._voice_pending else "voice_unavailable"
            self._send(chat_id, self._strings.get(key))
            return
        # Reject an over-long (or accidental) note BEFORE downloading: STT on CPU
        # runs near real-time and the poll loop AWAITS transcribe, so a long clip
        # would make the bot unresponsive for minutes. A missing/odd duration falls
        # through — download_file's byte cap is the backstop.
        duration = voice.get("duration")
        if isinstance(duration, (int, float)) and duration > self._max_voice_seconds:
            self._send(chat_id, self._strings.get("voice_too_long"))
            return
        ack_id = self._send_ack(chat_id, "voice_ack")
        try:
            audio, _mime = await asyncio.to_thread(self._download, voice["file_id"])
        except Exception as exc:  # noqa: BLE001 — download/oversize failures are user-visible
            self._delete(chat_id, ack_id)
            self._send(chat_id, self._strings.get("voice_failed"))
            print(f"coachd bot: voice download failed: {exc}", flush=True)
            return
        try:
            transcript = await asyncio.to_thread(
                self._transcriber.transcribe, audio, language=self._strings.lang
            )
        except Exception as exc:  # noqa: BLE001 — STT failure → friendly line, not a crash
            self._delete(chat_id, ack_id)
            self._send(chat_id, self._strings.get("voice_failed"))
            print(f"coachd bot: transcription failed: {exc}", flush=True)
            return
        if not transcript:
            self._delete(chat_id, ack_id)
            self._send(chat_id, self._strings.get("voice_empty"))
            return
        # echo what was heard so the user can verify the STT (kept, not transient)
        self._send(chat_id, self._strings.get("voice_heard", text=transcript))
        reply = await self._chat.run_chat(chat_id, transcript)
        await self._deliver(chat_id, reply, ack_id)

    async def _deliver(self, chat_id: object, reply, ack_id: object) -> None:
        """Send the reply, drop the stale ack, and surface any parked write for
        confirmation. Shared by the text and photo paths."""
        self._send(chat_id, reply.text)
        self._delete(chat_id, ack_id)  # answer landed → the ack is now stale
        for action in reply.pending:
            self._send_confirm(chat_id, action)

    async def _handle_callback(self, chat_id: object, data: str) -> None:
        kind, _, nonce = data.partition(":")
        if kind == "confirm":
            action = self._pending.confirm(nonce)
            if action is None:
                self._send(chat_id, self._strings.get("cb_already_handled"))
                return
            try:
                # the executor returns a fully-formed status line (✓ single tool,
                # ✓ create+schedule, or ⚠️ partial-failure note) — send verbatim
                msg = await self._executor.execute(action)
            except Exception as exc:  # noqa: BLE001 — surface any MCP failure to the user
                msg = self._strings.get("cb_exec_failed", exc=exc)
            self._send(chat_id, msg)
            # record the confirmed OUTCOME into chat memory (carries the absolute
            # date) so the coach recalls what it DID, not just what it proposed
            self._chat.note(chat_id, msg)
        elif kind == "cancel":
            ok = self._pending.cancel(nonce)
            msg = self._strings.get("cb_cancelled" if ok else "cb_cancel_already")
            self._send(chat_id, msg)
            if ok:
                self._chat.note(chat_id, msg)  # remember it was cancelled (not done)

    # --- offset persistence ---------------------------------------------- #
    def _load_offset(self) -> int | None:
        try:
            return int(self._offset_path.read_text().strip())
        except Exception:
            return None

    def _save_offset(self, offset: int | None) -> None:
        if offset is not None:
            self._offset_path.write_text(str(offset))

    # --- long-poll loop (I/O glue) --------------------------------------- #
    async def run(self) -> None:
        offset = self._load_offset()
        # Resilient startup: a transient API hiccup (or a misconfigured token)
        # must not crash the process — log and fall through to the retrying loop.
        try:
            await asyncio.to_thread(self._api, "deleteWebhook", {})  # else getUpdates 409
            if offset is None:
                # first start: swallow the backlog so we don't answer old messages
                ups = await asyncio.to_thread(self._api, "getUpdates", {"timeout": 0}) or []
                offset = (ups[-1]["update_id"] + 1) if ups else None
                self._save_offset(offset)
        except Exception as exc:  # noqa: BLE001
            print(f"coachd bot: startup poll failed ({exc}); entering retry loop", flush=True)

        print(f"coachd bot: started, offset={offset}", flush=True)
        while True:
            try:
                ups = await asyncio.to_thread(
                    self._api, "getUpdates",
                    {"offset": offset, "timeout": 30,
                     "allowed_updates": json.dumps(["message", "callback_query"])},
                ) or []
                for u in ups:
                    offset = u["update_id"] + 1
                    await self.handle_update(u)
                    self._save_offset(offset)
            except Exception as exc:  # noqa: BLE001 — never let one bad poll kill the loop
                print(f"coachd bot: poll error: {exc}", flush=True)
                await asyncio.sleep(3)
