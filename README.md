# coachd

Open-source, self-hosted AI health coach over your **Garmin Connect** data, in
**Telegram**. You run your own instance with your own Garmin login and your own
Anthropic API key. Your data, your keys, your machine.

> **Status: early.** This is a greenfield rewrite in progress. Today the Garmin
> login bootstrap (below) works. The coach loop (reports + chat + workout
> creation) is being built. Watch the repo.

## The loop (what it will do)
- **Reports** — a morning/evening verdict pushed to Telegram, computed from your
  Garmin metrics (HRV, recovery, training load, sleep).
- **Chat** — ask "why am I tired today?" and get an answer over your live data.
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
Then:
```bash
docker compose up -d
```
This runs the timezone-aware report scheduler (morning/evening verdicts pushed to
Telegram; times via `MORNING_TIME`/`EVENING_TIME`, default 07:30/22:15). When a
token expires you get a Telegram nudge to re-run `login`.

Status: scheduled reports work. The interactive chat (ask questions, create
workouts) is landing next. Report generation needs the bundled `claude` CLI in
the image — see the Docker notes if you build it yourself.

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
