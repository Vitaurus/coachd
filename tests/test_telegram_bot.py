"""Bot update dispatch: owner gate, chat replies, confirm/cancel callbacks."""

from __future__ import annotations

import asyncio

from coachd.adapters.telegram_bot import TelegramBot
from coachd.core.chat import ChatReply
from coachd.core.i18n import Strings
from coachd.core.pending import PendingStore
from coachd.security.authenticator import OwnerGate

OWNER = 123
STRINGS = Strings("uk")  # the bot is built with a language-bound catalog


class _Api:
    def __init__(self):
        self.calls = []
        self._next_id = 0

    def __call__(self, method, params=None):
        self.calls.append((method, params or {}))
        # mirror make_api: sendMessage's result is a Message dict (carries the
        # message_id the bot deletes the ack by); others return a bare list
        if method == "sendMessage":
            self._next_id += 1
            return {"message_id": self._next_id}
        return []

    def methods(self):
        return [m for m, _ in self.calls]


class _Chat:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []
        self.images = []  # (chat_id, image) for image turns
        self.notes = []   # out-of-band outcomes recorded into chat memory

    async def run_chat(self, chat_id, text, *, image=None):
        self.calls.append((chat_id, text))
        self.images.append((chat_id, image))
        return self._reply

    def note(self, chat_id, text):
        self.notes.append((chat_id, text))


class _Exec:
    def __init__(self, raise_exc=None):
        self.calls = []
        self._raise = raise_exc

    async def execute(self, action):
        self.calls.append(action)
        if self._raise:
            raise self._raise
        return "✓ Створено і заплановано на 2026-06-16."


class _Download:
    """Fake photo download: records the file_id, returns canned (bytes, mime),
    or raises to simulate a download/oversize failure."""

    def __init__(self, *, raise_exc=None, result=(b"IMG", "image/jpeg")):
        self.calls = []
        self._raise = raise_exc
        self._result = result

    def __call__(self, file_id):
        self.calls.append(file_id)
        if self._raise:
            raise self._raise
        return self._result


class _Transcriber:
    """Fake STT: records (audio, language), returns canned text or raises."""

    def __init__(self, *, text="як я сьогодні", raise_exc=None):
        self.calls = []
        self._text = text
        self._raise = raise_exc

    def transcribe(self, audio, *, language=None):
        self.calls.append((audio, language))
        if self._raise:
            raise self._raise
        return self._text


def _bot(tmp_path, *, reply=None, executor=None, pending=None, download=None,
         transcriber=None, max_voice_seconds=300):
    api = _Api()
    bot = TelegramBot(
        token="t",
        owner_gate=OwnerGate([OWNER]),
        chat_engine=_Chat(reply or ChatReply("відповідь", [])),
        pending=pending or PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1"),
        executor=executor or _Exec(),
        offset_path=tmp_path / "offset",
        strings=STRINGS,
        api=api,
        download=download or _Download(),
        transcriber=transcriber,
        max_voice_seconds=max_voice_seconds,
    )
    return bot, api


def _msg(chat_id, text):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def _photo_msg(chat_id, *, caption=None, sizes=("thumb", "big")):
    msg = {"chat": {"id": chat_id}, "photo": [{"file_id": s} for s in sizes]}
    if caption is not None:
        msg["caption"] = caption
    return {"message": msg}


def _voice_msg(chat_id, *, duration=5, file_id="voiceid", with_duration=True):
    voice = {"file_id": file_id}
    if with_duration:
        voice["duration"] = duration
    return {"message": {"chat": {"id": chat_id}, "voice": voice}}


