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

## Setup

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
Telegram; times via `MORNING_TIME`/`EVENING_TIME`, default 07:30/22:15). When a
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

Tags: `:latest` / `:latest-voice` track the newest release; `:vX.Y.Z` (and the
rolling `:vX.Y`) pin a specific version. The `-voice` images bundle faster-whisper
(see *Voice notes* below); the bare images are text-only.

To use a published image with Compose, comment out the `build:` block in
`docker-compose.yml` and point `image:` at the pulled tag:
```yaml
  coachd:
    # build: …            # ← comment out to use the published image
    image: ghcr.io/vitaurus/coachd:latest   # or :latest-voice for voice
```
Then `docker compose up -d` pulls instead of building. The `login` / `chat-id` /
`token-status` one-time commands work the same against the published image.

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

## License
MIT. See [LICENSE](LICENSE).
