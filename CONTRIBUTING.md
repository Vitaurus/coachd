# Contributing to coachd

Thanks for considering a contribution. coachd is a **privacy-first, self-hosted**
tool — each user runs their own instance with their own keys and data. That ethos
shapes what fits: features that keep data on the user's box are welcome; anything
that phones home, adds telemetry, or weakens a security boundary is not.

## Scope — what fits

- ✅ Bug fixes, reliability, clearer errors, docs.
- ✅ New Garmin metrics / coaching signals, new report content, better prompts.
- ✅ New languages (the coach is internationalized — see *i18n* below).
- ✅ Performance and image-size wins.
- ⚠️ New external integrations: fine, but they must respect the trust boundaries
  documented in the README disclaimers (data only goes where the user's keys
  send it). Open an issue first to discuss.
- ❌ Telemetry, analytics, crash phone-home, or anything that sends user data to a
  third party the user didn't configure. This is a hard no.

If a change is large or changes behavior, **open an issue first** so we can agree
on the approach before you write code.

## Development setup

coachd uses [uv](https://docs.astral.sh/uv/) and targets **Python 3.12+**.

```bash
uv sync --extra dev          # install deps + dev tools (uv fetches Python 3.12 itself)
uv run --extra dev python -m pytest -q     # run the test suite
```

Optional voice/STT stack (faster-whisper) lives behind an extra:

```bash
uv sync --extra dev --extra voice          # if you're touching voice code
```

`uv.lock` is committed and CI runs `--frozen`; if you change dependencies, run
`uv lock` and commit the updated lockfile.

## Architecture — read this before adding code

coachd is **hexagonal**. The dependency rule is one-way: the core depends on
abstractions (Protocols), never on SDKs.

- `coachd/core/` — domain logic: the coach engine, chat, prompts, journal,
  recovery math, resilience. Pure-ish; depends only on `coachd/ports/`.
- `coachd/ports/` — the `Protocol` interfaces (`datasource`, `llm`, `stt`). Core
  talks to these.
- `coachd/adapters/` — the concrete SDK-backed implementations (Anthropic agent,
  Garmin provider + MCP client, Telegram bot, faster-whisper). **SDK imports live
  here, never in core.**
- `coachd/security/` — the two invariants (see below).
- `coachd/auth/` — credential/token handling.
- `coachd/app.py` — the composition root that wires adapters into core.

When you add a capability: put the SDK call in an adapter, express what core needs
as a port Protocol, and wire it in `app.py`. Don't import an SDK into `core/`.

## Invariants — do not weaken these

These are enforced by tests; a PR that breaks them will be rejected.

- **Owner-gate is the only Telegram trust boundary.** The bot answers and acts
  **only** for chat ids in `TG_CHAT_ID` (`coachd/security/authenticator.py`).
  Never add a path that bypasses it.
- **Write-guard is confirmation-first.** Any state-changing action (e.g. creating
  or uploading a workout) must be parked for explicit user confirmation, never
  executed directly (`coachd/security/write_guard.py`,
  `tests/test_write_guard.py`).
- **Source is English-only.** All source strings are English; user-facing text
  goes through the i18n catalog, never inline. A guard test scans `coachd/**.py`
  and fails on Cyrillic in source (`tests/test_no_cyrillic_source.py`). Tests are
  exempt.

## i18n

User-facing strings live in the catalog at `coachd/core/i18n.py` as
`key -> {lang -> text}`, selected by `COACH_LANG` (`en` default, `uk`). To add a
string: add a key with **every** supported language (a placeholder-parity test in
`tests/test_i18n.py` enforces matching `{placeholders}` across languages). To add
a language: add its translations for every existing key. Don't hard-code
user-facing text in adapters or core.

## Tests

- The suite must stay green: `uv run --extra dev python -m pytest -q`.
- Add tests with your change — bug fixes get a regression test, features get
  coverage. The codebase is test-first; match that.
- The voice decode smoke (`tests/test_stt_decode_smoke.py`) skips unless the
  `voice` extra is installed — that's expected in a default dev env.
- If you touch a GitHub Actions workflow, lint it:
  `docker run --rm -v "$PWD":/repo --workdir /repo rhysd/actionlint:latest`.

## Commits & pull requests

- Branch off `main`; open the PR against `main`. CI (tests) must pass.
- Keep PRs focused — one logical change. Split unrelated work.
- Commit messages: `type: short imperative subject` (e.g. `fix: …`, `feat: …`,
  `docs: …`, `test: …`, `ci: …`), with a body explaining the *why* when it isn't
  obvious. Match the surrounding code's style and comment density.
- **Never commit secrets.** `.env`, the `/data` volume, Garmin tokens, and the
  HANDOFF doc are gitignored — keep it that way. Don't paste real tokens into
  tests or docs (use obvious placeholders).

## Reporting bugs vs security issues

- **Bugs / feature ideas:** open a GitHub issue.
- **Security vulnerabilities:** do **not** open a public issue or PR — follow
  [SECURITY.md](SECURITY.md) (private reporting via GitHub's Security tab).

## License

By contributing, you agree your contributions are licensed under the project's
[MIT License](LICENSE).
