"""Local Whisper transcription. Optional: disable with VOICE_LISTEN=false."""

from __future__ import annotations

import logging

from shadowmarket.config import WHISPER_HINT_MAX_CHARS, WHISPER_HINTS, WHISPER_MODEL
from shadowmarket.voice_audio import pcm48_stereo_to_float16k_mono

log = logging.getLogger("shadowmarket.stt")

_model = None
_model_failed = False


def whisper_hint_text(
    hints: list[str] | None = None,
    *,
    extras: list[str] | None = None,
    max_chars: int = WHISPER_HINT_MAX_CHARS,
) -> str | None:
    """Comma-separated vocabulary for Whisper, capped so it fits the prompt window."""
    seen: list[str] = []
    for raw in list(extras if extras is not None else WHISPER_HINTS) + list(hints or []):
        term = " ".join(str(raw).split()).strip().lower()
        if term and term not in seen:
            seen.append(term)
    if not seen:
        return None
    parts: list[str] = []
    used = 0
    for term in seen:
        extra = len(term) + (2 if parts else 0)
        if used + extra > max_chars:
            continue
        parts.append(term)
        used += extra
    return ", ".join(parts) if parts else None


def transcribe_pcm48(pcm: bytes, hints: list[str] | None = None) -> str:
    audio = pcm48_stereo_to_float16k_mono(pcm)
    if audio.size < 1600:  # < 0.1s at 16 kHz
        return ""
    model = _get_model()
    if model is None:
        return ""
    kwargs: dict = {
        "language": "en",
        "vad_filter": True,
        "beam_size": 1,
        "without_timestamps": True,
        "condition_on_previous_text": False,
    }
    hint = whisper_hint_text(hints)
    if hint:
        kwargs["hotwords"] = hint
    try:
        segments, _info = model.transcribe(audio, **kwargs)
    except TypeError:
        kwargs.pop("hotwords", None)
        segments, _info = model.transcribe(audio, **kwargs)
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
