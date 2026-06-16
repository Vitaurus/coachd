"""faster-whisper STT adapter — exercised with a FAKE model factory, so these
run without faster-whisper installed and never download a model."""

from __future__ import annotations

import io

import pytest

from coachd.adapters.faster_whisper_stt import FasterWhisperTranscriber
from coachd.ports.stt import TranscriptionError


class _Seg:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    """Records transcribe() args; returns canned segments (or raises)."""

    def __init__(self, segments, *, raises: Exception | None = None) -> None:
        self._segments = segments
        self._raises = raises
        self.calls: list[dict] = []

    def transcribe(self, audio, *, language=None, beam_size=None):
        self.calls.append({"audio": audio, "language": language, "beam_size": beam_size})
        if self._raises is not None:
            raise self._raises
        return (iter(self._segments), {"language": language})


def _factory_for(model):
    """A model_factory recording the build args and returning ``model``."""
    built: dict = {}

    def factory(model_size, compute_type, download_root):
        built.update(model_size=model_size, compute_type=compute_type, download_root=download_root)
        return model

    factory.built = built  # type: ignore[attr-defined]
    return factory


def test_transcribe_joins_and_strips_segments():
    model = _FakeModel([_Seg(" Привіт"), _Seg(" світ ")])
    tr = FasterWhisperTranscriber(model_size="small", model_factory=_factory_for(model))
    assert tr.transcribe(b"oggbytes", language="uk") == "Привіт світ"


def test_audio_wrapped_in_bytesio_and_language_beam_passed():
    model = _FakeModel([_Seg("hi")])
    tr = FasterWhisperTranscriber(
        model_size="small", beam_size=5, model_factory=_factory_for(model)
    )
    tr.transcribe(b"AUDIO", language="en")
    call = model.calls[0]
    assert call["language"] == "en"
    assert call["beam_size"] == 5
    assert isinstance(call["audio"], io.BytesIO)
    assert call["audio"].read() == b"AUDIO"  # raw bytes handed via a file-like, no temp file


def test_empty_segments_returns_empty_string():
    model = _FakeModel([])
    tr = FasterWhisperTranscriber(model_size="small", model_factory=_factory_for(model))
    assert tr.transcribe(b"x", language="uk") == ""


def test_model_error_becomes_transcription_error():
    model = _FakeModel([], raises=RuntimeError("ct2 boom"))
    tr = FasterWhisperTranscriber(model_size="small", model_factory=_factory_for(model))
    with pytest.raises(TranscriptionError):
        tr.transcribe(b"x", language="uk")


def test_model_built_once_with_config():
    model = _FakeModel([_Seg("a")])
    factory = _factory_for(model)
    tr = FasterWhisperTranscriber(
        model_size="medium",
        compute_type="int8",
        download_root="/data/whisper",
        model_factory=factory,
    )
    assert not tr.ready
    tr.load()
    tr.load()  # idempotent — built once
    tr.transcribe(b"x", language="uk")
    assert tr.ready
    assert factory.built == {  # type: ignore[attr-defined]
        "model_size": "medium",
        "compute_type": "int8",
        "download_root": "/data/whisper",
    }
