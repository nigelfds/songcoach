# Linux image that mirrors the Heroku runtime (Python 3.11 + ffmpeg, CPU torch).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# ffmpeg is required by both yt-dlp and the mp3 transcode step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so the layer caches unless requirements change.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code (overlaid by a bind mount in dev via docker-compose).
COPY . .

EXPOSE 8000

# Default = web. docker-compose overrides the command for the worker service.
CMD ["gunicorn", "songcoach.main:app", "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
