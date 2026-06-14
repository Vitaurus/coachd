"""Typed configuration, resolved from the environment, validated fail-fast.

Why fail-fast: the legacy chatbot read ``os.environ["TG_CHAT_ID"]`` inline, so a
missing var surfaced as a random KeyError mid-run. Here every required field is
validated once at boot and a missing/invalid one raises ``ConfigError`` with a
clear, actionable message naming exactly what is wrong.

Layering (deliberate): the ``login`` / ``token-status`` commands run BEFORE a
stranger has configured Telegram or Anthropic — they only need the Garmin
tokenstore. So tokenstore resolution lives in its own light helper
(:func:`resolve_tokenstore`) and does NOT require the full service config. The
full :class:`Config` (Telegram, Anthropic, scheduler) is validated only when the
long-running service boots (added in a later step).
"""

from __future__ import annotations

import os
from pathlib import Path

# garminconnect reads this env var natively as its tokenstore. The MCP that the
# coach engine will use reads the SAME store, so login and runtime agree by
# construction. Keep the name aligned with garminconnect's contract.
TOKENSTORE_ENV = "GARMINTOKENS"
DEFAULT_TOKENSTORE = "/data/garmin"


class ConfigError(Exception):
    """Raised at boot when required configuration is missing or invalid."""


def resolve_tokenstore(env: dict[str, str] | None = None) -> Path:
    """Resolve the Garmin tokenstore directory and ensure it is usable.

    Reads ``$GARMINTOKENS`` (default ``/data/garmin``, the mounted volume in the
    container). Creates the directory if absent and verifies it is writable, so a
    bad mount fails here with a clear message rather than deep inside the login
    flow after the user has already typed their password and MFA code.
    """
    env = os.environ if env is None else env
    raw = (env.get(TOKENSTORE_ENV) or DEFAULT_TOKENSTORE).strip()
    path = Path(raw).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"{TOKENSTORE_ENV}={raw!r} is not creatable ({exc}). "
            f"Mount a writable volume there (e.g. ./data/garmin:/data/garmin)."
        ) from exc
    if not os.access(path, os.W_OK):
        raise ConfigError(
            f"{TOKENSTORE_ENV}={raw!r} exists but is not writable. "
            f"Fix the volume permissions so the container user can write tokens."
        )
    return path
