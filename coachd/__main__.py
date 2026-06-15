"""CLI entrypoint: ``python -m coachd <command>`` (also the ``coachd`` script).

#0 commands only (the MFA bootstrap spike):
    login          interactive Garmin login (email + password + MFA) → save tokens
    token-status   print the token state and exit non-zero unless VALID

The long-running service (`serve`) is added in a later step.
"""

from __future__ import annotations

import sys

from .auth.garmin_login import LoginFailed, TokenState, run_login, token_status
from .config import ConfigError, resolve_tokenstore

_USAGE = "usage: python -m coachd {login|token-status|chat-id|serve}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(_USAGE, file=sys.stderr)
        return 2

    command, *_rest = argv

    # chat-id needs only TG_BOT_TOKEN — dispatch it BEFORE resolving the Garmin
    # token store (it runs during setup, before any Garmin login exists).
    if command == "chat-id":
        return _chat_id()

    try:
        tokenstore = resolve_tokenstore()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if command == "login":
        try:
            run_login(tokenstore)
        except LoginFailed as exc:
            print(f"login failed: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nlogin cancelled.", file=sys.stderr)
            return 130
        return 0

    if command == "token-status":
        state = token_status(tokenstore)
        print(state.value)
        return 0 if state is TokenState.VALID else 1

    if command == "serve":
        return _serve()

    print(f"unknown command: {command}\n{_USAGE}", file=sys.stderr)
    return 2


def _chat_id(*, discover: object = None) -> int:
    """Discover the Telegram chat id(s) of whoever messaged the bot.

    Needs only TG_BOT_TOKEN. ``discover`` is injectable for tests (defaults to
    the real network call).
    """
    import os
    import urllib.error

    token = (os.environ.get("TG_BOT_TOKEN") or "").strip()
    if not token:
        print(
            "chat-id: TG_BOT_TOKEN not set. Put it in .env, or pass it inline:\n"
            "  docker compose run --rm -e TG_BOT_TOKEN=<token> coachd chat-id",
            file=sys.stderr,
        )
        return 2

    if discover is None:
        from .adapters.telegram import discover_chat_ids as discover

    try:
        refs = discover(token)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            print(
                "chat-id: Telegram 409 — the bot is already polling getUpdates. "
                "Stop it first: docker compose down",
                file=sys.stderr,
            )
        elif exc.code == 401:
            print(
                "chat-id: Telegram 401 — bad TG_BOT_TOKEN. Check the token from @BotFather.",
                file=sys.stderr,
            )
        else:
            print(f"chat-id: Telegram error HTTP {exc.code}.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — surface any network failure as one line
        print(f"chat-id: could not reach Telegram ({exc}).", file=sys.stderr)
        return 1

    if not refs:
        print(
            "Напиши боту будь-що в Telegram, потім повтори:\n"
            "  docker compose run --rm coachd chat-id"
        )
        return 1

    for r in refs:
        print(f"  {r.id}  — {r.label} ({r.type})")
    print("\nВстав у .env:")
    print("TG_CHAT_ID=" + ",".join(str(r.id) for r in refs))
    return 0


def _serve() -> int:
    """Run the long-running coach: timezone-aware report scheduler.

    Chat (the interactive bot) lands next; today serve fires the morning/evening
    reports. Agent turns require the bundled `claude` CLI at runtime (see Docker).
    """
    import asyncio
    import os

    from .app import build_app
    from .auth.garmin_login import token_status
    from .config import ConfigError, ServiceConfig
    from .scheduler import ReportScheduler

    try:
        config = ServiceConfig.from_env()
    except ConfigError as exc:
        print(f"config error:\n{exc}", file=sys.stderr)
        return 2

    # Fail fast on a bad Anthropic credential — otherwise every chat turn (and
    # report) dies with a cryptic "Claude Code returned an error result: success"
    # loop. A transient outage is tolerated (we proceed and retry per-turn).
    from .adapters.anthropic_agent import probe_anthropic_auth

    auth = probe_anthropic_auth(config.anthropic_api_key, config.oauth_token)
    if auth == "rejected":
        print(
            "serve: Anthropic відхилив креденшл (HTTP 401). Перевір ANTHROPIC_API_KEY "
            "(ключ з console.anthropic.com) або CLAUDE_CODE_OAUTH_TOKEN (від "
            "`claude setup-token`) — і памʼятай: OAuth-токен ставиться в "
            "CLAUDE_CODE_OAUTH_TOKEN, не в ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2
    if auth == "unreachable":
        print(
            "serve: не вдалося перевірити Anthropic-креденшл (мережа?). Стартую далі — "
            "звернення можуть падати, поки звʼязок не відновиться.",
            flush=True,
        )

    morning = os.environ.get("MORNING_TIME", "07:00")
    evening = os.environ.get("EVENING_TIME", "22:00")

    async def _run() -> None:
        app = build_app(config)
        scheduler = ReportScheduler(
            app,
            morning=morning,
            evening=evening,
            token_state_fn=lambda ts: token_status(ts),
        )
        scheduler.start()  # background cron jobs on this loop
        print(
            f"coachd serve: reports (morning {morning}, evening {evening}, "
            f"TZ {config.tz}) + chat bot starting.",
            flush=True,
        )
        await app.bot.run()  # long-poll loop; keeps the process alive

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\ncoachd serve: stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
