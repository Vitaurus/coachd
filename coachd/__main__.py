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

_USAGE = "usage: python -m coachd {login|token-status|serve}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(_USAGE, file=sys.stderr)
        return 2

    command, *_rest = argv
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
        scheduler.start()
        print(
            f"coachd serve: report scheduler started "
            f"(morning {morning}, evening {evening}, TZ {config.tz}). "
            f"Chat lands next.",
            flush=True,
        )
        await asyncio.Event().wait()  # run until the container stops

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\ncoachd serve: stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
