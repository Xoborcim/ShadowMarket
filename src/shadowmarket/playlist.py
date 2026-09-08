"""Download and pick tracks from a YouTube playlist for VC clips."""

from __future__ import annotations

import logging
import random
import subprocess
from pathlib import Path

from shadowmarket import config

log = logging.getLogger("shadowmarket.playlist")

AUDIO_SUFFIXES = {".opus", ".ogg", ".mp3", ".m4a", ".webm", ".wav", ".flac"}


def playlist_dir() -> Path:
    path = config.BACK_JAM_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_tracks(directory: Path | None = None) -> list[Path]:
    root = directory or playlist_dir()
    if not root.is_dir():
        return []
    tracks = [
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and not p.name.startswith(".")
    ]
    return sorted(tracks)


def pick_track(directory: Path | None = None) -> Path | None:
    tracks = list_tracks(directory)
    if not tracks:
        return None
    return random.choice(tracks)


def download_playlist(
    url: str | None = None,
    directory: Path | None = None,
) -> int:
    """Download/update playlist audio with yt-dlp. Returns number of audio files present."""
    root = directory or playlist_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = url or config.BACK_JAM_PLAYLIST_URL
    if not target:
        log.warning("No playlist URL configured; skipping download")
        return len(list_tracks(root))

    archive = root / ".yt-dlp-archive.txt"
    outtmpl = str(root / "%(playlist_index)03d-%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-progress",
        "--ignore-errors",
        "--download-archive",
        str(archive),
        "-x",
        "--audio-format",
        "opus",
        "--audio-quality",
        "0",
        "-o",
        outtmpl,
        "--yes-playlist",
        target,
    ]
    log.info("Downloading playlist into %s", root)
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=60 * 60,
        )
    except FileNotFoundError:
        log.error("yt-dlp is not installed; cannot download playlist")
        return len(list_tracks(root))
    except subprocess.TimeoutExpired:
        log.error("Playlist download timed out")
        return len(list_tracks(root))

    if result.returncode != 0:
        # Partial downloads still usable; log stderr for debugging.
        err = (result.stderr or result.stdout or "").strip()
        log.warning("yt-dlp exited %s: %s", result.returncode, err[-500:])

    count = len(list_tracks(root))
    log.info("Playlist ready: %s audio file(s) in %s", count, root)
    return count
