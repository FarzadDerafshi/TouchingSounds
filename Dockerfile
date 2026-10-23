FROM python:3.11-slim

WORKDIR /app

# ffmpeg backs /relay, which re-streams HTTPS/HLS stations as plain-HTTP MP3
# (the speaker has expired TLS root certs and no HLS support).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Set by ProdRelease.bat (git rev-parse --short HEAD); shown at /api/version
# and in the UI footer, so it's clear which build is running.
ARG GIT_COMMIT=dev
ENV GIT_COMMIT=$GIT_COMMIT

EXPOSE 8000

# app.server wraps uvicorn so the "ETag" header keeps its casing; the
# speaker ignores a lowercase "etag" (see app/server.py).
CMD ["python", "-m", "app.server"]
