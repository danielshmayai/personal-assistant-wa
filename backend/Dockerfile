# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# BuildKit cache mount keeps downloaded wheels between builds.
# Core deps first — this layer is cached when requirements.txt doesn't change.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# faster-whisper (+ ctranslate2 ~150 MB) is kept separate so the layer above
# stays cached even if the STT dep is updated. Not included in requirements.txt
# to keep CI pip install fast.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "faster-whisper>=1.0.0"

COPY app/ ./app/
COPY scripts/ ./scripts/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
