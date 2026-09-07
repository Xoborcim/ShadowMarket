"""PCM helpers and per-speaker utterance detection for VC transcription."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

DISCORD_RATE = 48_000
WHISPER_RATE = 16_000
STEREO = 2
SILENCE_RMS = 220.0
MIN_SPEECH_SECONDS = 0.7
MAX_SPEECH_SECONDS = 10.0
SILENCE_SECONDS = 0.45


def pcm48_stereo_to_float16k_mono(pcm: bytes) -> np.ndarray:
    """Discord voice packets are 48 kHz signed-16 stereo. Whisper wants 16 kHz float mono."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return np.zeros(0, dtype=np.float32)
    if samples.size % STEREO == 0:
        mono = samples.reshape(-1, STEREO).astype(np.float32).mean(axis=1)
    else:
        mono = samples.astype(np.float32)
    # 48k -> 16k is an exact 3:1 decimation
    mono = mono[:: DISCORD_RATE // WHISPER_RATE]
    return np.clip(mono / 32768.0, -1.0, 1.0).astype(np.float32)


def rms_int16_stereo(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


@dataclass
class SpeakerBuffer:
    chunks: list[bytes] = field(default_factory=list)
    speech_packets: int = 0
    silence_packets: int = 0

    def push(self, pcm: bytes, *, packet_seconds: float = 0.02) -> bytes | None:
        loud = rms_int16_stereo(pcm) >= SILENCE_RMS
        if loud:
            self.chunks.append(pcm)
            self.speech_packets += 1
            self.silence_packets = 0
        elif self.chunks:
            self.chunks.append(pcm)
            self.silence_packets += 1

        speech = self.speech_packets * packet_seconds
        silence = self.silence_packets * packet_seconds
        if speech >= MAX_SPEECH_SECONDS or (
            speech >= MIN_SPEECH_SECONDS and silence >= SILENCE_SECONDS
        ):
            blob = b"".join(self.chunks)
            self.chunks.clear()
            self.speech_packets = 0
            self.silence_packets = 0
            return blob
        if not loud and not self.chunks:
            return None
        return None


class UtteranceAssembler:
    def __init__(self) -> None:
        self._speakers: dict[int, SpeakerBuffer] = {}

    def push(self, user_id: int, pcm: bytes) -> bytes | None:
        buf = self._speakers.setdefault(user_id, SpeakerBuffer())
        return buf.push(pcm)

    def drop(self, user_id: int) -> None:
        self._speakers.pop(user_id, None)

    def clear(self) -> None:
        self._speakers.clear()


def busiest_channel_id(channels: list[tuple[int, int]]) -> int | None:
    """channels: list of (channel_id, human_count)."""
    best_id = None
    best_n = 0
    for channel_id, count in channels:
        if count > best_n:
            best_id = channel_id
            best_n = count
    return best_id if best_n > 0 else None


def should_switch_channel(
    current_id: int | None,
    current_humans: int,
    candidate_id: int | None,
    candidate_humans: int,
    *,
    margin: int = 2,
) -> bool:
    if candidate_id is None or candidate_humans <= 0:
        return False
    if current_id is None or current_humans <= 0:
        return True
    if candidate_id == current_id:
        return False
    return candidate_humans >= current_humans + margin
