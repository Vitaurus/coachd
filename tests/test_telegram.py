"""Pin Telegram chunking (scar tissue) at the 3999/4000/4001 boundaries + send."""

from __future__ import annotations

from coachd.adapters.telegram import LIMIT, TelegramMessenger, chunk_message, strip_markdown


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
