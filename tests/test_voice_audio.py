import numpy as np

from shadowmarket.voice_audio import (
    SpeakerBuffer,
    busiest_channel_id,
    pcm48_stereo_to_float16k_mono,
    should_switch_channel,
)


def _packet(level: int) -> bytes:
    return np.full(960 * 2, level, dtype=np.int16).tobytes()


def test_pcm_downsamples_48k_stereo_to_16k_mono():
    pcm = _packet(1000)
    audio = pcm48_stereo_to_float16k_mono(pcm)
    assert audio.dtype == np.float32
    assert len(audio) == 320  # 20ms at 16 kHz


def test_busiest_channel():
    assert busiest_channel_id([(1, 2), (2, 5), (3, 0)]) == 2
    assert busiest_channel_id([(1, 0), (2, 0)]) is None


def test_switch_uses_margin_unless_empty():
    assert should_switch_channel(1, 3, 2, 4, margin=2) is False
    assert should_switch_channel(1, 3, 2, 5, margin=2) is True
    assert should_switch_channel(None, 0, 2, 1, margin=2) is True
    assert should_switch_channel(1, 0, 2, 1, margin=2) is True


def test_utterance_flushes_after_speech_and_silence():
    buf = SpeakerBuffer()
    flushed = None
    for _ in range(40):
        flushed = buf.push(_packet(4000))
        assert flushed is None
    for _ in range(30):
        flushed = buf.push(_packet(0))
        if flushed:
            break
    assert flushed is not None
    assert len(flushed) > 0
