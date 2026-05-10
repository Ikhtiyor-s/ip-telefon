"""
Asterisk Webhook Listener
==========================
Asterisk serverida ishga tushadi (172.29.124.85:5000).

Autodialer → POST /notify → bu server → asterisk -rx → admin qo'ng'irog'i

Ishga tushirish:
    python3 asterisk_webhook_listener.py

Systemd (production):
    cp asterisk_webhook_listener.py /opt/autodialer/
    systemctl enable asterisk-webhook
    systemctl start asterisk-webhook

Muhit o'zgaruvchilari:
    WEBHOOK_SECRET   — autodialer bilan bir xil bo'lishi kerak (default: nonbor-secret-key)
    ALERT_AUDIO      — Asterisk Playback uchun audio yo'li (extension'siz)
    SIP_ENDPOINT     — PJSIP endpoint nomi (default: sarkor-endpoint)
    WEBHOOK_PORT     — tinglash porti (default: 5000)
"""

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("asterisk_webhook")

# Bot X-Telegram-Bot-Secret header yuboradi → EXTERNAL_API_SECRET bilan bir xil
_raw_secret = os.getenv("EXTERNAL_API_SECRET", os.getenv("WEBHOOK_SECRET", ""))
if not _raw_secret:
    raise RuntimeError(
        "EXTERNAL_API_SECRET yoki WEBHOOK_SECRET env var o'rnatilmagan! "
        "Xavfsizlik uchun ishga tushish to'xtatildi."
    )
WEBHOOK_SECRET = _raw_secret
ALERT_AUDIO    = os.getenv("ALERT_AUDIO", "/tmp/autodialer/api_alert")
SIP_ENDPOINT   = os.getenv("SIP_ENDPOINT", "sarkor-endpoint")
WEBHOOK_PORT   = int(os.getenv("WEBHOOK_PORT", "5000"))
CALLER_ID      = os.getenv("CALLER_ID", "+998783331002")


def _sanitize_audio_path(path: str) -> str:
    """audio_path ni tozalash — faqat xavfsiz belgilar.
    Asterisk CLI injection himoyasi: bo'shliq, newline, maxsus belgilar olib tashlanadi.
    Faqat harf, raqam, /, _, -, . ruxsat.
    """
    sanitized = re.sub(r"[^\w/._\-]", "", path)
    # Path traversal oldini olish: ../  bo'lmasin
    sanitized = re.sub(r"\.\.+", "", sanitized)
    return sanitized


def _sanitize_phone(raw: str) -> str:
    """Faqat raqamlar qoldirish — command injection himoyasi."""
    digits = re.sub(r"[^\d]", "", raw)
    # O'zbekiston: 998XXXXXXXXX (12 raqam) yoki 9XXXXXXXX (9 raqam)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return digits
    return ""  # noto'g'ri format — ishlatilmaydi


def _check_audio(path: str) -> bool:
    """Audio fayl mavjud va bo'sh emasligini tekshirish.
    Asterisk extension'siz path ishlatadi → .wav qo'shib tekshirish.
    """
    wav = path if path.endswith(".wav") else path + ".wav"
    import os as _os
    if not _os.path.exists(wav):
        logger.error(f"Audio fayl topilmadi: {wav} — qo'ng'iroq BEKOR")
        return False
    if _os.path.getsize(wav) == 0:
        logger.error(f"Audio fayl bo'sh (0 bayt): {wav} — qo'ng'iroq BEKOR")
        return False
    logger.info(f"Audio tekshirildi: {wav} ({_os.path.getsize(wav)} bayt)")
    return True


