# coachd

Open-source, **self-hosted** AI health coach over your **Garmin Connect** data,
in **Telegram**. Your data, your keys, your machine — nothing runs on anyone
else's server.

Morning/evening readiness verdicts, "why am I tired today?" answered over your
live metrics (text + photos + opt-in voice notes), and confirmation-first
workouts pushed to your watch.

## Tags

| Tag | What |
|-----|------|
| `latest`, `X.Y.Z`, `X.Y` | text-only (lean, ~1.16 GB) |
| `latest-voice`, `X.Y.Z-voice` | + local voice/STT (faster-whisper, on-box, no audio leaves the host; ~1.48 GB) |

Multi-arch: `linux/amd64` + `linux/arm64`, auto-selected. The image tag has **no
`v` prefix** even though the git release tag does (`v0.1.0` → `:0.1.0`).

## Quick start (no clone)

Create two files, fill in your secrets, run.

**`docker-compose.yml`:**

```yaml
services:
  coachd:
    image: vitaurus/coachd:latest      # :latest-voice for voice notes
    env_file: .env
    volumes:
      - ./data:/data                   # Garmin tokens + state — keep out of git
    restart: unless-stopped
```

**`.env`** (next to the compose file):

```bash
TG_BOT_TOKEN=                  # from @BotFather
TG_CHAT_ID=                    # fill in after step 2
ANTHROPIC_API_KEY=             # sk-ant-… from console.anthropic.com
# CLAUDE_CODE_OAUTH_TOKEN=     # …OR a Claude Pro/Max token; set EXACTLY one of the two
USER_NAME=                     # your name, used in the coach's prompts
WORN_START=2026-06-08          # first day you wore the watch (ISO date)
TZ=Europe/Kyiv                 # your timezone — REQUIRED for correct timing
# COACH_LANG=uk                # optional: coach language (en default / uk)
```

Then:

```bash
docker compose run --rm coachd login      # 1. one-time Garmin login (email, password, MFA)
docker compose run --rm coachd chat-id    # 2. message your bot once, then paste TG_CHAT_ID into .env
docker compose up -d                        # 3. go — scheduled reports + chat begin
```

Full docs — every config variable, the build-from-source path, and a Portainer
(named-volume) deployment — plus the code:
**https://github.com/Vitaurus/coachd**

## ⚠️ Disclaimers

- **Not a medical device** — estimates only, never for a life-critical decision.
- **Unofficial Garmin access** — reads Garmin Connect via an unofficial client; a
  gray area under Garmin's terms. Self-host for your own account, at your own risk.
- **Your data goes to Anthropic** — your metrics are sent to Anthropic's API (via
  your key) to generate insights. That's the trust boundary.

MIT licensed.
