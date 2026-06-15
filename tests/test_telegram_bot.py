"""Bot update dispatch: owner gate, chat replies, confirm/cancel callbacks."""

from __future__ import annotations

import asyncio

from coachd.adapters.telegram_bot import TelegramBot
from coachd.core.chat import ChatReply
from coachd.core.pending import PendingStore
from coachd.security.authenticator import OwnerGate

OWNER = 123


class _Api:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params=None):
        self.calls.append((method, params or {}))
        return []

    def methods(self):
        return [m for m, _ in self.calls]


class _Chat:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    async def run_chat(self, chat_id, text):
        self.calls.append((chat_id, text))
        return self._reply


class _Exec:
    def __init__(self, raise_exc=None):
        self.calls = []
        self._raise = raise_exc

    async def execute(self, action):
        self.calls.append(action)
        if self._raise:
            raise self._raise
        return "done"


def _bot(tmp_path, *, reply=None, executor=None, pending=None):
    api = _Api()
    bot = TelegramBot(
        token="t",
        owner_gate=OwnerGate([OWNER]),
        chat_engine=_Chat(reply or ChatReply("відповідь", [])),
        pending=pending or PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1"),
        executor=executor or _Exec(),
        offset_path=tmp_path / "offset",
        api=api,
    )
    return bot, api


def _msg(chat_id, text):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_owner_message_gets_chat_reply(tmp_path):
    bot, api = _bot(tmp_path, reply=ChatReply("ось аналіз", []))
    asyncio.run(bot.handle_update(_msg(OWNER, "як я?")))
    sends = [p for m, p in api.calls if m == "sendMessage"]
    assert any(p.get("text") == "ось аналіз" for p in sends)
    assert bot._chat.calls == [(OWNER, "як я?")]


def test_ack_sent_before_the_answer(tmp_path):
    from coachd.adapters.telegram_bot import ACK_TEXT

    bot, api = _bot(tmp_path, reply=ChatReply("ось аналіз", []))
    asyncio.run(bot.handle_update(_msg(OWNER, "як я?")))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    # ack first ("working on it"), then the real answer
    assert texts == [ACK_TEXT, "ось аналіз"]


def test_non_owner_message_ignored(tmp_path):
    bot, api = _bot(tmp_path)
    asyncio.run(bot.handle_update(_msg(999, "впусти")))
    assert api.calls == []                 # nothing sent
    assert bot._chat.calls == []           # chat never invoked


def test_parked_write_sends_confirm_buttons(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    action = pending.put("mcp__garmin__upload_workout", {"name": "x"})
    bot, api = _bot(tmp_path, reply=ChatReply("готую тренування", [action]), pending=pending)
    asyncio.run(bot.handle_update(_msg(OWNER, "додай тренування")))
    confirms = [p for m, p in api.calls if m == "sendMessage" and "reply_markup" in p]
    assert len(confirms) == 1
    assert "N1" in confirms[0]["reply_markup"]  # nonce in the inline keyboard


def test_confirm_callback_executes(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    action = pending.put("mcp__garmin__upload_workout", {"name": "x"})
    ex = _Exec()
    bot, api = _bot(tmp_path, executor=ex, pending=pending)
    cb = {"callback_query": {"id": "c1", "data": "confirm:N1", "message": {"chat": {"id": OWNER}}}}
    asyncio.run(bot.handle_update(cb))
    # executor ran on the confirmed action (confirm() returns the used-marked copy)
    assert len(ex.calls) == 1
    assert ex.calls[0].nonce == "N1"
    assert ex.calls[0].tool == action.tool and ex.calls[0].input == action.input
    assert "answerCallbackQuery" in api.methods()
    assert pending.get("N1").status == "used"             # single-use


def test_confirm_stale_nonce_no_execute(tmp_path):
    ex = _Exec()
    bot, api = _bot(tmp_path, executor=ex)
    cb = {"callback_query": {"id": "c1", "data": "confirm:ghost", "message": {"chat": {"id": OWNER}}}}
    asyncio.run(bot.handle_update(cb))
    assert ex.calls == []                                  # nothing executed
    assert any("вже оброблено" in p.get("text", "") for m, p in api.calls if m == "sendMessage")


def test_cancel_callback(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    pending.put("mcp__garmin__upload_workout", {})
    ex = _Exec()
    bot, api = _bot(tmp_path, executor=ex, pending=pending)
    cb = {"callback_query": {"id": "c1", "data": "cancel:N1", "message": {"chat": {"id": OWNER}}}}
    asyncio.run(bot.handle_update(cb))
    assert ex.calls == []
    assert pending.get("N1").status == "cancelled"


def test_non_owner_callback_no_execute_but_answered(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    pending.put("mcp__garmin__upload_workout", {})
    ex = _Exec()
    bot, api = _bot(tmp_path, executor=ex, pending=pending)
    cb = {"callback_query": {"id": "c1", "data": "confirm:N1", "message": {"chat": {"id": 999}}}}
    asyncio.run(bot.handle_update(cb))
    assert ex.calls == []                                  # stranger cannot execute
    assert "answerCallbackQuery" in api.methods()          # but the spinner is dismissed
    assert pending.get("N1").status == "pending"           # untouched
