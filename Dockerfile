# Minimal image for the #0 bootstrap. The MCP pre-bake (architecture decision A1)
# and the runtime deps for the coach loop are added when `serve` lands.
FROM python:3.12-slim

ENV GARMINTOKENS=/data/garmin \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY coachd ./coachd
RUN pip install --no-cache-dir .

# `docker compose run --rm coachd login`  → python -m coachd login
# `docker compose up`                      → python -m coachd serve (placeholder)
ENTRYPOINT ["python", "-m", "coachd"]
CMD ["serve"]
