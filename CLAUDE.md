# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**coachd** — open-source, self-hosted AI health coach over your Garmin Connect data,
delivered as a closed loop in Telegram (morning/evening readiness reports + chat +
confirmation-first workouts to your watch). Python 3.12+, hexagonal architecture,
`uv` for deps, Docker for deploy. Single-user/household, self-hosted: your data, your
keys, your machine.

Greenfield rewrite. The legacy LXC scripts under `../garmin/opt/garmin-coach/` are a
**BEHAVIORAL SPEC ONLY** — never import them (see `coachd/__init__.py`).

## Commands

`uv` is the sole dependency manager (no pip/poetry). Python `>=3.12`.

```bash
# Setup
uv sync --extra dev                 # add --extra voice for local STT

# Tests (no linter/formatter/typecheck configured — pytest only)
uv run pytest -q                                  # all
uv run pytest tests/test_app.py -q                # one file
uv run pytest tests/test_app.py::test_name -q     # one test
# CI runs: uv sync --extra dev --frozen && uv run pytest -q  (--frozen fails on stale uv.lock)

# Run locally (needs a populated .env)
uv run python -m coachd serve         # the daemon (scheduler + Telegram polling)
uv run python -m coachd login         # one-time interactive Garmin login (email+password+MFA)
uv run python -m coachd chat-id       # discover your Telegram chat id
uv run python -m coachd token-status  # Garmin token state -> exit code

# Docker (default image is text-only/lean; voice is opt-in at BUILD time)
docker compose build                  # lean (~1.16GB)
VOICE=true docker compose build       # + faster-whisper/STT (~1.48GB)
docker compose run --rm coachd login  # interactive — needs a TTY (run, not exec)
docker compose up -d
```

## Architecture

Hexagonal. The dependency rule is the load-bearing convention: **third-party SDK
imports live only in `adapters/`; `core/` depends on `ports/` (Protocols), never on a
concrete SDK.**

- `coachd/core/` — pure domain, no I/O. `engine.py` (CoachEngine: read-only morning/
  evening reports), `chat.py` (ChatEngine: one interactive turn + bounded history),
  `pending.py` (PendingStore: nonce-keyed proposed writes), `journal.py` (append-only
  JSONL of outcomes), `session_store.py` (per-chat history).
- `coachd/ports/` — Protocol interfaces: `llm.py` (LLMPort), `datasource.py`
  (DataSourcePort), `stt.py` (STTPort).
- `coachd/adapters/` — concrete impls: `anthropic_agent.py` (Claude Agent SDK + MCP +
  `can_use_tool` callback), `garmin_provider.py` (community Garmin MCP; READ_TOOLS /
  WRITE_TOOLS allowlists), `telegram_bot.py` (getUpdates polling, owner-gate filtered),
  `faster_whisper_stt.py` (lazy import — module loads fine in a lean image, only
  `load()` fails).
- `coachd/security/` — `authenticator.py` (OwnerGate), `write_guard.py` (make_write_guard).
- `coachd/auth/` — Garmin OAuth / token / MFA.
- `coachd/app.py` — composition root: `build_app(ServiceConfig)` validates env and wires
  the graph (a read-only `report_agent` and a read+write `chat_agent` gated by the
  write-guard, the two engines, OwnerGate, TelegramBot, stores, Journal).
- `coachd/config.py` — env validation. `coachd/__main__.py` — CLI dispatch.
  `coachd/scheduler.py` — APScheduler cron (morning/evening in `config.tz`).

`serve` runs the cron scheduler and the Telegram polling loop concurrently forever.
State lives under `/data/`: `garmin/` (OAuth tokens), `sessions.json`, `journal.jsonl`,
`pending.json` (nonce-keyed confirmations), `offset` (poll resume), `whisper/` (model cache).

## Invariants — do not weaken (test-enforced, PR-blocking)

- **Owner-gate is the only Telegram trust boundary.** The bot acts only for chat ids in
  `TG_CHAT_ID` (comma-separated for a household). The bot token is public; the allowlist
  is the gate. `coachd/security/authenticator.py`.
- **Write-guard is confirmation-first.** Every state-changing tool call is parked to disk
  as a PendingAction and denied to the agent; it executes only after explicit Telegram
  confirmation (nonce = idempotency key, safe across restarts). Reports are read-only by
  construction. WRITE_TOOLS deliberately excludes destructive ops (delete/unschedule).
  `coachd/security/write_guard.py`, `tests/test_write_guard.py`.
- **Source is English-only.** No Cyrillic (or other non-ASCII user text) in `coachd/**.py`;
  user-facing strings go through the i18n catalog `coachd/core/i18n.py`. Enforced by
  `tests/test_no_cyrillic_source.py` (the catalog is the sole exception; tests are exempt).
- **i18n parity.** Every key in every language, matching `{placeholders}`. `COACH_LANG`
  (`en` default / `uk`) switches output; the prompt base is always English.
  `tests/test_i18n.py`.

## Configuration (env)

Full table in `README.md`. Essentials for `serve`:

- Required: `TG_BOT_TOKEN`, `TG_CHAT_ID`, `USER_NAME`, `WORN_START` (ISO date), `TZ` (IANA).
- **Anthropic auth — set EXACTLY ONE:** `ANTHROPIC_API_KEY` (`sk-ant-api…` from the
  console) **or** `CLAUDE_CODE_OAUTH_TOKEN` (`sk-ant-oat…` from `claude setup-token`, a
  Pro/Max subscription). An OAuth token placed in `ANTHROPIC_API_KEY` is rejected with 401.
  Beta flags like `USE_1M_CONTEXT` are a no-op under OAuth auth.
- Optional: `COACH_LANG`, `MORNING_TIME` (07:00), `EVENING_TIME` (22:00), `MODEL`
  (`claude-sonnet-4-6`). Voice (only when built with `VOICE=true`): `WHISPER_MODEL`, etc.

## Releases

`__version__` in `coachd/__init__.py` mirrors `version` in `pyproject.toml`. Pushing a
git tag `vX.Y.Z` triggers `.github/workflows/release.yml` → multi-arch (amd64+arm64)
lean + voice images to GHCR and Docker Hub. Image tags drop the `v` (`v0.1.1` → `:0.1.1`).
Voice images run a per-arch decode gate on `tests/fixtures/voice.oga` before push.

See `CONTRIBUTING.md` (architecture + contribution scope) and `SECURITY.md` (what's in/out
of scope, private reporting) before non-trivial changes.
