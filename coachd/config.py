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
from dataclasses import dataclass
from datetime import date
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


# Default coaching model — overridable via MODEL. A balanced daily default; set
# MODEL=claude-opus-4-8 for max quality (the user pays for their own key).
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class ServiceConfig:
    """Full configuration for the long-running coach (the `serve` entrypoint).

    Validated all-at-once at boot: every missing/invalid field is collected and
    reported together, so a stranger fixes their whole .env in one pass instead
    of discovering problems one restart at a time.
    """

    tg_bot_token: str
    owner_chat_ids: tuple[int, ...]
    # Exactly one of these carries the Anthropic credential; the bundled claude
    # CLI reads whichever is in the environment (we don't pass it explicitly).
    anthropic_api_key: str  # console.anthropic.com pay-as-you-go key, or ""
    oauth_token: str  # `claude setup-token` subscription token, or ""
    user_name: str
    worn_start: date
    tz: str
    tokenstore: str
    model: str
    use_1m_context: bool

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ServiceConfig":
        env = os.environ if env is None else env
        problems: list[str] = []

        def required(key: str) -> str:
            val = (env.get(key) or "").strip()
            if not val:
                problems.append(f"{key} is required")
            return val

        tg_bot_token = required("TG_BOT_TOKEN")
        user_name = required("USER_NAME")
        tz = required("TZ")
        worn_raw = required("WORN_START")

        # owner chat ids: TG_CHAT_ID, comma-separated for a household (1..N)
        owner_ids: tuple[int, ...] = ()
        raw_ids = (env.get("TG_CHAT_ID") or "").strip()
        if not raw_ids:
            problems.append("TG_CHAT_ID is required")
        else:
            try:
                owner_ids = tuple(int(p.strip()) for p in raw_ids.split(",") if p.strip())
            except ValueError:
                problems.append("TG_CHAT_ID must be integer chat id(s), comma-separated")
            if not owner_ids:
                problems.append("TG_CHAT_ID had no valid chat id")

        # worn_start must be an ISO date
        worn_start = date(1970, 1, 1)
        if worn_raw:
            try:
                worn_start = date.fromisoformat(worn_raw)
            except ValueError:
                problems.append(f"WORN_START={worn_raw!r} is not an ISO date (YYYY-MM-DD)")

        # TZ must be a real zone — wrong TZ silently skews recovery/night attribution
        if tz:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(tz)
            except Exception:
                problems.append(f"TZ={tz!r} is not a valid timezone (e.g. Europe/Kyiv)")

        # Anthropic auth: a pay-as-you-go key (ANTHROPIC_API_KEY) OR a Claude
        # subscription token from `claude setup-token` (CLAUDE_CODE_OAUTH_TOKEN).
        # Both start with "sk-ant-", but the OAuth token (sk-ant-oat…) is rejected
        # when sent as an x-api-key — catch that mix-up explicitly.
        anthropic_api_key = (env.get("ANTHROPIC_API_KEY") or "").strip()
        oauth_token = (env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if anthropic_api_key.startswith("sk-ant-oat"):
            problems.append(
                "ANTHROPIC_API_KEY looks like a `claude setup-token` OAuth token "
                "(sk-ant-oat…). Put it in CLAUDE_CODE_OAUTH_TOKEN and leave "
                "ANTHROPIC_API_KEY empty — the API rejects an OAuth token sent as "
                "an x-api-key."
            )
        elif not anthropic_api_key and not oauth_token:
            problems.append(
                "ANTHROPIC_API_KEY (a console.anthropic.com API key) or "
                "CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) is required"
            )

        tokenstore = (env.get(TOKENSTORE_ENV) or DEFAULT_TOKENSTORE).strip()
        model = (env.get("MODEL") or DEFAULT_MODEL).strip()
        use_1m = (env.get("USE_1M_CONTEXT") or "").strip().lower() in ("1", "true", "yes")

        if problems:
            raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(problems))

        return cls(
            tg_bot_token=tg_bot_token,
            owner_chat_ids=owner_ids,
            anthropic_api_key=anthropic_api_key,
            oauth_token=oauth_token,
            user_name=user_name,
            worn_start=worn_start,
            tz=tz,
            tokenstore=tokenstore,
            model=model,
            use_1m_context=use_1m,
        )
