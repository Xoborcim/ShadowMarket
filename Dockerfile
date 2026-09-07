FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DATABASE_PATH=/data/shadowmarket.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libopus0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "-m", "shadowmarket"]
