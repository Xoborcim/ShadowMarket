# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DATABASE_PATH=/data/shadowmarket.db \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Keep apt light (full Debian ffmpeg pulls mesa/llvm and often fails with mirror hash mismatches).
# Static ffmpeg is enough for discord.py playback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --fix-missing \
        ca-certificates curl xz-utils libgomp1 libopus0 \
    && curl -fsSL --retry 5 --retry-all-errors -o /tmp/ffmpeg.tar.xz \
        https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp \
    && install -m 755 /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ffmpeg \
    && install -m 755 /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg* /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 15 --timeout 120 -r requirements.txt

COPY src ./src

RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "-m", "shadowmarket"]
