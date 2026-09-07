"""Receive-path DAVE decrypt + Opus decode.

discord-ext-voice-recv on PyPI only strips the RTP transport layer. Discord voice
is also end-to-end encrypted (DAVE), so those frames are still MLS ciphertext and
Opus raises 'corrupted stream' — which kills the receive thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import discord

log = logging.getLogger("shadowmarket.voice")

try:
    from discord.ext import voice_recv
except ImportError:  # pragma: no cover
    voice_recv = None


def dave_decrypt_opus(voice_client: Any, user_id: int, payload: bytes) -> bytes:
    """Decrypt a DAVE frame if a session is ready; otherwise return payload as-is."""
    if not payload:
        return payload
    try:
        import davey
    except ImportError:
        return payload
    state = getattr(voice_client, "_connection", None)
    session = getattr(state, "dave_session", None)
    if session is None or not getattr(session, "ready", False):
        return payload
    if getattr(state, "dave_protocol_version", 0) == 0:
        return payload
    try:
        out = session.decrypt(int(user_id), davey.MediaType.audio, bytes(payload))
        return bytes(out) if out else payload
    except Exception:
        return payload


@dataclass
class _PcmPacket:
    pcm: bytes


if voice_recv is not None:

    class DavePcmSink(voice_recv.AudioSink):
        """Ask for Opus so the library skips decode, then DAVE-decrypt and decode ourselves."""

        def __init__(self, callback: Callable[[Any, _PcmPacket], None]) -> None:
            super().__init__()
            self._callback = callback
            self._decoders: dict[int, discord.opus.Decoder] = {}
            self._decoded_once = False

        def wants_opus(self) -> bool:
            return True

        def write(self, user, data) -> None:  # noqa: ANN001
            if user is None:
                return
            packet = getattr(data, "packet", None)
            payload = getattr(packet, "decrypted_data", None) or getattr(data, "opus", None)
            if not payload:
                return
            payload = dave_decrypt_opus(self.voice_client, user.id, bytes(payload))
            decoder = self._decoders.setdefault(user.id, discord.opus.Decoder())
            try:
                pcm = decoder.decode(payload, fec=False)
            except Exception:
                return
            if not self._decoded_once:
                self._decoded_once = True
                log.info("Decoded first VC audio frame from user %s", user.id)
            self._callback(user, _PcmPacket(pcm))

        def cleanup(self) -> None:
            self._decoders.clear()

else:  # pragma: no cover

    class DavePcmSink:  # type: ignore[no-redef]
        def __init__(self, callback: Callable) -> None:
            raise RuntimeError("discord-ext-voice-recv is not installed")