def _originate(phone: str, audio_path: str) -> bool:
    """Asterisk CLI orqali qo'ng'iroq boshlash.
    audio_path mavjud bo'lmasa qo'ng'iroq qilinmaydi.
    """
    clean = _sanitize_phone(phone)
    if not clean:
        logger.warning(f"Noto'g'ri telefon raqami: {phone!r} — o'tkazib yuborildi")
        return False

    # Audio path sanitizatsiyasi — injection himoyasi
    safe_path = _sanitize_audio_path(audio_path)
    if safe_path != audio_path:
        logger.warning(f"audio_path sanitized: {audio_path!r} → {safe_path!r}")

    # Audio tekshirish — yo'q bo'lsa qo'ng'iroq YO'Q
    if not _check_audio(safe_path):
        return False

    # Asterisk CLI buyrug'i — barcha qismlar sanitized
    cmd_inner = (
        f"channel originate "
        f"PJSIP/{clean}@{SIP_ENDPOINT} "
        f"application Playback {safe_path}"
    )
    try:
        result = subprocess.run(
            ["asterisk", "-rx", cmd_inner],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info(f"Qo'ng'iroq boshlandi: {clean} | audio: {audio_path}")
            return True
        logger.error(f"Asterisk xatosi [{clean}]: {result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"Asterisk CLI timeout: {clean}")
        return False
    except FileNotFoundError:
        logger.error("'asterisk' buyrug'i topilmadi — Asterisk o'rnatilganmi?")
        return False


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # ── Auth ──────────────────────────────────────────────────────────────
        # Bot X-Telegram-Bot-Secret header yuboradi
        secret = (
            self.headers.get("X-Telegram-Bot-Secret", "")
            or self.headers.get("X-Webhook-Secret", "")
        )
        if not hmac.compare_digest(secret, WEBHOOK_SECRET):
            self._respond(401, {"error": "unauthorized"})
            logger.warning(f"Ruxsatsiz so'rov: {self.client_address[0]}")
            return

        # ── Body parse ────────────────────────────────────────────────────────
        try:
            length = int(self.headers.get("Content-Length", 0))
            body: dict = json.loads(self.rfile.read(length))
        except Exception as e:
            self._respond(400, {"error": f"bad json: {e}"})
            return

        event = body.get("event", "")
        logger.info(f"Event keldi: {event} | {self.client_address[0]}")

        # ── Eventlarni qayta ishlash ──────────────────────────────────────────
        if event == "api_down":
            # Bot: admin_phone (string) yoki admin_phones (array)
            raw = body.get("admin_phones") or body.get("admin_phone", "")
            if isinstance(raw, list):
                phones: List[str] = raw
            elif isinstance(raw, str) and raw:
                phones = [raw]
            else:
                phones = []

            # Audio path: autodialer yuborsa ishlatiladi, aks holda ALERT_AUDIO
            audio_path = body.get("audio_path", "").strip() or ALERT_AUDIO
            reason = body.get("reason", "")

            logger.warning(
                f"API down — {reason} | "
                f"audio: {audio_path} | "
                f"{len(phones)} ta adminga qo'ng'iroq"
            )

            # Audio mavjudligini tekshir — yo'q bo'lsa hech kim chaqirilmaydi
            if not _check_audio(audio_path):
                self._respond(503, {
                    "status": "error",
                    "reason": f"audio topilmadi: {audio_path}.wav",
                    "called": 0,
                })
                return

            called = 0
            for phone in phones:
                if _originate(phone, audio_path):
                    called += 1

            self._respond(200, {"status": "ok", "called": called, "total": len(phones)})

        else:
            # Boshqa eventlar — kelajak uchun
            logger.debug(f"Noma'lum event e'tiborsiz: {event}")
            self._respond(200, {"status": "ignored", "event": event})

    def do_GET(self):
        """Health check."""
        if self.path == "/health":
            self._respond(200, {"status": "ok", "service": "asterisk-webhook"})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Standart HTTPServer loglarini o'chiramiz, o'zimiznikini ishlatamiz
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), WebhookHandler)
    logger.info(f"Asterisk webhook listener: 0.0.0.0:{WEBHOOK_PORT}")
    logger.info(f"Audio: {ALERT_AUDIO} | Endpoint: {SIP_ENDPOINT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("To'xtatildi")
        server.shutdown()
