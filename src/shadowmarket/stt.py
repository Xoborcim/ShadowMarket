"""Local Whisper transcription. Optional: disable with VOICE_LISTEN=false."""

from __future__ import annotations

import logging

from shadowmarket.config import WHISPER_MODEL
from shadowmarket.voice_audio import pcm48_stereo_to_float16k_mono

log = logging.getLogger("shadowmarket.stt")

_model = None
_model_failed = False


def transcribe_pcm48(pcm: bytes) -> str:
    audio = pcm48_stereo_to_float16k_mono(pcm)
    if audio.size < 1600:  # < 0.1s at 16 kHz
        return ""
    model = _get_model()
    if model is None:
        return ""
    segments, _info = model.transcribe(
        audio,
        language="en",
        vad_filter=True,
        beam_size=1,
        without_timestamps=True,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if len(text) < 3:
        return ""
    return text


def _get_model():
    global _model, _model_failed
    if _model_failed:
        return None
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel

        log.info("Loading Whisper model %s (first run may download it)", WHISPER_MODEL)
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model
    except Exception:
        log.exception("Whisper is unavailable; voice word tracking disabled")
        _model_failed = True
        return None
