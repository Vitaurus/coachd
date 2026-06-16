# Runtime image for the coach. Heavier than pip-only because the Claude Agent SDK
# spawns the `claude` CLI (Node), so the CLI is bundled here.
FROM python:3.12-slim

# UTF-8 everywhere: the slim base defaults to ASCII, so a non-ASCII (e.g.
# Cyrillic) Garmin password read from stdin would surrogate-escape and crash the
# HTTP layer ("surrogates not allowed"). Force UTF-8 for stdin and all I/O.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    GARMINTOKENS=/data/garmin \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH"

# System deps: Node 20 + the claude CLI (the Agent SDK's backend), git (for the
# pinned MCP install), curl/ca-certificates/gnupg (for the NodeSource setup).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# A1: pre-bake the Garmin MCP at a PINNED commit (no runtime git pull → no
# network-SPOF on start, deterministic image). It is installed in its OWN
# isolated env via pipx, because it pins garminconnect==0.3.2 while coachd needs
# 0.3.6 — they cannot share a venv. The MCP runs as a separate stdio subprocess,
# so isolation is the correct boundary anyway. The `garmin-mcp` shim lands on
# PATH (/root/.local/bin) where GarminProvider's default command finds it.
RUN pip install --no-cache-dir pipx \
    && pipx install "git+https://github.com/Taxuspt/garmin_mcp@7af73ebf9b4073cf3b1ad1cb42d351f38e7ef47c"

# Voice (local STT) is OPT-IN: the faster-whisper stack (ctranslate2/onnxruntime/
# av) adds ~320MB, so the DEFAULT image is text-only (~1.16GB). Build with
# VOICE=true to include it (~1.48GB). When on, the whisper model is NOT baked — it
# downloads once at first boot to /data/whisper (a mounted volume → persists).
ARG VOICE=false
WORKDIR /app
COPY pyproject.toml README.md ./
COPY coachd ./coachd
RUN if [ "$VOICE" = "true" ]; then \
        pip install --no-cache-dir ".[voice]"; \
    else \
        pip install --no-cache-dir "."; \
    fi
# Default runtime voice state FOLLOWS the build: a text-only image won't try to
# load a model it doesn't have (no scary "load failed" log). .env can override
# (e.g. VOICE_ENABLED=false on a voice image to keep voice off without a rebuild).
ENV VOICE_ENABLED=$VOICE

# `docker compose run --rm coachd login` → python -m coachd login
# `docker compose up`                      → python -m coachd serve (report scheduler)
ENTRYPOINT ["python", "-m", "coachd"]
CMD ["serve"]