# --- photo input ------------------------------------------------------------ #
def test_uncaptioned_photo_is_processed_not_dropped(tmp_path):
    # the critical ordering: the photo branch sits BEFORE `if not text: return`,
    # so a captionless photo (no `text`) is still handled
    dl = _Download()
    bot, api = _bot(tmp_path, reply=ChatReply("млинці ~450 ккал", []), download=dl)
    asyncio.run(bot.handle_update(_photo_msg(OWNER)))
    assert bot._chat.calls == [(OWNER, "")]                 # run_chat invoked, empty caption
    assert bot._chat.images == [(OWNER, (b"IMG", "image/jpeg"))]  # image forwarded
    assert any(p.get("text") == "млинці ~450 ккал" for m, p in api.calls if m == "sendMessage")


def test_photo_branch_acks_downloads_largest_and_replies(tmp_path):
    dl = _Download()
    bot, api = _bot(tmp_path, reply=ChatReply("бачу твій сон 7г", []), download=dl)
    asyncio.run(bot.handle_update(_photo_msg(OWNER, caption="що скажеш?", sizes=("s", "m", "big"))))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    # the photo-specific ack came first, then the reply
    assert texts[0] == STRINGS.get("photo_ack")
    assert "бачу твій сон 7г" in texts
    assert dl.calls == ["big"]                              # largest size (photo[-1])
    assert bot._chat.calls == [(OWNER, "що скажеш?")]       # caption rode as the message
    # the transient ack was deleted after the answer landed
    assert any(m == "deleteMessage" for m, _ in api.calls)


