"""
Xavfsizlik testlari.

Tekshiriladi:
- Hardcoded credentials yo'qligi
- pjsip.conf ENV var ishlatishi
- SSRF himoyasi
- API key bo'sh bo'lganda kirish bloklanishi
- Audio path injection himoyasi
- .env fayllar git'da yo'qligi
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"

# Ruxsat etilgan "test/namuna" qiymatlar (real secret emas)
_ALLOWED_VALUES = {
    "nonbor-secret-key",       # default placeholder
    "your-secret-here",
    "your-api-secret-key-here",
    "your-bot-token-here",
    "autodialer123",           # local dev AMI password
    "test-",                   # test prefix
    "<KUCHLI_AMI_PAROL>",
    "<SIP_LOGIN>",
    "<SIP_PAROL>",
    "KAMIDA_32_BELGILI",
}


# ── Credentials ───────────────────────────────────────────────────────────────

def test_pjsip_uses_env_vars():
    """pjsip.conf barcha maxfiy qiymatlar ENV var orqali bo'lishi kerak."""
    pjsip = (ROOT / "config/asterisk/pjsip.conf").read_text(encoding="utf-8")

    assert "username=${ENV(" in pjsip, \
        "pjsip.conf: username hardcoded — ENV(SIP_USERNAME) kerak"
    assert "password=${ENV(" in pjsip, \
        "pjsip.conf: password hardcoded — ENV(SIP_PASSWORD) kerak"
    assert "contact_user=${ENV(" in pjsip, \
        "pjsip.conf: contact_user hardcoded — ENV(SIP_USERNAME) kerak"
    assert "from_user=${ENV(" in pjsip, \
        "pjsip.conf: from_user hardcoded — ENV(SIP_USERNAME) kerak"


def test_no_hardcoded_passwords_in_config():
    """Config fayllarida hardcoded parollar bo'lmasligi kerak."""
    # .local.conf fayllar gitignored va local dev uchun — tekshirilmaydi
    conf_files = [f for f in (ROOT / "config").rglob("*.conf") if ".local." not in f.name]
    pattern = re.compile(r'^password\s*=\s*(?!\$\{ENV\()([a-zA-Z0-9]{6,})\s*$', re.MULTILINE)

    for f in conf_files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        matches = pattern.findall(content)
        # Ruxsat etilgan qiymatlarni olib tashlash
        real = [m for m in matches if not any(a in m for a in _ALLOWED_VALUES)]
        assert not real, \
            f"{f.name}: Hardcoded password topildi — ENV var ishlatish kerak: {real}"


def test_no_real_secrets_in_python_files():
    """Python fayllarida haqiqiy secretlar hardcoded bo'lmasligi kerak."""
    skip_files = {"test_security.py", "conftest.py"}
    # Shubhali pattern: password = "uzun_narsa" (8+ belgi, harf+raqam)
    pattern = re.compile(
        r'(?:password|secret|token|key)\s*=\s*["\'](?!test[-_]|your[-_]|<|KUCHLI)([a-zA-Z0-9!@#$%]{8,})["\']',
        re.IGNORECASE,
    )
    errors = []
    for f in SRC.rglob("*.py"):
        if f.name in skip_files:
            continue
        content = f.read_text(encoding="utf-8", errors="ignore")
        for m in pattern.finditer(content):
            val = m.group(1)
            if not any(a.lower() in val.lower() for a in _ALLOWED_VALUES):
                line = content[:m.start()].count("\n") + 1
                errors.append(f"{f.name}:{line} — {m.group()[:60]}")
    assert not errors, "Hardcoded credentials:\n" + "\n".join(errors)


def test_env_files_not_committed():
    """Haqiqiy .env fayllar git'da saqlanmasligi kerak."""
    import subprocess
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=str(ROOT)
    )
    tracked = result.stdout.splitlines()
    dangerous = [f for f in tracked if re.match(r'^\.env\.(production|secret|prod)$', f)]
    assert not dangerous, f"Maxfiy .env fayllar git'da: {dangerous}"


# ── SSRF himoyasi ─────────────────────────────────────────────────────────────

def test_ssrf_blocks_localhost():
    """_is_safe_url() localhost va 127.x ni bloklashi kerak."""
    from services.webhook_service import _is_safe_url
    assert not _is_safe_url("http://localhost/hook"),    "localhost bloklanmadi"
    assert not _is_safe_url("http://127.0.0.1/hook"),   "127.0.0.1 bloklanmadi"
    assert not _is_safe_url("http://0.0.0.0/hook"),     "0.0.0.0 bloklanmadi"


def test_ssrf_blocks_private_networks():
    """_is_safe_url() private network'larni bloklashi kerak."""
    from services.webhook_service import _is_safe_url
    assert not _is_safe_url("http://192.168.1.1/hook"),    "192.168.x bloklanmadi"
    assert not _is_safe_url("http://10.0.0.1/hook"),       "10.x.x bloklanmadi"
    assert not _is_safe_url("http://169.254.169.254/meta"), "link-local bloklanmadi"


def test_ssrf_allows_public_urls():
    """_is_safe_url() public URL larni o'tkazishi kerak."""
    from services.webhook_service import _is_safe_url
    assert _is_safe_url("https://prod.nonbor.uz/hook"), "Public URL bloklanib qoldi"


# ── API xavfsizligi ──────────────────────────────────────────────────────────

def test_api_key_check_uses_hmac_compare():
    """api_server.py constant-time HMAC taqqoslash ishlatishi kerak."""
    api_src = (SRC / "api_server.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in api_src, \
        "api_server.py timing attack himoyasiz — hmac.compare_digest kerak"


def test_api_rejects_empty_key():
    """Bo'sh API_SECRET_KEY barcha so'rovlarni rad etishi kerak."""
    api_src = (SRC / "api_server.py").read_text(encoding="utf-8")
    assert "key_missing" in api_src or "not self.api_key" in api_src, \
        "Bo'sh API key holatida auth bypass mumkin"


def test_config_endpoint_no_full_secret():
    """GET /config da to'liq NONBOR_SECRET qaytarilmasligi kerak."""
    api_src = (SRC / "api_server.py").read_text(encoding="utf-8")
    # "nonbor_secret": os.getenv("NONBOR_SECRET") pattern bo'lmasligi kerak
    assert '"nonbor_secret": os.getenv' not in api_src, \
        "GET /config to'liq NONBOR_SECRET qaytarmoqda!"


# ── Audio injection himoyasi ──────────────────────────────────────────────────

def test_audio_path_sanitization_exists():
    """asterisk_webhook_listener.py audio_path sanitizatsiyasi bo'lishi kerak."""
    listener = ROOT / "scripts" / "asterisk_webhook_listener.py"
    if not listener.exists():
        return  # Script yo'q — o'tkazib yuboramiz
    content = listener.read_text(encoding="utf-8")
    assert "_sanitize_audio_path" in content, \
        "audio_path sanitizatsiyasi yo'q — injection xavfi bor"


def test_webhook_listener_no_default_secret():
    """asterisk_webhook_listener.py bo'sh default secret ishlatmasligi kerak."""
    listener = ROOT / "scripts" / "asterisk_webhook_listener.py"
    if not listener.exists():
        return
    content = listener.read_text(encoding="utf-8")
    # 'nonbor-secret-key' default sifatida bo'lmasligi kerak
    assert '"nonbor-secret-key"' not in content or "RuntimeError" in content, \
        "Listener ma'lum default secret ishlatmoqda — xavfsizlik xavfi"
