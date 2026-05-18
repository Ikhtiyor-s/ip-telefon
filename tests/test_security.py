"""
Xavfsizlik testlari — credentials va xavfsiz kod tekshiruvi.
Bu testlar CI/CD va pre-push hook'da ishga tushadi.
"""
import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC  = ROOT / "src"
CFG  = ROOT / "config"


# ─── Yordamchi ───────────────────────────────────────────────────────────────

def _all_py_files():
    return list(SRC.rglob("*.py")) + list(ROOT.glob("scripts/*.py"))

def _all_config_files():
    return list(CFG.rglob("*.conf"))


# ─── Sintaksis testlari ──────────────────────────────────────────────────────

def test_python_syntax():
    """Barcha Python fayllar sintaktik jihatdan to'g'ri bo'lishi kerak."""
    errors = []
    for f in _all_py_files():
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{f.relative_to(ROOT)}: {e}")
    assert not errors, "Sintaksis xatolari:\n" + "\n".join(errors)


# ─── Xavfsizlik testlari ─────────────────────────────────────────────────────

# Qiymatlari git'da bo'lmasligi kerak bo'lgan kalit so'zlar
_SECRET_PATTERNS = [
    (r'password\s*=\s*["\'](?!(\$\{ENV|your-|<|KUCHLI|autodialer123))[^"\']{4,}["\']', "Hardcoded password"),
    (r'secret\s*=\s*["\'](?!(\$\{ENV|your-|<|nonbor-secret-key|autodialer123))[^"\']{6,}["\']', "Hardcoded secret"),
    (r'\bpassword\s*=\s*(?!\$\{ENV)[a-zA-Z0-9]{6,}\b', "Hardcoded password (no quotes)"),
]


def test_no_hardcoded_credentials_in_config():
    """Config fayllarida hardcoded parollar bo'lmasligi kerak."""
    errors = []
    for f in _all_config_files():
        content = f.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in _SECRET_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                # pjsip.conf da ENV var ishlatilishi kerak
                if "ENV(" not in content and "password" in content.lower():
                    errors.append(f"{f.relative_to(ROOT)}: {label}")
    assert not errors, "Hardcoded credentials:\n" + "\n".join(errors)


def test_pjsip_uses_env_vars():
    """pjsip.conf barcha maxfiy qiymatlar uchun ENV var ishlatishi kerak."""
    pjsip = CFG / "asterisk" / "pjsip.conf"
    if not pjsip.exists():
        return
    content = pjsip.read_text(encoding="utf-8")
    # username va password ENV var'dan olinishi kerak
    assert "username=${ENV(" in content, "pjsip.conf: username ENV var ishlatmayapti"
    assert "password=${ENV(" in content, "pjsip.conf: password ENV var ishlatmayapti"
    assert "contact_user=${ENV(" in content, "pjsip.conf: contact_user ENV var ishlatmayapti"


def test_no_hardcoded_credentials_in_python():
    """Python kodida hardcoded parollar bo'lmasligi kerak."""
    _SKIP_FILES = {"test_security.py"}
    _SKIP_PATTERNS = [
        r'password\s*=\s*["\'][a-zA-Z0-9!@#$%^&*]{8,}["\']',
        r'secret\s*=\s*["\'][a-zA-Z0-9!@#$%^&*]{8,}["\']',
    ]
    # Ruxsat etilgan qiymatlar (test/default)
    _ALLOWED = {
        "autodialer123", "your-api-secret-key-here", "nonbor-secret-key",
        "your-secret-here", "your-bot-token-here",
    }
    errors = []
    for f in _all_py_files():
        if f.name in _SKIP_FILES:
            continue
        content = f.read_text(encoding="utf-8", errors="ignore")
        for pattern in _SKIP_PATTERNS:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                val = re.search(r'["\']([^"\']+)["\']', m.group())
                if val and val.group(1) not in _ALLOWED:
                    errors.append(f"{f.relative_to(ROOT)}:{content[:m.start()].count(chr(10))+1}: {m.group()[:60]}")
    assert not errors, "Python da hardcoded credentials:\n" + "\n".join(errors)


