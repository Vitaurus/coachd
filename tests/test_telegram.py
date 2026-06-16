"""Pin Telegram chunking (scar tissue) at the 3999/4000/4001 boundaries + send."""

from __future__ import annotations

import pytest

from coachd.adapters.telegram import (
    LIMIT,
    TelegramMessenger,
    chunk_message,
    download_file,
    strip_markdown,
)


def _fake_api(info):
    calls: list = []

    def api(method, params=None):
        calls.append((method, params))
        return info

    api.calls = calls  # type: ignore[attr-defined]
    return api


def test_download_file_happy_path_returns_bytes_and_mime():
    api = _fake_api({"file_path": "photos/file_7.jpg", "file_size": 1234})
    data, mime = download_file("TOK", "FID", api=api, fetch=lambda url: b"\xff\xd8jpegbytes")
    assert data == b"\xff\xd8jpegbytes"
    assert mime == "image/jpeg"
    assert api.calls == [("getFile", {"file_id": "FID"})]


def test_download_file_builds_file_url_with_token_and_path():
    api = _fake_api({"file_path": "photos/file_7.jpg"})
    seen = {}
    download_file("TOK", "FID", api=api, fetch=lambda url: seen.setdefault("url", url) or b"x")
    assert seen["url"] == "https://api.telegram.org/file/botTOK/photos/file_7.jpg"


def test_download_file_mime_by_extension():
    for path, expected in [
        ("a/b.png", "image/png"),
        ("a/b.webp", "image/webp"),
        ("a/b.bin", "image/jpeg"),   # unknown ext → jpeg fallback (Telegram photos are jpeg)
        ("noext", "image/jpeg"),
    ]:
        api = _fake_api({"file_path": path})
        _, mime = download_file("T", "F", api=api, fetch=lambda url: b"x")
        assert mime == expected, path


def test_download_file_refuses_oversized_via_file_size_before_fetch():
    api = _fake_api({"file_path": "p.jpg", "file_size": 99})
    fetched = {"called": False}

    def fetch(url):
        fetched["called"] = True
        return b"x"

    with pytest.raises(ValueError, match="too large"):
        download_file("T", "F", max_bytes=10, api=api, fetch=fetch)
    assert fetched["called"] is False  # cap stops the cost at the source


def test_download_file_refuses_oversized_via_bytes_when_no_file_size():
    api = _fake_api({"file_path": "p.jpg"})  # no file_size from getFile
    with pytest.raises(ValueError, match="too large"):
        download_file("T", "F", max_bytes=3, api=api, fetch=lambda url: b"toolong")


def test_download_file_missing_file_path_raises():
    api = _fake_api({"file_size": 10})
    with pytest.raises(ValueError, match="file_path"):
        download_file("T", "F", api=api, fetch=lambda url: b"x")


def test_strip_markdown_removes_bold_code_headers():
    assert strip_markdown("**Readiness 87**") == "Readiness 87"
    assert strip_markdown("__bold__ and `code`") == "bold and code"
    assert strip_markdown("# Heading\ntext") == "Heading\ntext"
    assert strip_markdown("## Sub — note") == "Sub — note"


def test_strip_markdown_leaves_plain_text_and_legit_asterisks():
    # single * / _ stay (legit "5*5 sets"); emoji + catalog text untouched
    assert strip_markdown("5*5 sets at Z2") == "5*5 sets at Z2"
    assert strip_markdown("⏸ Action needs confirmation: x") == "⏸ Action needs confirmation: x"
    # idempotent on already-clean text
    clean = "Readiness 87 — HIGH. Sleep 91/EXCELLENT."
    assert strip_markdown(clean) == clean


def test_messenger_strips_markdown_before_send():
    posts: list = []
    m = TelegramMessenger("tok", 1, post=lambda url, data: posts.append(data))
    m.send("**Readiness 87 — HIGH.**")
    assert b"%2A%2A" not in posts[0]            # the ** is gone (not URL-encoded either)
    assert b"Readiness+87" in posts[0]


def test_short_text_single_chunk():
    assert chunk_message("hello") == ["hello"]


def test_exactly_limit_is_one_chunk():
    s = "a" * LIMIT
    assert chunk_message(s) == [s]


def test_one_below_limit_is_one_chunk():
    s = "a" * (LIMIT - 1)
    assert chunk_message(s) == [s]


def test_one_over_limit_hard_cut_when_no_newline():
    s = "a" * (LIMIT + 1)
    chunks = chunk_message(s)
    assert len(chunks) == 2
    assert len(chunks[0]) == LIMIT
    assert chunks[1] == "a"


def test_prefers_last_newline_before_limit():
    head = "x" * 3000 + "\n" + "y" * 2000  # newline at index 3000, well before 4000
    chunks = chunk_message(head)
    assert chunks[0] == "x" * 3000          # break at the newline, not at 4000
    assert chunks[1] == "y" * 2000          # leading newline stripped from remainder


def test_empty_text_no_chunks():
    assert chunk_message("") == []


def test_send_posts_each_chunk_in_order():
    posts: list[tuple[str, bytes]] = []
    m = TelegramMessenger("TOKEN", 12345, limit=10, post=lambda url, data: posts.append((url, data)))
    n = m.send("a" * 25)  # 10 + 10 + 5 → 3 chunks
    assert n == 3
    assert len(posts) == 3
    assert all("bot TOKEN".replace(" ", "") in url for url, _ in posts)
    # chat id + plain-text flags present
    assert b"chat_id=12345" in posts[0][1]
    assert b"disable_web_page_preview=true" in posts[0][1]


def test_send_empty_does_nothing():
    posts: list = []
    m = TelegramMessenger("T", 1, post=lambda u, d: posts.append((u, d)))
    assert m.send("   \n ") == 0
    assert posts == []
