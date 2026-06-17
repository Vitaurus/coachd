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
- **Depends on:** ~~release-CI / image-publish phase~~ — NOW EXISTS
  (`release.yml`, 2026-06-17). Unblocked, but DEFERRED: still a niche (air-gapped)
  audience and a heavy cost (+~480MB small / +~1.5GB medium per voice image), and
  it forks the publish matrix on a new axis (baked vs download × arch × model-size).
  Revisit on real air-gapped demand; pick a model-size to bake then.
- **Decision:** plan-eng-review 2026-06-17, D5 = defer.
- **Surfaced by:** plan-eng-review 2026-06-16, Tension 3 (outside voice).

## [voice] Decode smoke → automated CI job — ✅ DONE (2026-06-17)
- **Shipped in:** `.github/workflows/release.yml` (`publish-voice` job). Before any
  voice tag is pushed, each arch is built single-platform with `--load`, then a
  model-free decode runs against the freshly-built image:
  `docker run --platform linux/<arch> -v tests/fixtures:/fix:ro <img> python -c
  "decode_audio('/fix/voice.oga') …"`. A decode regression fails the job before
  the manifest is published, on BOTH amd64 and arm64 (arm64 under QEMU).
- **Why it lives there, not in `ci.yml`:** the check needs the voice image (av +
  faster_whisper present), which only exists at publish time. `ci.yml` stays
  tests-only.
- **Resolved by:** plan-eng-review 2026-06-17, D4 (decode-gate ON, per-arch).