def test_webhook_listener_no_default_secret():
    """asterisk_webhook_listener.py bo'sh default secret ishlatmasligi kerak."""
    listener = ROOT / "scripts" / "asterisk_webhook_listener.py"
    if not listener.exists():
        return
    content = listener.read_text(encoding="utf-8")
    assert '"nonbor-secret-key"' not in content, \
        "asterisk_webhook_listener.py: hardcoded default secret topildi!"
    assert "RuntimeError" in content, \
        "asterisk_webhook_listener.py: bo'sh secret da xato berishi kerak"


def test_api_server_no_full_secret_in_response():
    """api_server.py GET /config da to'liq secret qaytarmasligi kerak."""
    api = SRC / "api_server.py"
    content = api.read_text(encoding="utf-8")
    # "***xxxx" pattern bo'lmasligi kerak — faqat true/false
    assert '"nonbor_secret":' not in content or "_set" in content, \
        "api_server.py: nonbor_secret to'liq qaytarilayapti!"


# ─── Import testlari ─────────────────────────────────────────────────────────

def test_services_importable():
    """Asosiy servislar import qilinishi kerak."""
    import sys
    sys.path.insert(0, str(SRC))
    from services.webhook_service import WebhookService, _is_safe_url
    from services.stats_service import StatsService, CallRecord, OrderRecord
    from services.asterisk_service import AsteriskAMI, CallManager, CallTracker
    from services.tts_service import TTSService


def test_ssrf_protection():
    """_is_safe_url ichki IP larni bloklashi kerak."""
    import sys
    sys.path.insert(0, str(SRC))
    from services.webhook_service import _is_safe_url

    assert not _is_safe_url("http://localhost/hook"),       "localhost bloklanmadi"
    assert not _is_safe_url("http://127.0.0.1/hook"),       "127.0.0.1 bloklanmadi"
    assert not _is_safe_url("http://192.168.1.1/hook"),     "192.168.x bloklanmadi"
    assert not _is_safe_url("http://169.254.169.254/meta"), "link-local bloklanmadi"
    assert _is_safe_url("https://prod.nonbor.uz/hook"),     "public URL bloklandi"


def test_audio_path_sanitization():
    """Webhook listener audio_path sanitizatsiyasi xavfsiz bo'lishi kerak."""
    import sys
    sys.path.insert(0, str(SRC.parent / "scripts"))
    # Script'ni import qilmasdan regex bilan tekshirish
    listener = ROOT / "scripts" / "asterisk_webhook_listener.py"
    content = listener.read_text(encoding="utf-8")
    assert "_sanitize_audio_path" in content, "sanitize funksiyasi yo'q"
    assert r"[^\w/._\-]" in content or r"[^\w" in content, "sanitize regex yo'q"


def test_call_record_backward_compat():
    """CallRecord eski JSON ma'lumotlarini o'qiy olishi kerak."""
    import sys
    sys.path.insert(0, str(SRC))
    from services.stats_service import CallRecord

    old = {
        "phone": "+998901234567", "seller_name": "Test", "order_count": 1,
        "attempts": 1, "result": "answered", "timestamp": "2024-01-01",
        "order_ids": [],
    }
    r = CallRecord.from_dict(old)
    assert r.wait_seconds == 0
    assert r.recording_url == ""


def test_daily_stats_backward_compat():
    """DailyStats eski JSON ma'lumotlarini o'qiy olishi kerak."""
    import sys
    sys.path.insert(0, str(SRC))
    from services.stats_service import DailyStats

    old = {
        "date": "2024-01-01", "total_calls": 5, "answered_calls": 3,
        "unanswered_calls": 2, "calls_1_attempt": 2, "calls_2_attempts": 1,
        "calls_3_attempts": 0, "total_orders": 3, "accepted_orders": 2,
        "rejected_orders": 1, "accepted_without_telegram": 0,
        "call_records": [], "order_records": [],
    }
    s = DailyStats.from_dict(old)
    assert s.total_calls == 5
    assert s.unanswered_1_attempt == 0  # yangi maydon, default 0


def test_ami_multiple_handlers():
    """AsteriskAMI bir event uchun bir nechta handler qabul qilishi kerak."""
    import sys
    sys.path.insert(0, str(SRC))
    from services.asterisk_service import AsteriskAMI

    ami = object.__new__(AsteriskAMI)
    ami._event_handlers = {}
    called = []

    async def h1(d): called.append(1)
    async def h2(d): called.append(2)

    ami.on_event("Hangup", h1)
    ami.on_event("Hangup", h2)
    assert len(ami._event_handlers["Hangup"]) == 2, "Multiple handlers ishlamadi"
