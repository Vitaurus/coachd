"""Decode smoke — prove OGG/Opus decodes WITHOUT system ffmpeg, no model needed.

This guards the load-bearing transport assumption of voice: faster-whisper's
``av`` wheel bundles ffmpeg, so Telegram voice notes (OGG/Opus) decode to PCM with
no system ffmpeg installed. It is SEPARATE from STT accuracy (which needs a real
whisper model) — here we only assert the bytes demux+decode to samples.

faster-whisper is the ``voice`` optional extra, NOT a dev dependency, so this
skips in dev/CI (where it isn't installed) and runs on the BUILT image (which
installs ``.[voice]``). That is the operator/built-image gate the voice plan
ships now; automating it on the image in CI is a deferred TODO (release-CI phase).

Fixture: ``tests/fixtures/voice.oga`` is a synthetic 1s 440Hz tone encoded to
OGG/Opus with the SAME PyAV wheel the runtime uses — so it exercises the exact
demux+decode codepath as a real voice note (accuracy isn't tested here, decode is).
Regenerate with ``uv run --with av --with numpy`` (see the project's git history).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

# voice extra absent (dev/CI) → skip; present (built image) → run the real decode.
pytest.importorskip("faster_whisper", reason="voice extra not installed (built-image gate)")

from faster_whisper.audio import decode_audio  # noqa: E402 — after importorskip

_FIXTURE = Path(__file__).parent / "fixtures" / "voice.oga"


def test_ogg_opus_decodes_to_pcm_without_system_ffmpeg():
    data = _FIXTURE.read_bytes()
    assert data[:4] == b"OggS"  # the fixture really is an Ogg container

    pcm = decode_audio(io.BytesIO(data))  # model-free: bundled av demux+decode+resample

    assert len(pcm) > 0                       # decoded to samples, not an empty buffer
    assert str(pcm.dtype) == "float32"        # whisper's expected PCM dtype
    # ~1s resampled to whisper's 16kHz mono (allow slack for encoder priming/padding)
    assert 15000 <= len(pcm) <= 17000
    assert any(abs(float(x)) > 0.01 for x in pcm[:2000])  # real signal, not all-zero