def test_photo_that_parks_a_write_sends_confirm_buttons(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    action = pending.put("mcp__garmin__upload_workout", {"name": "x"})
    bot, api = _bot(
        tmp_path, reply=ChatReply("створюю з фото", [action]), pending=pending, download=_Download()
    )
    asyncio.run(bot.handle_update(_photo_msg(OWNER, caption="створи це тренування")))
    confirms = [p for m, p in api.calls if m == "sendMessage" and "reply_markup" in p]
    assert len(confirms) == 1                               # plan-photo → write parked → buttons
    assert "N1" in confirms[0]["reply_markup"]


def test_photo_download_failure_is_graceful(tmp_path):
    dl = _Download(raise_exc=ValueError("image too large: 99 bytes > 10 cap"))
    bot, api = _bot(tmp_path, download=dl)
    asyncio.run(bot.handle_update(_photo_msg(OWNER, caption="x")))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert STRINGS.get("photo_download_failed") in texts    # friendly line, no traceback
    assert bot._chat.calls == []                            # never reached the model


def test_non_owner_photo_ignored(tmp_path):
    dl = _Download()
    bot, api = _bot(tmp_path, download=dl)
    asyncio.run(bot.handle_update(_photo_msg(999, caption="впусти")))
    assert api.calls == []                                  # nothing sent
    assert dl.calls == []                                   # never downloaded
    assert bot._chat.calls == []


# --- voice input (STT) ------------------------------------------------------ #
def _voice_download(result=(b"OGG", "audio/ogg"), **kw):
    return _Download(result=result, **kw)


def test_voice_transcribes_echoes_and_replies(tmp_path):
    tr = _Transcriber(text="як я сьогодні")
    dl = _voice_download()
    bot, api = _bot(tmp_path, reply=ChatReply("сон 7г, готовність висока", []),
                    download=dl, transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert texts[0] == STRINGS.get("voice_ack")                      # transient ack first
    assert STRINGS.get("voice_heard", text="як я сьогодні") in texts # echo for verification
    assert "сон 7г, готовність висока" in texts                      # the coach reply
    assert tr.calls == [(b"OGG", "uk")]                              # audio + COACH_LANG passthrough
    assert bot._chat.calls == [(OWNER, "як я сьогодні")]            # transcript rode as the turn
    assert any(m == "deleteMessage" for m, _ in api.calls)           # ack deleted after the answer


def test_voice_unavailable_when_no_transcriber(tmp_path):
    dl = _voice_download()
    bot, api = _bot(tmp_path, download=dl, transcriber=None)          # model not ready / disabled
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert texts == [STRINGS.get("voice_unavailable")]               # one neutral line
    assert dl.calls == []                                            # never downloaded
    assert bot._chat.calls == []                                     # never reached the model


def test_voice_too_long_rejected_before_download(tmp_path):
    tr = _Transcriber()
    dl = _voice_download()
    bot, api = _bot(tmp_path, download=dl, transcriber=tr, max_voice_seconds=300)
    asyncio.run(bot.handle_update(_voice_msg(OWNER, duration=999)))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert texts == [STRINGS.get("voice_too_long")]
    assert dl.calls == []                                            # rejected pre-download
    assert tr.calls == []


def test_voice_missing_duration_falls_through(tmp_path):
    # a missing duration must NOT crash (None > cap) — fall through to normal flow
    tr = _Transcriber(text="коротко")
    dl = _voice_download()
    bot, api = _bot(tmp_path, download=dl, transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(OWNER, with_duration=False)))
    assert dl.calls == ["voiceid"]                                   # proceeded
    assert tr.calls == [(b"OGG", "uk")]
    assert bot._chat.calls == [(OWNER, "коротко")]


def test_voice_empty_transcript_nudges(tmp_path):
    tr = _Transcriber(text="")  # adapter strips → "" means whisper heard nothing
    bot, api = _bot(tmp_path, download=_voice_download(), transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert STRINGS.get("voice_empty") in texts
    assert bot._chat.calls == []                                     # nothing to run
    assert any(m == "deleteMessage" for m, _ in api.calls)           # ack cleaned up


def test_voice_transcription_error_is_graceful(tmp_path):
    tr = _Transcriber(raise_exc=RuntimeError("ct2 boom"))
    bot, api = _bot(tmp_path, download=_voice_download(), transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert STRINGS.get("voice_failed") in texts                      # friendly line, no traceback
    assert bot._chat.calls == []


def test_voice_download_failure_is_graceful(tmp_path):
    tr = _Transcriber()
    dl = _voice_download(raise_exc=ValueError("too large"))
    bot, api = _bot(tmp_path, download=dl, transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert STRINGS.get("voice_failed") in texts
    assert tr.calls == []                                            # never transcribed
    assert bot._chat.calls == []


def test_non_owner_voice_ignored(tmp_path):
    tr = _Transcriber()
    dl = _voice_download()
    bot, api = _bot(tmp_path, download=dl, transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(999)))
    assert api.calls == []                                           # nothing sent
    assert dl.calls == []
    assert tr.calls == []


def test_voice_that_parks_a_write_sends_confirm_buttons(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    action = pending.put("mcp__garmin__upload_workout", {"name": "x"})
    tr = _Transcriber(text="створи інтервали")
    bot, api = _bot(tmp_path, reply=ChatReply("створюю", [action]), pending=pending,
                    download=_voice_download(), transcriber=tr)
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    confirms = [p for m, p in api.calls if m == "sendMessage" and "reply_markup" in p]
    assert len(confirms) == 1                                        # voice-issued write still parks
    assert "N1" in confirms[0]["reply_markup"]


def test_set_transcriber_enables_voice(tmp_path):
    tr = _Transcriber(text="привіт")
    bot, api = _bot(tmp_path, download=_voice_download(), transcriber=None)
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))               # warming up → unavailable
    assert bot._chat.calls == []
    bot.set_transcriber(tr)                                          # background loader finished
    asyncio.run(bot.handle_update(_voice_msg(OWNER)))
    assert bot._chat.calls == [(OWNER, "привіт")]                   # voice now works


def test_owner_message_gets_chat_reply(tmp_path):
    bot, api = _bot(tmp_path, reply=ChatReply("ось аналіз", []))
    asyncio.run(bot.handle_update(_msg(OWNER, "як я?")))
    sends = [p for m, p in api.calls if m == "sendMessage"]
    assert any(p.get("text") == "ось аналіз" for p in sends)
    assert bot._chat.calls == [(OWNER, "як я?")]


def test_ack_sent_before_the_answer(tmp_path):
    bot, api = _bot(tmp_path, reply=ChatReply("ось аналіз", []))
    asyncio.run(bot.handle_update(_msg(OWNER, "як я?")))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    # ack first ("working on it"), then the real answer
    assert texts == [STRINGS.get("ack"), "ось аналіз"]


def test_chat_reply_markdown_is_stripped(tmp_path):
    # the model sometimes emits **bold**; Telegram is plain text, so the bot must
    # strip it before sending (else the user sees literal asterisks)
    bot, api = _bot(tmp_path, reply=ChatReply("**Readiness 87 — HIGH.**", []))
    asyncio.run(bot.handle_update(_msg(OWNER, "score?")))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert "Readiness 87 — HIGH." in texts
    assert not any("**" in t for t in texts)


def test_ack_deleted_after_the_answer(tmp_path):
    bot, api = _bot(tmp_path, reply=ChatReply("ось аналіз", []))
    asyncio.run(bot.handle_update(_msg(OWNER, "як я?")))
    # the ack is sendMessage #1 → message_id 1; it's removed once the reply lands
    deletes = [p for m, p in api.calls if m == "deleteMessage"]
    assert len(deletes) == 1
    assert deletes[0] == {"chat_id": OWNER, "message_id": 1}
    # ordering: ack + reply sent BEFORE the delete (ack no longer dangles)
    methods = api.methods()
    assert methods.index("deleteMessage") > methods.index("sendMessage")


def test_ack_delete_failure_does_not_break_turn(tmp_path):
    # Telegram refuses to delete an old/missing message → the reply must still
    # have gone out; the cosmetic delete failure is swallowed.
    class _RaisingApi(_Api):
        def __call__(self, method, params=None):
            if method == "deleteMessage":
                self.calls.append((method, params or {}))
                raise RuntimeError("message to delete not found")
            return super().__call__(method, params)

    api = _RaisingApi()
    bot = TelegramBot(
        token="t",
        owner_gate=OwnerGate([OWNER]),
        chat_engine=_Chat(ChatReply("ось аналіз", [])),
        pending=PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1"),
        executor=_Exec(),
        offset_path=tmp_path / "offset",
        strings=STRINGS,
        api=api,
    )
    asyncio.run(bot.handle_update(_msg(OWNER, "як я?")))
    texts = [p.get("text") for m, p in api.calls if m == "sendMessage"]
    assert "ось аналіз" in texts          # reply still delivered
    assert "deleteMessage" in api.methods()  # delete was attempted


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
    # the executor's status line reaches the user verbatim (incl. schedule outcome)
    assert any(p.get("text") == "✓ Створено і заплановано на 2026-06-16."
               for m, p in api.calls if m == "sendMessage")
    # AND is recorded into chat memory (absolute date) so future recall is accurate
    assert bot._chat.notes == [(OWNER, "✓ Створено і заплановано на 2026-06-16.")]


def test_confirm_stale_nonce_no_execute(tmp_path):
    ex = _Exec()
    bot, api = _bot(tmp_path, executor=ex)
    cb = {"callback_query": {"id": "c1", "data": "confirm:ghost", "message": {"chat": {"id": OWNER}}}}
    asyncio.run(bot.handle_update(cb))
    assert ex.calls == []                                  # nothing executed
    assert any("вже оброблено" in p.get("text", "") for m, p in api.calls if m == "sendMessage")
    assert bot._chat.notes == []                           # stale → nothing recorded to memory


def test_cancel_callback(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    pending.put("mcp__garmin__upload_workout", {})
    ex = _Exec()
    bot, api = _bot(tmp_path, executor=ex, pending=pending)
    cb = {"callback_query": {"id": "c1", "data": "cancel:N1", "message": {"chat": {"id": OWNER}}}}
    asyncio.run(bot.handle_update(cb))
    assert ex.calls == []
    assert pending.get("N1").status == "cancelled"
    assert bot._chat.notes == [(OWNER, "✗ Скасовано.")]    # coach remembers it was cancelled


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
