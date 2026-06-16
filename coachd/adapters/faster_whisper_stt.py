"""faster-whisper STT adapter — local CPU transcription, lazy model load.

faster-whisper (ctranslate2) runs whisper on CPU with int8 quantization: no GPU,
no API, audio never leaves the box. The OGG/Opus bytes are decoded by
faster-whisper's bundled PyAV (the ``av`` wheel bundles ffmpeg — no system ffmpeg
needed); we hand it a ``BytesIO`` so there is no temp file (unlike the reference).

The model is loaded LAZILY — built once on the first ``transcribe`` (or an
explicit ``load()`` at boot), never at import. So unit tests inject a FAKE model
factory and never import the heavy dep, and the composition root can preload the
real model off the event loop (in a worker thread) before serving voice.

  bytes ──► BytesIO ──► WhisperModel.transcribe(language=…) ──► join segments ──► str
                              │ any failure
                              └──────────────────────► TranscriptionError (friendly line)
"""

from __future__ import annotations

import io
from typing import Callable

from ..ports.stt import TranscriptionError


def _default_model_factory(model_size: str, compute_type: str, download_root: str | None):
    """Build a real faster-whisper model. Imported lazily so this module loads
    (and unit tests run) without faster-whisper installed. ``download_root`` is
    where ctranslate2 caches the fetched model — coachd points it at a persisted
    volume path so it is downloaded once and survives restarts."""
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_size, device="cpu", compute_type=compute_type, download_root=download_root
    )


class FasterWhisperTranscriber:
    """A ``TranscriberPort`` backed by a local faster-whisper model.

    ``transcribe`` is a BLOCKING CPU call — the bot runs it via ``to_thread`` so
    the shared event loop is never stalled. The model is built once (on ``load``
    or the first ``transcribe``) and reused. ``model_factory`` is injectable so
    tests exercise ``transcribe`` with a fake model and never touch real whisper.
    """

    def __init__(
        self,
        *,
        model_size: str,
        compute_type: str = "int8",
        download_root: str | None = None,
        beam_size: int = 5,
        model_factory: Callable[..., object] | None = None,
    ) -> None:
        self._model_size = model_size
        self._compute_type = compute_type
        self._download_root = download_root
        self._beam_size = beam_size
        self._factory = model_factory or _default_model_factory
        self._model: object | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Build the model now (a blocking download+load on first run). Called
        once at boot in a worker thread; idempotent. A failure propagates so the
        composition root can log it loudly and leave voice disabled."""
        if self._model is None:
            self._model = self._factory(
                self._model_size, self._compute_type, self._download_root
            )

    def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """Decode ``audio`` (OGG/Opus bytes) and return the transcript.

        Raises ``TranscriptionError`` on any decode/inference failure so the bot
        surfaces one friendly line instead of a traceback. An empty/garbled clip
        returns ``""`` (the bot turns that into a "didn't catch that" nudge)."""
        try:
            self.load()
            segments, _info = self._model.transcribe(  # type: ignore[union-attr]
                io.BytesIO(audio), language=language, beam_size=self._beam_size
            )
            return "".join(seg.text for seg in segments).strip()
        except Exception as exc:  # noqa: BLE001 — any whisper/decode failure → one friendly line
            raise TranscriptionError(str(exc)) from exc
