#!/bin/bash
set -e

# API server fon rejimda
python src/api_server.py &

# Asosiy autodialer (foreground)
exec python src/autodialer.py
