# Minimal image for the #0 bootstrap. The MCP pre-bake (architecture decision A1)
# and the runtime deps for the coach loop are added when `serve` lands.
FROM python:3.12-slim

# UTF-8 everywhere: the slim base defaults to ASCII, so a non-ASCII (e.g.
# Cyrillic) Garmin password read from stdin would surrogate-escape and crash the
# HTTP layer ("surrogates not allowed"). Force UTF-8 for stdin and all I/O.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    GARMINTOKENS=/data/garmin \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY coachd ./coachd
RUN pip install --no-cache-dir .

# `docker compose run --rm coachd login`  → python -m coachd login
# `docker compose up`                      → python -m coachd serve (placeholder)
ENTRYPOINT ["python", "-m", "coachd"]
CMD ["serve"]
