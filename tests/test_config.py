"""Pin ServiceConfig fail-fast parsing/validation."""

from __future__ import annotations

from datetime import date

import pytest

from coachd.config import DEFAULT_MODEL, ConfigError, ServiceConfig

_VALID = {
    "TG_BOT_TOKEN": "123:abc",
    "TG_CHAT_ID": "12345",
    "ANTHROPIC_API_KEY": "sk-ant-xxx",
    "USER_NAME": "Віталій",
    "WORN_START": "2026-06-08",
    "TZ": "Europe/Kyiv",
}


def test_valid_env_parses():
    c = ServiceConfig.from_env(dict(_VALID))
    assert c.owner_chat_ids == (12345,)
    assert c.worn_start == date(2026, 6, 8)
    assert c.user_name == "Віталій"
    assert c.model == DEFAULT_MODEL
    assert c.use_1m_context is False


def test_household_chat_ids_comma_separated():
    env = dict(_VALID, TG_CHAT_ID="111, 222 , 333")
    assert ServiceConfig.from_env(env).owner_chat_ids == (111, 222, 333)


def test_model_and_1m_overrides():
    env = dict(_VALID, MODEL="claude-opus-4-8", USE_1M_CONTEXT="true")
    c = ServiceConfig.from_env(env)
    assert c.model == "claude-opus-4-8"
    assert c.use_1m_context is True


def test_lang_defaults_to_oss_baseline_en():
    # no COACH_LANG → English (the OSS default); a live UA deploy must opt in
    assert ServiceConfig.from_env(dict(_VALID)).lang == "en"


def test_lang_accepts_uk_case_insensitive():
    assert ServiceConfig.from_env(dict(_VALID, COACH_LANG="uk")).lang == "uk"
    assert ServiceConfig.from_env(dict(_VALID, COACH_LANG="UK")).lang == "uk"


def test_unsupported_lang_is_rejected():
    with pytest.raises(ConfigError) as ei:
        ServiceConfig.from_env(dict(_VALID, COACH_LANG="fr"))
    assert "COACH_LANG" in str(ei.value)


def test_oauth_token_instead_of_api_key_is_valid():
    # Claude subscription users authenticate with `claude setup-token`, not a
    # console.anthropic.com key. Either credential satisfies auth.
    env = {k: v for k, v in _VALID.items() if k != "ANTHROPIC_API_KEY"}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-zzz"
    c = ServiceConfig.from_env(env)
    assert c.oauth_token == "sk-ant-oat01-zzz"
    assert c.anthropic_api_key == ""


def test_oauth_token_misplaced_in_api_key_is_rejected():
    # The user's real mistake: pasting the setup-token into ANTHROPIC_API_KEY,
    # which the API rejects as an x-api-key (401). Catch it with a clear nudge.
    env = dict(_VALID, ANTHROPIC_API_KEY="sk-ant-oat01-zzz")
    with pytest.raises(ConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        ServiceConfig.from_env(env)


def test_missing_auth_mentions_both_options():
    env = {k: v for k, v in _VALID.items() if k != "ANTHROPIC_API_KEY"}
    with pytest.raises(ConfigError) as ei:
        ServiceConfig.from_env(env)
    msg = str(ei.value)
    assert "ANTHROPIC_API_KEY" in msg and "CLAUDE_CODE_OAUTH_TOKEN" in msg


def test_missing_required_reports_all_at_once():
    with pytest.raises(ConfigError) as ei:
        ServiceConfig.from_env({})
    msg = str(ei.value)
    for key in ("TG_BOT_TOKEN", "TG_CHAT_ID", "ANTHROPIC_API_KEY", "USER_NAME", "TZ", "WORN_START"):
        assert key in msg  # every missing field surfaced together


def test_bad_worn_start_rejected():
    env = dict(_VALID, WORN_START="08-06-2026")
    with pytest.raises(ConfigError, match="WORN_START"):
        ServiceConfig.from_env(env)


def test_bad_timezone_rejected():
    env = dict(_VALID, TZ="Mars/Olympus")
    with pytest.raises(ConfigError, match="TZ"):
        ServiceConfig.from_env(env)


def test_bad_chat_id_rejected():
    env = dict(_VALID, TG_CHAT_ID="not-a-number")
    with pytest.raises(ConfigError, match="TG_CHAT_ID"):
        ServiceConfig.from_env(env)
