#!/bin/bash
set -e

# Audio cache papkasini yaratish va ruxsatlarni sozlash
# Asterisk konteyner ham o'qiy olishi uchun 777
mkdir -p /app/audio/cache
chmod -R 777 /app/audio/cache 2>/dev/null || true

# API server fon rejimda
python src/api_server.py &

# Asosiy autodialer (foreground)
exec python src/autodialer.py
