"""CLI entrypoint: ``python -m coachd <command>`` (also the ``coachd`` script).

#0 commands only (the MFA bootstrap spike):
    login          interactive Garmin login (email + password + MFA) → save tokens
    token-status   print the token state and exit non-zero unless VALID

The long-running service (`serve`) is added in a later step.
"""

from __future__ import annotations

import sys

from .auth.garmin_login import TokenState, run_login, token_status
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
        run_login(tokenstore)
        return 0

    if command == "token-status":
        state = token_status(tokenstore)
        print(state.value)
        return 0 if state is TokenState.VALID else 1

    if command == "serve":
        # Placeholder until the coach loop (scheduler + bot) lands. Exits cleanly
        # so `docker compose up` does not crash-loop on an unbuilt service.
        print(
            "serve: not implemented yet — the coach loop is in progress.\n"
            "Token bootstrap works today: `docker compose run --rm coachd login`."
        )
        return 0

    print(f"unknown command: {command}\n{_USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
