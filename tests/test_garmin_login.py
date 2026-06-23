"""Tests for the #0 Garmin MFA bootstrap + token-state classification.

Fully headless: the Garmin client and all prompts are injected, so nothing here
touches a TTY or the network. The token file written by the fake mirrors what
garminconnect 0.3.6's ``client.dump()`` produces (a single ``garmin_tokens.json``
inside the tokenstore dir), so the file-presence probe in ``token_status`` is
exercised against the real on-disk shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectInvalidFileFormatError,
    GarminConnectTooManyRequestsError,
)

from coachd.auth.garmin_login import (
    LoginFailed,
    TokenExpired,
    TokenState,
    ensure_valid,
    run_login,
    token_status,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeGarthClient:
    """Stands in for garminconnect's internal client; records the dump path and
    writes the token file exactly where the real one would."""

    def __init__(self, record: dict) -> None:
        self._record = record

    def dump(self, path: str) -> None:
        self._record["dumped_to"] = path
        store = Path(path)
        # mirror garminconnect 0.3.6: a dir (or non-.json path) → garmin_tokens.json
        target = (
            store / "garmin_tokens.json"
            if store.is_dir() or not store.name.endswith(".json")
            else store
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")


class _FakeGarmin:
    """Configurable Garmin double.

    Used two ways, matching the real factory call sites:
      * ``factory(email, password, prompt_mfa=...)`` for the login flow
      * ``factory()`` for token validation
    """

    def __init__(self, email=None, password=None, prompt_mfa=None, *, record=None, **_kw):
        # **_kw swallows real constructor kwargs like retry_attempts
        self._record = record if record is not None else {}
        self._record["email"] = email
        self._record["password"] = password
        self._prompt_mfa = prompt_mfa
        self.client = _FakeGarthClient(self._record)

    def login(self, tokenstore=None):
        # interactive login path: simulate Garmin demanding an MFA code
        if self._record.get("simulate_mfa") and self._prompt_mfa is not None:
            self._record["mfa_entered"] = self._prompt_mfa()
        # validation path: optionally raise the configured error
        exc = self._record.get("login_raises")
        if exc is not None:
            raise exc
        return (None, None)


def _factory(record: dict):
    def make(email=None, password=None, prompt_mfa=None, **kw):
        return _FakeGarmin(email, password, prompt_mfa=prompt_mfa, record=record, **kw)

    return make


# --------------------------------------------------------------------------- #
# run_login
# --------------------------------------------------------------------------- #
def test_run_login_saves_tokens_and_runs_mfa(tmp_path):
    record = {"simulate_mfa": True}
    out_lines: list[str] = []

    run_login(
        tmp_path,
        ask_email=lambda: "rider@example.com",
        ask_password=lambda: "hunter2",
        ask_mfa=lambda: "123456",
        garmin_factory=_factory(record),
        out=out_lines.append,
    )

    # credentials reached the client; MFA callback fired and delivered the code
    assert record["email"] == "rider@example.com"
    assert record["password"] == "hunter2"
    assert record["mfa_entered"] == "123456"
    # tokens persisted to the store
    assert (tmp_path / "garmin_tokens.json").exists()
    assert record["dumped_to"] == str(tmp_path)
    assert any("saved" in line for line in out_lines)


def test_run_login_without_mfa_skips_callback(tmp_path):
    """Accounts without MFA never invoke the MFA prompt."""
    record = {"simulate_mfa": False}

    run_login(
        tmp_path,
        ask_email=lambda: "rider@example.com",
        ask_password=lambda: "hunter2",
        ask_mfa=lambda: pytest.fail("MFA prompt must not be called"),
        garmin_factory=_factory(record),
        out=lambda _l: None,
    )

    assert "mfa_entered" not in record
    assert (tmp_path / "garmin_tokens.json").exists()


def test_run_login_requires_email(tmp_path):
    with pytest.raises(ValueError, match="email"):
        run_login(
            tmp_path,
            ask_email=lambda: "   ",  # whitespace → empty after strip upstream
            ask_password=lambda: "hunter2",
            ask_mfa=lambda: "x",
            garmin_factory=_factory({}),
            out=lambda _l: None,
        )


def test_run_login_rate_limited_raises_clean(tmp_path):
    """A 429 surfaces as an actionable LoginFailed, not a raw error or hang."""
    record = {"login_raises": GarminConnectTooManyRequestsError("429")}
    with pytest.raises(LoginFailed, match="rate-limited"):
        run_login(
            tmp_path,
            ask_email=lambda: "rider@example.com",
            ask_password=lambda: "hunter2",
            ask_mfa=lambda: "x",
            garmin_factory=_factory(record),
            out=lambda _l: None,
        )
    # nothing persisted on failure
    assert not (tmp_path / "garmin_tokens.json").exists()


def test_run_login_bad_credentials_raises_clean(tmp_path):
    record = {"login_raises": GarminConnectAuthenticationError("401")}
    with pytest.raises(LoginFailed, match="rejected"):
        run_login(
            tmp_path,
            ask_email=lambda: "rider@example.com",
            ask_password=lambda: "wrong",
            ask_mfa=lambda: "x",
            garmin_factory=_factory(record),
            out=lambda _l: None,
        )


def test_run_login_requires_password(tmp_path):
    with pytest.raises(ValueError, match="password"):
        run_login(
            tmp_path,
            ask_email=lambda: "rider@example.com",
            ask_password=lambda: "",
            ask_mfa=lambda: "x",
            garmin_factory=_factory({}),
            out=lambda _l: None,
        )


# --------------------------------------------------------------------------- #
# token_status classification
# --------------------------------------------------------------------------- #
def _seed_tokens(store: Path) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text("{}")


def test_status_missing_when_no_files(tmp_path):
    assert token_status(tmp_path, garmin_factory=_factory({})) is TokenState.MISSING


def test_status_missing_for_legacy_oauth_files_only(tmp_path):
    # garth's old oauth1/oauth2 pair is NOT the garminconnect 0.3.6 format; a
    # store holding only those must read as MISSING. Regression: the probe once
    # matched oauth*_token.json and so reported MISSING for a real (garmin_tokens
    # .json) login while reporting VALID for these stale leftovers — exactly
    # backwards. Pin the real filename.
    (tmp_path / "oauth1_token.json").write_text("{}")
    (tmp_path / "oauth2_token.json").write_text("{}")
    assert token_status(tmp_path, garmin_factory=_factory({})) is TokenState.MISSING


def test_status_valid(tmp_path):
    _seed_tokens(tmp_path)
    assert token_status(tmp_path, garmin_factory=_factory({})) is TokenState.VALID


def test_status_expired_on_auth_error(tmp_path):
    _seed_tokens(tmp_path)
    record = {"login_raises": GarminConnectAuthenticationError("rejected")}
    assert token_status(tmp_path, garmin_factory=_factory(record)) is TokenState.EXPIRED


@pytest.mark.parametrize(
    "exc",
    [
        GarminConnectConnectionError("garmin down"),
        GarminConnectTooManyRequestsError("rate limited"),
    ],
)
def test_status_unreachable_on_transient(tmp_path, exc):
    _seed_tokens(tmp_path)
    record = {"login_raises": exc}
    assert token_status(tmp_path, garmin_factory=_factory(record)) is TokenState.UNREACHABLE


def test_status_invalid_format_is_missing(tmp_path):
    _seed_tokens(tmp_path)
    record = {"login_raises": GarminConnectInvalidFileFormatError("corrupt")}
    assert token_status(tmp_path, garmin_factory=_factory(record)) is TokenState.MISSING


# --------------------------------------------------------------------------- #
# ensure_valid (runtime guard → re-auth nudge)
# --------------------------------------------------------------------------- #
def test_ensure_valid_passes_when_valid(tmp_path):
    _seed_tokens(tmp_path)
    ensure_valid(tmp_path, garmin_factory=_factory({}))  # no raise


def test_ensure_valid_raises_on_expired(tmp_path):
    _seed_tokens(tmp_path)
    record = {"login_raises": GarminConnectAuthenticationError("rejected")}
    with pytest.raises(TokenExpired, match="login"):
        ensure_valid(tmp_path, garmin_factory=_factory(record))


def test_ensure_valid_raises_on_missing(tmp_path):
    with pytest.raises(TokenExpired):
        ensure_valid(tmp_path, garmin_factory=_factory({}))


def test_ensure_valid_swallows_unreachable(tmp_path):
    """A Garmin/network blip must NOT be treated as expiry (no false nudge)."""
    _seed_tokens(tmp_path)
    record = {"login_raises": GarminConnectConnectionError("garmin down")}
    ensure_valid(tmp_path, garmin_factory=_factory(record))  # no raise
