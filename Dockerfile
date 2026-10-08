# VIDEO & MUSIC DOWNLOADS — Render Docker image
# Everything that is needed (FFmpeg, Deno, Python packages) is installed HERE, at build time.
# Nothing is installed when the bot starts.

FROM python:3.12-slim-bookworm

# Pinned Deno release used by yt-dlp for YouTube. Change only after testing.
ARG DENO_VERSION=v2.1.4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=10000

# FFmpeg (ffmpeg + ffprobe), CA certificates, and the tools needed to fetch Deno at build time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
 && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s ${DENO_VERSION} \
 && apt-get purge -y --auto-remove curl unzip \
 && rm -rf /var/lib/apt/lists/* \
 && deno --version \
 && ffmpeg -version | head -n 1

WORKDIR /app

# Python dependencies: build stage only.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt \
 && yt-dlp --version

# Application code. cookies.txt, .env, databases and downloads are excluded by .dockerignore.
COPY . .

# Non-root runtime user with a writable temp area.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /tmp/vmd-downloads \
 && chown -R appuser:appuser /app /tmp/vmd-downloads

USER appuser

EXPOSE 10000

CMD ["python", "bot.py"]
