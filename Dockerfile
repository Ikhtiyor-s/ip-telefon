# =============================================================================
# AUTODIALER PRO - DOCKERFILE
# =============================================================================

FROM python:3.11-slim

LABEL maintainer="WellTech"
LABEL description="Autodialer Pro - Professional autodialer tizimi"

# Environment
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Tashkent

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user yaratish (xavfsizlik)
RUN useradd -m -u 1000 -s /bin/bash appuser

# Application code
COPY src/ ./src/
COPY config/ ./config/
COPY docker-entrypoint.sh /docker-entrypoint.sh

# Create directories va ruxsatlar
# audio/cache papkasiga 777 - Asterisk konteyner ham o'qiy olishi uchun (shared volume)
RUN mkdir -p audio/cache logs data && \
    chmod -R 777 audio && \
    chown -R appuser:appuser /app && \
    sed -i 's/\r//' /docker-entrypoint.sh && \
    chmod +x /docker-entrypoint.sh

# Non-root user bilan ishlash
USER appuser

# Healthcheck - API server port 8585
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8585/api/autodialer/health')" || exit 1

CMD ["/docker-entrypoint.sh"]
