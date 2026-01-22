#!/bin/bash
# ============================================================
# AutoDialer Pro - Linux Server Setup Script
# ============================================================
# Bu skript production serverda AutoDialer ni o'rnatadi
# Ishlatish: sudo bash setup_server.sh
# ============================================================

set -e

echo "============================================================"
echo "AUTODIALER PRO - LINUX SERVER O'RNATISH"
echo "============================================================"

# Root tekshirish
if [ "$EUID" -ne 0 ]; then
    echo "Xato: Bu skriptni root sifatida ishga tushiring!"
    echo "Ishlatish: sudo bash setup_server.sh"
    exit 1
fi

# 1. Kerakli papkalarni yaratish
echo ""
echo "[1/8] Papkalar yaratilmoqda..."
mkdir -p /opt/autodialer
mkdir -p /var/lib/autodialer/audio/cache
mkdir -p /usr/share/asterisk/sounds/custom
mkdir -p /var/log/autodialer

# 2. Python dependencies o'rnatish
echo ""
echo "[2/8] Python kutubxonalari o'rnatilmoqda..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip ffmpeg sox

pip3 install --quiet aiohttp python-telegram-bot edge-tts pydub python-dotenv colorlog requests

# 3. Fayllarni ko'chirish
echo ""
echo "[3/8] Fayllar ko'chirilmoqda..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$SCRIPT_DIR/autodialer_v2_linux.py" /opt/autodialer/
cp "$SCRIPT_DIR/autodialer.service" /etc/systemd/system/

# 4. Environment faylni yaratish (XAVFSIZLIK)
echo ""
echo "[4/8] Environment fayl yaratilmoqda..."
if [ ! -f /opt/autodialer/.env ]; then
    cat > /opt/autodialer/.env << 'ENVEOF'
# AutoDialer Pro - Environment Variables
# Bu faylni tahrirlang va haqiqiy qiymatlarni kiriting!

# Telegram Bot (MAJBURIY - o'zgartiring!)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_ADMIN_CHAT_ID=your_telegram_chat_id_here

# Nonbor API
NONBOR_API_URL=https://test.nonbor.uz/api/v2/telegram_bot/get-order-for-courier/
ENVEOF
    chmod 600 /opt/autodialer/.env
    echo "   .env fayl yaratildi: /opt/autodialer/.env"
    echo "   MUHIM: Telegram credentials ni tahrirlang!"
else
    echo "   .env fayl mavjud, o'tkazib yuborildi"
fi

# 5. Audio fayllarni ko'chirish (agar mavjud bo'lsa)
echo ""
echo "[5/8] Audio fayllar ko'chirilmoqda..."
if [ -d "$SCRIPT_DIR/audio/cache" ]; then
    cp -r "$SCRIPT_DIR/audio/cache/"* /var/lib/autodialer/audio/cache/ 2>/dev/null || true
    echo "   Audio fayllar ko'chirildi"
else
    echo "   Audio cache papkasi topilmadi, TTS avtomatik yaratadi"
fi

# 6. Huquqlarni sozlash
echo ""
echo "[6/8] Huquqlar sozlanmoqda..."
chown -R root:root /opt/autodialer
chown -R root:root /var/lib/autodialer
chown -R asterisk:asterisk /usr/share/asterisk/sounds/custom
chmod +x /opt/autodialer/autodialer_v2_linux.py
chmod 644 /etc/systemd/system/autodialer.service
chmod 600 /opt/autodialer/.env  # Faqat root o'qiy olsin

# 7. Asterisk dialplan sozlash
echo ""
echo "[7/8] Asterisk dialplan sozlanmoqda..."

# Agar dialplan mavjud bo'lmasa, qo'shish
if ! grep -q "autodialer-dynamic" /etc/asterisk/extensions.conf 2>/dev/null; then
    cat >> /etc/asterisk/extensions.conf << 'EOF'

; ============ AUTODIALER DIALPLAN ============
[autodialer-dynamic]
exten => _X.,1,NoOp(AutoDialer Dynamic: ${EXTEN})
 same => n,Answer()
 same => n,Wait(1)
 same => n,Playback(custom/${EXTEN})
 same => n,Wait(1)
 same => n,Hangup()

[autodialer-ivr]
exten => s,1,NoOp(AutoDialer IVR)
 same => n,Answer()
 same => n,Wait(1)
 same => n,Playback(custom/default_message)
 same => n,Wait(1)
 same => n,Hangup()
; ============================================
EOF
    echo "   Dialplan qo'shildi"
else
    echo "   Dialplan allaqachon mavjud"
fi

# Asterisk reload
asterisk -rx "dialplan reload" 2>/dev/null || true

# 8. Systemd service ishga tushirish
echo ""
echo "[8/8] Service ishga tushirilmoqda..."
systemctl daemon-reload
systemctl enable autodialer

# .env tekshirish
if grep -q "your_telegram_bot_token_here" /opt/autodialer/.env; then
    echo ""
    echo "============================================================"
    echo "OGOHLANTIRISH: Telegram credentials sozlanmagan!"
    echo "============================================================"
    echo ""
    echo "Service ishga tushmaydi. Avval quyidagilarni bajaring:"
    echo ""
    echo "1. .env faylni tahrirlang:"
    echo "   nano /opt/autodialer/.env"
    echo ""
    echo "2. Quyidagi qiymatlarni kiriting:"
    echo "   TELEGRAM_BOT_TOKEN=7683981246:AAF..."
    echo "   TELEGRAM_ADMIN_CHAT_ID=-5219407458"
    echo ""
    echo "3. Service ni ishga tushiring:"
    echo "   systemctl restart autodialer"
    echo ""
else
    systemctl restart autodialer
    echo ""
    echo "============================================================"
    echo "O'RNATISH MUVAFFAQIYATLI TUGADI!"
    echo "============================================================"
    echo ""
    echo "Service holati:"
    systemctl status autodialer --no-pager -l | head -15
fi

echo ""
echo "============================================================"
echo "FOYDALI BUYRUQLAR:"
echo "============================================================"
echo "  Credentials sozlash:  nano /opt/autodialer/.env"
echo "  Loglarni ko'rish:     journalctl -u autodialer -f"
echo "  Service to'xtatish:   systemctl stop autodialer"
echo "  Service qayta ishga:  systemctl restart autodialer"
echo "  Service holati:       systemctl status autodialer"
echo "============================================================"
echo ""
echo "Test qilish: curl http://localhost:8080/test"
echo ""
