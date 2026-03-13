#!/bin/bash
# =============================================================================
# AUTODIALER PRO - DEPLOY SKRIPTI
# Ishlatish: ssh server "cd /opt/autodialer-pro && ./deploy.sh"
# =============================================================================

set -e

echo "========================================="
echo "  AUTODIALER PRO - YANGILASH"
echo "========================================="

cd /opt/autodialer-pro

# 1. Yangi kodni tortish
echo ""
echo "[1/4] Git pull..."
git stash -q 2>/dev/null || true
git pull origin master
git stash pop -q 2>/dev/null || true

# 2. Docker image qayta build
echo ""
echo "[2/4] Docker image build..."
docker compose build --no-cache autodialer

# 3. Konteynerlarni qayta ishga tushirish
echo ""
echo "[3/4] Konteynerlarni qayta ishga tushirish..."
docker compose up -d

# 4. Status tekshirish
echo ""
echo "[4/4] Status tekshirish..."
sleep 3
docker compose ps
echo ""
echo "Loglarni ko'rish: docker compose logs -f autodialer"
echo "========================================="
echo "  YANGILASH TUGADI!"
echo "========================================="
