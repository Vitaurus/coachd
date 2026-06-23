# coachd

Open-source, self-hosted AI health coach over your **Garmin Connect** data, in
**Telegram**. You run your own instance with your own Garmin login and your own
Anthropic API key. Your data, your keys, your machine.

> **Status: early, but the loop works end-to-end.** Greenfield rewrite. Today the
> full loop runs: scheduled reports, chat over your live Garmin data (text,
> photos, opt-in voice), and confirmation-first workout creation. It rides
> unofficial Garmin access (see disclaimers), so treat it as beta. Watch the repo.

## The loop
- **Reports** — a morning/evening verdict pushed to Telegram, computed from your
  Garmin metrics (HRV, recovery, training load, sleep).
- **Chat** — ask "why am I tired today?" and get an answer over your live data.
  Send a **photo** (a meal, a Garmin screenshot, a training plan) any time. **Voice
  notes** are opt-in (they add ~320MB to the image): build with `VOICE=true` and
  they're transcribed on your box by local whisper — no audio leaves the host. See
  *Voice notes* below.
- **Action** — create and upload workouts to your watch (always confirmation-first).

## Requirements
- Docker + Docker Compose
- A Garmin Connect account
- Anthropic access — either a pay-as-you-go API key from
  [console.anthropic.com](https://console.anthropic.com), or a Claude Pro/Max
  subscription (run `claude setup-token` to mint a token)

## Quick start (no clone — just Docker)
The fastest path: create two files, fill in your secrets, run. No `git clone`, no
build — this pulls the prebuilt image.

**1. `docker-compose.yml`** — copy as-is:
```yaml
services:
  coachd:
    image: vitaurus/coachd:latest      # text-only; use :latest-voice for voice notes
    env_file: .env
    volumes:
      - ./data:/data                   # Garmin tokens + coach state live here — keep out of git
    restart: unless-stopped
```

**2. `.env`** — create it next to the compose file and fill in:
```bash
TG_BOT_TOKEN=                  # from @BotFather
TG_CHAT_ID=                    # leave blank for now — you fill it in step 4
ANTHROPIC_API_KEY=             # sk-ant-api… from console.anthropic.com …
# CLAUDE_CODE_OAUTH_TOKEN=     # …OR a Claude Pro/Max token; set EXACTLY one of the two
USER_NAME=                     # your name, used in the coach's prompts
WORN_START=2026-06-08          # first day you wore the watch (ISO date)
TZ=Europe/Kyiv                 # your timezone — REQUIRED for correct timing
# COACH_LANG=uk                # optional: coach language (en default / uk)
```

**3. Log in to Garmin** — one-time, interactive (email, password, and an MFA code):
```bash
docker compose run --rm coachd login
```

**4. Get your Telegram chat id** — message your bot once, then:
```bash
docker compose run --rm coachd chat-id
```
Paste the printed `TG_CHAT_ID=…` line into `.env`.

**5. Start it:**
```bash
docker compose up -d
```

Scheduled reports begin and the bot answers chat + photos. Want **voice notes**?
Change the image to `vitaurus/coachd:latest-voice` and re-run step 5. Each step
(MFA, households, token refresh, all env vars) is explained in detail below.

## Setup (clone & build from source)
Cloning the repo gives you the committed `docker-compose.yml` (which builds the
image locally) and `.env.example` to copy. The steps below also document every
command in more depth than the Quick start above.

### 1. Log in to Garmin (one-time, interactive)
Garmin's first login needs your email, password, and an MFA code, so it cannot
run unattended. Run it once; it writes OAuth tokens to a mounted volume:

```bash
docker compose run --rm coachd login
```

You'll be asked for your Garmin email, password (hidden), and — if Garmin demands
it — an MFA code from your email/SMS. Your password is never stored; only the
resulting tokens are saved. When a token later expires, the coach tells you in
Telegram to re-run this command.

Check token state anytime:
```bash
docker compose run --rm coachd token-status   # prints: valid | missing | expired | unreachable
```

### 2. Get your Telegram chat id
Create a bot with [@BotFather](https://t.me/BotFather), put its token in `.env`
as `TG_BOT_TOKEN`, then send the bot any message. Run this **before**
`docker compose up` — the running coach is the only allowed `getUpdates`
consumer, so discovering while it's up returns a 409:

```bash
docker compose run --rm coachd chat-id
```

It prints each chat id that messaged the bot plus the exact line to paste:
`TG_CHAT_ID=<id>` (comma-joined for a household). If you haven't filled
`TG_BOT_TOKEN` into `.env` yet, pass it inline:
```bash
docker compose run --rm -e TG_BOT_TOKEN=<token> coachd chat-id
```

### 3. Start the coach
Fill in `.env` (copy from `.env.example`): `TG_BOT_TOKEN`, `TG_CHAT_ID`,
your Anthropic credential (`ANTHROPIC_API_KEY` **or** `CLAUDE_CODE_OAUTH_TOKEN`
from `claude setup-token` — set one, not both), `USER_NAME`, `WORN_START`, `TZ`.
The coach speaks English by default; set `COACH_LANG=uk` for Ukrainian (if you
ran an earlier Ukrainian-only build, set this or it switches to English).
Then:
```bash
docker compose up -d
```
This runs the timezone-aware report scheduler (morning/evening verdicts pushed to
Telegram; times via `MORNING_TIME`/`EVENING_TIME`, default 07:00/22:00). When a
token expires you get a Telegram nudge to re-run `login`.

Beyond the scheduled reports, the bot answers chat at any time — ask a question,
send a photo, or (on a voice image) a voice note — and creates workouts on your
watch, always confirmation-first. The `claude` CLI that the Agent SDK drives is
bundled in the image, so there's no extra setup.

## Run from a published image (instead of building)
Prefer not to build locally? Each release publishes prebuilt, multi-arch images
(`linux/amd64` + `linux/arm64`, auto-selected for your host) to **GHCR** and
**Docker Hub** — mirrors of the same image, pull from whichever you like:

```bash
# GitHub Container Registry
docker pull ghcr.io/vitaurus/coachd:latest          # text-only (lean)
docker pull ghcr.io/vitaurus/coachd:latest-voice    # with local voice/STT
# Docker Hub
docker pull vitaurus/coachd:latest                  # text-only (lean)
docker pull vitaurus/coachd:latest-voice            # with local voice/STT
```

Tags: `:latest` / `:latest-voice` track the newest release; `:X.Y.Z` (e.g.
`:0.1.0`) and the rolling `:X.Y` pin a specific version — note the image tag has
no `v` prefix even though the git release tag does. The `-voice` images bundle
faster-whisper (see *Voice notes* below); the bare images are text-only.

To run from a published image **without cloning**, use the **Quick start** above —
it gives a self-contained `docker-compose.yml` that pulls instead of builds. If
you already cloned, switch the committed compose to a published image by
commenting out its `build:` block and setting `image:` to one of the tags above.

## Configuration
All configuration is via environment variables (put them in `.env`). The `login`
and `token-status` commands need only `GARMINTOKENS` + `TZ`; the running coach
(`serve`) needs every **Required** variable below.

### Required (for `serve`)
| Variable | Default | What it does |
|---|---|---|
| `TG_BOT_TOKEN` | — | Telegram bot token from [@BotFather](https://t.me/BotFather). |
| `TG_CHAT_ID` | — | Owner chat id(s) the bot answers — comma-separated for a household (`111,222`). This is the **only** trust boundary; `docker compose run --rm coachd chat-id` prints it. |
| `ANTHROPIC_API_KEY` **or** `CLAUDE_CODE_OAUTH_TOKEN` | — | Anthropic credential — set **exactly one**. An API key (`sk-ant-api…`) from console.anthropic.com, **or** a Claude Pro/Max token from `claude setup-token` (`sk-ant-oat…`). An OAuth token placed in `ANTHROPIC_API_KEY` is rejected with 401. |
| `USER_NAME` | — | Your name, used in the coach's prompts. |
| `WORN_START` | — | First day you wore the watch (ISO `YYYY-MM-DD`) — anchors "day N" and the baseline math. |
| `TZ` | `Europe/Kyiv` * | Your timezone (IANA, e.g. `Europe/Kyiv`). Required — a wrong TZ skews report timing and night/recovery attribution. |

\* `.env.example` pre-fills `Europe/Kyiv`, but `TZ` has no real default — it must be set.

### Garmin tokenstore
| Variable | Default | What it does |
|---|---|---|
| `GARMINTOKENS` | `/data/garmin` | Directory for Garmin OAuth tokens (a mounted volume). Leave as-is unless you remap the volume. |

### Optional — coach behavior
| Variable | Default | What it does |
|---|---|---|
| `COACH_LANG` | `en` | Output language the coach speaks: `en` or `uk`. The prompt corpus is English internally; this changes the output and the bot's chrome only. |
| `MORNING_TIME` | `07:00` | Morning report time (`HH:MM`, 24h, in your `TZ`). |
| `EVENING_TIME` | `22:00` | Evening report time (`HH:MM`, 24h, in your `TZ`). |
| `MODEL` | `claude-sonnet-4-6` | Coaching model. Set `claude-opus-4-8` for max quality (you pay for your own key). |
| `USE_1M_CONTEXT` | `false` | Enable the 1M-context beta. Only affects **API-key** auth; ignored under a Claude subscription (OAuth) token. |

### Optional — voice notes (local STT)
Voice is **opt-in at build time** to keep the default image lean. `VOICE` is a
**build-arg**, not a runtime toggle — changing it requires a rebuild. The knobs
below take effect only on a voice image.

| Variable | Default | What it does |
|---|---|---|
| `VOICE` (build-arg) | `false` | Build-time: `true` installs faster-whisper (~320MB) **and** bakes the runtime default on. Set it in `.env` (or `VOICE=true docker compose build`) and **rebuild**. |
| `VOICE_ENABLED` | follows `VOICE` † | Runtime toggle. Run a voice-capable image with voice **off** by setting `false` — no rebuild needed. |
| `WHISPER_MODEL` | `small` | Whisper model size. `small` is the fail-safe; `medium` (~1.5GB RAM, slower) is noticeably better for Ukrainian/accented speech. |
| `WHISPER_COMPUTE` | `int8` | Quantization. `int8` = lean CPU choice; `int8_float32`/`float32` trade RAM/CPU for a little accuracy. Valid: `int8, int8_float16, int8_float32, int16, float16, float32`. |
| `MAX_VOICE_SECONDS` | `300` | Reject voice notes longer than this many seconds. |
| `STT_DOWNLOAD_ROOT` | `/data/whisper` | Where the whisper model is cached (a persisted volume → downloaded once, survives restarts). |

† The image bakes `VOICE_ENABLED` to match the `VOICE` build-arg: a lean image
defaults it **off**, a voice image **on**. `VOICE_ENABLED` overrides that at runtime.

## Voice notes (optional)
Voice transcription runs **on your box** (local whisper, no API, no audio leaves
the host) — but it's **off by default** because the faster-whisper stack adds
~320MB to the image. The default build is text-only (~1.16GB; ~1.48GB with voice).

To enable it, set `VOICE=true` in `.env` and **rebuild** (it's a build-time flag,
not just a runtime toggle):
```bash
VOICE=true docker compose build && docker compose up -d
```
The whisper model downloads once at first boot to `/data/whisper` (persisted), so
the first voice note after a fresh start may lag while it fetches. For better
Ukrainian/accented accuracy set `WHISPER_MODEL=medium` (more RAM, slower). On a
text-only image, voice notes get a "type instead" reply; everything else works.

## ⚠️ Disclaimers (read these)
- **Not a medical device.** Estimates are approximate, no guarantees. Do not use
  for any life-critical decision.
- **Unofficial Garmin access.** This reads Garmin Connect through an unofficial
  client (the same one the community MCP uses). It can break when Garmin changes
  their site, and it is a gray area under Garmin's consumer terms. You self-host
  it for your own account, at your own risk.
- **Your data goes to Anthropic.** The coach sends your metrics to Anthropic's
  API (via your key) to generate insights. That is the trust boundary; it's your
  data and your key, but be aware of it.
- **Bring your own secrets.** Garmin tokens, your Anthropic key, and your Telegram
  credentials live only in your `.env` / mounted volumes. Never commit them.

## Contributing
Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup,
the hexagonal architecture, and the invariants to respect (owner-gate, write-guard
confirmation-first, English-only source). Found a security issue? Report it
privately per [SECURITY.md](SECURITY.md), not in a public issue.

## License
MIT. See [LICENSE](LICENSE).
