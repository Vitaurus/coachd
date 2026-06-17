# Security Policy

coachd is **self-hosted**: you run your own instance, with your own Garmin login,
your own Anthropic credential, and your own Telegram bot, on your own machine.
There is no shared service and no central server that holds anyone's data. This
shapes everything below — most of the security surface is in *your* deployment,
and the threat model is "an attacker who can reach your bot or your box," not "a
multi-tenant breach."

## Supported versions

coachd is pre-1.0 and moves fast. Security fixes land on the **latest release
only**; there are no backports. Pull the newest image (or rebuild from `main`)
to stay current.

| Version | Supported |
| ------- | --------- |
| latest `0.1.x` | ✅ |
| older tags | ❌ (upgrade) |

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Public issues are visible
to everyone and tip off attackers before a fix exists.

Report privately through **GitHub Private Vulnerability Reporting**:
the repo's **Security** tab → **Report a vulnerability**. This opens a private
advisory visible only to you and the maintainer.

Please include:
- what the vulnerability is and the impact (what an attacker gains),
- steps to reproduce or a proof of concept,
- the version / image tag you are running (the published tag, e.g. `0.1.0`, or
  the git SHA you built from),
- any suggested fix.

**Response targets** (best-effort, single maintainer, early-stage project):
- acknowledgement within **7 days**,
- an assessment (accepted / needs-info / out-of-scope) within **14 days**,
- a fix or mitigation plan for accepted reports as soon as practical.

Please give a reasonable window for a fix before any public disclosure
(coordinated disclosure). Credit is gladly given in the advisory unless you
prefer to stay anonymous.

## What's in scope

Vulnerabilities in coachd's own code and packaging, for example:
- **Owner-gate bypass** — getting the bot to answer or act for a Telegram chat id
  that is not in `TG_CHAT_ID` (the owner-gate is the *only* Telegram trust
  boundary; see `coachd/security/authenticator.py`).
- **Write-guard bypass** — causing a state-changing action (e.g. creating or
  uploading a workout) to execute **without** the explicit confirmation step
  (`coachd/security/write_guard.py`). Confirmation-first is a core invariant.
- **Secret leakage** — Garmin OAuth tokens, the Anthropic key/OAuth token, or the
  Telegram bot token being written to logs, baked into the image, or otherwise
  exposed beyond `.env` / the mounted `/data` volume.
- **Injection / RCE** via crafted Telegram input (text, photo, or voice note), the
  bundled `claude` CLI, or the pinned Garmin MCP subprocess.
- **Container issues** — anything that lets the container reach host resources it
  shouldn't, or that ships a known-vulnerable dependency we can update.

## What's out of scope

These are documented trade-offs of a self-hosted tool, not vulnerabilities:
- **The Anthropic trust boundary.** coachd sends your Garmin metrics to Anthropic's
  API (via your own key) to generate insights. That is the intended design and is
  called out in the README disclaimers.
- **Unofficial Garmin access.** coachd reads Garmin Connect through an unofficial
  client. It can break when Garmin changes their site and is a gray area under
  Garmin's consumer terms. Operate it for your own account, at your own risk.
- **Operator misconfiguration.** Committing your own `.env`, putting an attacker's
  chat id in `TG_CHAT_ID`, exposing your Docker host, or leaking your own tokens.
- **Denial of service from your own usage**, and rate limits from Garmin / Telegram
  / Anthropic.
- **Vulnerabilities in upstream dependencies** that already have an upstream
  advisory — report those upstream. A heads-up so we can bump the pin is welcome.

## Operator hardening checklist

You own most of the security surface. Minimum hygiene:
- Keep `.env` and the `/data` volume **out of version control** (they hold your
  tokens). They are gitignored and dockerignored by default — keep it that way.
- Put **only** your own (and trusted household) chat ids in `TG_CHAT_ID`.
- Use a **scoped** Anthropic credential and rotate it if it may have leaked; the
  same for the Telegram bot token (`@BotFather` → revoke/regenerate).
- Run the **latest** image; re-`login` when Garmin tokens expire rather than
  working around it.
- Don't expose the container to untrusted networks — it only needs outbound
  access to Telegram, Garmin, and Anthropic.
