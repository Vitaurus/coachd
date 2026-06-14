"""Garmin token bootstrap (interactive MFA) and runtime token-state detection.

This is decision #0 in the architecture doc — the *build-first* item, because
Garmin's first login requires email + password + an MFA code and CANNOT run
headless. A stranger therefore cannot get a token file by just `docker compose
up`. The flow that makes the product installable:

    HOST (one-time)                          CONTAINER (every boot)
    ───────────────                          ──────────────────────
    docker compose run --rm coachd login     service starts
      │ asks email                             │ token_status(tokenstore)
      │ asks password (hidden)                 │   ├── VALID       → run normally
      │ Garmin demands MFA ──► asks MFA code   │   ├── MISSING     → tell user to `login`
      │ login() succeeds                       │   ├── EXPIRED     → Telegram re-auth NUDGE
      ▼ client.dump(tokenstore) ──────────┐    │   └── UNREACHABLE → quiet retry (Garmin blip,
        oauth1_token.json                  │    │                     NOT an auth problem → no nudge)
        oauth2_token.json                  │    ▼
                                           └──► mounted volume ◄── same GARMINTOKENS the engine reads

The EXPIRED-vs-UNREACHABLE split is the whole point of the state machine: nudging
"re-login" on every transient Garmin/network blip would train the user to ignore
the nudge, so the genuine-expiry signal must be distinct from a connection blip.
"""

from __future__ import annotations

import getpass
from enum import Enum
from pathlib import Path
from typing import Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectInvalidFileFormatError,
    GarminConnectTooManyRequestsError,
)

# garminconnect's client.dump() writes exactly these two files into the
# tokenstore dir. Their presence is the cheap "do tokens exist at all" probe
# before we pay a network round-trip to validate them.
_TOKEN_GLOB = "oauth*_token.json"


class TokenState(str, Enum):
    """Classified state of the stored Garmin tokens.

    The runtime maps these to behaviour: only EXPIRED triggers the re-auth nudge.
    """

    VALID = "valid"            # tokens load and authenticate → run normally
    MISSING = "missing"        # no token files → user has never run `login`
    EXPIRED = "expired"        # tokens present but rejected → re-login required (NUDGE)
    UNREACHABLE = "unreachable"  # Garmin/network down → transient, retry, do NOT nudge


class TokenExpired(Exception):
    """Raised at runtime when stored tokens no longer authenticate.

    The bot catches this to send the user a Telegram nudge to re-run `login`.
    Distinct from connection errors, which must NOT be treated as expiry.
    """


class LoginFailed(Exception):
    """Interactive login could not complete, with a human-actionable message.

    Wraps Garmin's raw failures (rate-limit, bad credentials, unreachable) so the
    CLI prints one clear line and exits, instead of dumping a traceback or hanging
    in transport/retry churn.
    """


# Injection seams so the flow is testable without a TTY or the network.
EmailPrompt = Callable[[], str]
PasswordPrompt = Callable[[], str]
MfaPrompt = Callable[[], str]


def _default_email_prompt() -> str:
    return input("Garmin email: ").strip()


def _default_password_prompt() -> str:
    return getpass.getpass("Garmin password (hidden): ")


def _default_mfa_prompt() -> str:
    return input("Garmin MFA code (check email/SMS): ").strip()


def run_login(
    tokenstore: str | Path,
    *,
    ask_email: EmailPrompt = _default_email_prompt,
    ask_password: PasswordPrompt = _default_password_prompt,
    ask_mfa: MfaPrompt = _default_mfa_prompt,
    garmin_factory: Callable[..., Garmin] = Garmin,
    out: Callable[[str], None] = print,
) -> None:
    """Interactively log in to Garmin and persist tokens to ``tokenstore``.

    ``prompt_mfa`` fires only when Garmin actually demands an MFA code, so an
    account without MFA still works (the callback is simply never called). The
    Garmin password is read but NEVER persisted — only the resulting OAuth tokens
    are written to disk.

    Designed for ``docker compose run --rm coachd login`` (an interactive TTY).
    All prompts and the Garmin client are injectable so tests drive it headless.
    """
    store = Path(tokenstore).expanduser()
    store.mkdir(parents=True, exist_ok=True)

    email = ask_email().strip()
    if not email:
        raise ValueError("Garmin email is required.")
    password = ask_password()  # never stripped: passwords may contain spaces
    if not password:
        raise ValueError("Garmin password is required.")

    # retry_attempts=1: a 429 or bad-credential failure should surface fast, not
    # back off three times and look like a hang. The user simply re-runs.
    client = garmin_factory(email, password, prompt_mfa=ask_mfa, retry_attempts=1)
    try:
        client.login()  # triggers ask_mfa if Garmin demands MFA
    except GarminConnectTooManyRequestsError as exc:
        raise LoginFailed(
            "Garmin rate-limited your IP (HTTP 429). Wait 15–60 minutes before "
            "trying again — repeated attempts extend the block."
        ) from exc
    except GarminConnectAuthenticationError as exc:
        raise LoginFailed(
            "Garmin rejected the email/password. Check your credentials "
            "(and any MFA code) and try again."
        ) from exc
    except GarminConnectConnectionError as exc:
        raise LoginFailed(
            "Could not reach Garmin. Check your network and try again."
        ) from exc

    client.client.dump(str(store))  # writes oauth1_token.json + oauth2_token.json
    out(f"✓ Garmin tokens saved to {store}")
    out("You can now start the coach: docker compose up -d")


def token_status(
    tokenstore: str | Path,
    *,
    garmin_factory: Callable[..., Garmin] = Garmin,
) -> TokenState:
    """Classify the stored Garmin tokens without raising.

    Cheap file probe first (no token files → MISSING, no network call), then a
    validating load. Auth rejection → EXPIRED (re-login needed). Connection or
    rate-limit failure → UNREACHABLE (transient — retry, never nudge).
    """
    store = Path(tokenstore).expanduser()
    if not store.exists() or not any(store.glob(_TOKEN_GLOB)):
        return TokenState.MISSING

    client = garmin_factory()
    try:
        client.login(tokenstore=str(store))
        return TokenState.VALID
    except GarminConnectInvalidFileFormatError:
        # files exist but are corrupt/unreadable — effectively no usable token
        return TokenState.MISSING
    except GarminConnectAuthenticationError:
        return TokenState.EXPIRED
    except (GarminConnectConnectionError, GarminConnectTooManyRequestsError):
        return TokenState.UNREACHABLE


def ensure_valid(
    tokenstore: str | Path,
    *,
    garmin_factory: Callable[..., Garmin] = Garmin,
) -> None:
    """Raise :class:`TokenExpired` if the stored tokens cannot authenticate.

    Runtime guard for the engine: MISSING and EXPIRED both mean "re-login
    required" and raise; UNREACHABLE is swallowed (transient, caller retries).
    """
    state = token_status(tokenstore, garmin_factory=garmin_factory)
    if state in (TokenState.EXPIRED, TokenState.MISSING):
        raise TokenExpired(
            "Garmin login is no longer valid. Re-run: "
            "docker compose run --rm coachd login"
        )
