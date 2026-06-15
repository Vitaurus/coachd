"""Tests for the `chat-id` discovery command + its telegram helpers."""

from __future__ import annotations

import urllib.error

import pytest

from coachd import __main__ as cli
from coachd.adapters import telegram
from coachd.adapters.telegram import ChatRef, discover_chat_ids, make_api, parse_chat_ids


# --------------------------------------------------------------------------- #
# parse_chat_ids
# --------------------------------------------------------------------------- #
def _msg(cid, *, type="private", **chat):
    return {"message": {"chat": {"id": cid, "type": type, **chat}}}


def test_private_chat_uses_first_name():
    refs = parse_chat_ids([_msg(123, first_name="Віталій")])
    assert refs == [ChatRef(id=123, label="Віталій", type="private")]


def test_group_chat_uses_title():
    refs = parse_chat_ids([{"message": {"chat": {"id": -55, "type": "group", "title": "Family"}}}])
    assert refs == [ChatRef(id=-55, label="Family", type="group")]


def test_edited_message_counts():
    refs = parse_chat_ids([{"edited_message": {"chat": {"id": 7, "type": "private", "username": "v"}}}])
    assert refs == [ChatRef(id=7, label="v", type="private")]


def test_dedup_by_id_first_seen_order():
    refs = parse_chat_ids([_msg(1, first_name="A"), _msg(2, first_name="B"), _msg(1, first_name="A2")])
    assert [r.id for r in refs] == [1, 2]  # 1 appears once, order preserved


def test_no_name_field_falls_back_to_id():
    refs = parse_chat_ids([{"message": {"chat": {"id": 999, "type": "private"}}}])
    assert refs == [ChatRef(id=999, label="999", type="private")]


def test_empty_updates():
    assert parse_chat_ids([]) == []
    assert parse_chat_ids(None) == []


def test_callback_query_only_is_ignored():
    # callback_query never fires during first-time discovery → not walked
    refs = parse_chat_ids([{"callback_query": {"message": {"chat": {"id": 5, "type": "private"}}}}])
    assert refs == []


# --------------------------------------------------------------------------- #
# discover_chat_ids (injected api)
# --------------------------------------------------------------------------- #
def test_discover_calls_deletewebhook_then_getupdates():
    calls = []

    def fake_api(method, params=None):
        calls.append(method)
        return [_msg(42, first_name="V")] if method == "getUpdates" else None

    refs = discover_chat_ids("tok", api=fake_api)
    assert calls == ["deleteWebhook", "getUpdates"]
    assert refs == [ChatRef(id=42, label="V", type="private")]


# --------------------------------------------------------------------------- #
# make_api — regression for the #4 json import (exercises json.loads)
# --------------------------------------------------------------------------- #
def test_make_api_parses_getupdates(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true,"result":[{"update_id":1}]}'

    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda *a, **k: _Resp())
    api = make_api("tok")
    assert api("getUpdates", {}) == [{"update_id": 1}]  # json.loads worked → import present


# --------------------------------------------------------------------------- #
# _chat_id command (injected discover, env-controlled token)
# --------------------------------------------------------------------------- #
def test_chat_id_found_prints_paste_line(monkeypatch, capsys):
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    rc = cli._chat_id(discover=lambda token: [ChatRef(99, "Віталій", "private")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "99" in out
    assert "TG_CHAT_ID=99" in out


def test_chat_id_multiple_ids_comma_joined(monkeypatch, capsys):
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    rc = cli._chat_id(discover=lambda token: [ChatRef(1, "A", "private"), ChatRef(2, "B", "private")])
    assert rc == 0
    assert "TG_CHAT_ID=1,2" in capsys.readouterr().out


def test_chat_id_empty_prompts_to_message(monkeypatch, capsys):
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    rc = cli._chat_id(discover=lambda token: [])
    assert rc == 1
    assert "Напиши боту" in capsys.readouterr().out


def test_chat_id_missing_token_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    rc = cli._chat_id(discover=lambda token: pytest.fail("must not call discover"))
    assert rc == 2
    assert "TG_BOT_TOKEN not set" in capsys.readouterr().err


def _http_error(code):
    def _raise(token):
        raise urllib.error.HTTPError("url", code, "err", None, None)
    return _raise


def test_chat_id_409_actionable(monkeypatch, capsys):
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    rc = cli._chat_id(discover=_http_error(409))
    assert rc == 1
    assert "409" in capsys.readouterr().err


def test_chat_id_401_actionable(monkeypatch, capsys):
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    rc = cli._chat_id(discover=_http_error(401))
    assert rc == 1
    assert "401" in capsys.readouterr().err
