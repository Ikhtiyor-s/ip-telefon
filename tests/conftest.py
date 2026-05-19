"""
Pytest konfiguratsiyasi — barcha testlar uchun umumiy sozlamalar.
src/ papkasini path'ga qo'shadi va minimal env o'rnatadi.
"""
import os
import sys
from pathlib import Path

# src/ ni import path'ga qo'shish
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Test uchun minimal env (haqiqiy secrets kerak emas)
os.environ.setdefault("NONBOR_BASE_URL", "https://test.nonbor.uz/api/v2")
os.environ.setdefault("NONBOR_SECRET", "test-secret-key-for-ci-only")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")
os.environ.setdefault("AMI_HOST", "127.0.0.1")
os.environ.setdefault("AMI_PORT", "5038")
os.environ.setdefault("AMI_USERNAME", "autodialer")
os.environ.setdefault("AMI_PASSWORD", "test-ami-password")
os.environ.setdefault("API_SECRET_KEY", "test-api-key-minimum-32-characters-long")
os.environ.setdefault("EXTERNAL_API_SECRET", "test-external-secret-32-chars-long")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret-32-chars-long")
os.environ.setdefault("ASTERISK_SOUNDS_PATH", "/tmp/test-audio")
os.environ.setdefault("ASTERISK_PLAYBACK_PATH", "/tmp/test-audio/cache")
os.environ.setdefault("SIP_USERNAME", "test-sip-user")
os.environ.setdefault("SIP_PASSWORD", "test-sip-password")
