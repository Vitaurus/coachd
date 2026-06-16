# TODOS

Deferred work with enough context to pick up cold. Each item names what, why,
pros/cons, and what it's blocked by.

## [voice] BAKE_MODEL — air-gapped whisper model delivery
- **What:** A Docker build option that bakes the whisper model into the image at
  build time, for installs with no network at runtime.
- **Why:** The voice plan ships runtime-download (model fetched once to
  `/data/whisper` at boot). Air-gapped / no-egress self-hosters can't runtime-download.
- **Pros:** Serves the offline audience; fully deterministic runtime (no first-boot fetch).
- **Cons:** `.dockerignore` excludes `data/`, so a COPY-from-context bake fails; downloading
  during the build adds network-at-build → non-deterministic image (the exact thing the
  Dockerfile's Garmin-MCP note brags about avoiding). Needs a multi-stage build that fetches
  to a non-`data` path, and an image-size budget that doesn't exist until release-CI.
- **Depends on:** release-CI / image-publish phase (roadmap #3). Decide bake-vs-runtime
  with the image budget + publish pipeline known.
- **Surfaced by:** plan-eng-review 2026-06-16, Tension 3 (outside voice).

## [voice] Decode smoke → automated CI job
- **What:** Run the model-free `decode_audio(BytesIO(ogg))` OGG/Opus check on the BUILT
  image in CI, on each target arch.
- **Why:** faster-whisper's `av` wheels bundle ffmpeg, so no system ffmpeg is needed — but
  that's arch-specific (an arm64 wheel gap or a source build silently pulls system ffmpeg).
  A manual smoke rots; the decode path is load-bearing for all of voice.
- **Pros:** Continuously guards the decode/transport assumption; catches arm64 regressions
  before a user files "voice does nothing."
- **Cons:** Needs an image-build workflow (`ci.yml` only runs pytest today).
- **Depends on:** release-CI / image-build workflow (roadmap #3). The voice plan ships the
  smoke as an operator/built-image check now; this TODO automates it.
- **Surfaced by:** plan-eng-review 2026-06-16, outside voice (feasibility).
