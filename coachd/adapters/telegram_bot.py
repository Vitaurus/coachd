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
from typing import Callable

from ..core.chat import ChatEngine
from ..core.pending import PendingStore
from ..security.authenticator import OwnerGate
from ..security.write_guard import default_confirm_message
from .telegram import chunk_message, make_api

# Sent the instant an owner message arrives, before the (multi-second) agent
# turn — so the user knows the question was received and is being worked on.
# Ported verbatim from the reference chatbot's "⏳ дивлюсь дані…" ack.
ACK_TEXT = "⏳ дивлюсь дані…"


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
        api: Callable[[str, dict], object] | None = None,
    ) -> None:
        self._owner_gate = owner_gate
        self._chat = chat_engine
        self._pending = pending
        self._executor = executor
        self._offset_path = Path(offset_path)
        self._api = api or make_api(token)

    # --- sending --------------------------------------------------------- #
    def _send(self, chat_id: object, text: str) -> None:
        for c in chunk_message(text):
            self._api("sendMessage", {
                "chat_id": chat_id, "text": c, "disable_web_page_preview": "true",
            })

    def _send_ack(self, chat_id: object) -> object:
        """Send the "⏳" ack and return its ``message_id`` so it can be removed
        once the real reply lands (None if the API gave no id — then we skip the
        delete). ACK_TEXT is one short line, so no chunking is needed."""
        result = self._api("sendMessage", {"chat_id": chat_id, "text": ACK_TEXT})
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
            {"text": "✓ Підтвердити", "callback_data": f"confirm:{action.nonce}"},
            {"text": "✗ Скасувати", "callback_data": f"cancel:{action.nonce}"},
        ]]}
        self._api("sendMessage", {
            "chat_id": chat_id,
            "text": default_confirm_message(action),
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
        text = (msg.get("text") or "").strip()
        if not text:
            return  # v1: text only (voice is v1.1)

        ack_id = self._send_ack(chat_id)  # "received, working on it" — the turn is slow
        reply = await self._chat.run_chat(chat_id, text)
        self._send(chat_id, reply.text)
        self._delete(chat_id, ack_id)  # answer landed → the "⏳" ack is now stale
        for action in reply.pending:
            self._send_confirm(chat_id, action)

    async def _handle_callback(self, chat_id: object, data: str) -> None:
        kind, _, nonce = data.partition(":")
        if kind == "confirm":
            action = self._pending.confirm(nonce)
            if action is None:
                self._send(chat_id, "⏱ Дію вже оброблено або скасовано.")
                return
            try:
                # the executor returns a fully-formed status line (✓ single tool,
                # ✓ create+schedule, or ⚠️ partial-failure note) — send verbatim
                msg = await self._executor.execute(action)
                self._send(chat_id, msg)
            except Exception as exc:  # noqa: BLE001 — surface any MCP failure to the user
                self._send(chat_id, f"⚠️ Не вдалося виконати дію: {exc}")
        elif kind == "cancel":
            ok = self._pending.cancel(nonce)
            self._send(chat_id, "✗ Скасовано." if ok else "Дію вже оброблено.")

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
