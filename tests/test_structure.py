"""
Loyiha strukturasi testlari.

Maqsad: muhim fayllar yo'qolmasin, papka tuzilishi buzilmasin.
Har qanday o'zgarishda struktura saqlanishi kafolatlanadi.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"


# ── Majburiy fayllar ──────────────────────────────────────────────────────────

REQUIRED_FILES = [
    # Asosiy manba
    "src/autodialer.py",
    "src/api_server.py",
    "src/services/__init__.py",
    "src/services/asterisk_service.py",
    "src/services/nonbor_service.py",
    "src/services/telegram_service.py",
    "src/services/tts_service.py",
    "src/services/stats_service.py",
    "src/services/webhook_service.py",
    "src/services/admin_call_service.py",
    # Konfiguratsiya
    "config/asterisk/extensions.conf",
    "config/asterisk/pjsip.conf",
    "config/asterisk/manager.conf",
    "config/asterisk/manager.local.conf",
    # Docker
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.local.yml",
    "docker-entrypoint.sh",
    # Muhit
    ".env.example",
    "requirements.txt",
]

REQUIRED_DIRS = [
    "src",
    "src/services",
    "config/asterisk",
    "tests",
    "scripts",
]


def test_required_files_exist():
    """Barcha muhim fayllar mavjud bo'lishi kerak."""
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    assert not missing, f"Majburiy fayllar yo'q:\n" + "\n".join(missing)


def test_required_dirs_exist():
    """Barcha muhim papkalar mavjud bo'lishi kerak."""
    missing = [d for d in REQUIRED_DIRS if not (ROOT / d).is_dir()]
    assert not missing, f"Majburiy papkalar yo'q:\n" + "\n".join(missing)


def test_all_python_files_valid_syntax():
    """Barcha Python fayllar sintaktik jihatdan to'g'ri bo'lishi kerak."""
    errors = []
    py_files = list(SRC.rglob("*.py")) + list((ROOT / "scripts").glob("*.py"))
    for f in py_files:
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{f.relative_to(ROOT)}: {e}")
    assert not errors, "Sintaksis xatolari:\n" + "\n".join(errors)


def test_services_init_exports_all():
    """services/__init__.py barcha asosiy klasslarni eksport qilishi kerak."""
    init = (SRC / "services" / "__init__.py").read_text(encoding="utf-8")
    required_exports = [
        "TTSService",
        "NonborService",
        "AsteriskAMI",
        "CallManager",
        "CallTracker",
        "TelegramService",
        "StatsService",
        "WebhookService",
        "AdminCallService",
    ]
    missing = [cls for cls in required_exports if cls not in init]
    assert not missing, f"services/__init__.py da eksport yo'q: {missing}"


def test_env_example_has_required_vars():
    """.env.example barcha muhim o'zgaruvchilarni o'z ichiga olishi kerak."""
    env_content = (ROOT / ".env.example").read_text(encoding="utf-8")
    required_vars = [
        "NONBOR_BASE_URL",
        "NONBOR_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "AMI_HOST",
        "AMI_PASSWORD",
        "SIP_USERNAME",
        "SIP_PASSWORD",
        "API_SECRET_KEY",
        "ASTERISK_SOUNDS_PATH",
        "ASTERISK_PLAYBACK_PATH",
        "WAIT_BEFORE_CALL",
        "MAX_CALL_ATTEMPTS",
    ]
    missing = [v for v in required_vars if v not in env_content]
    assert not missing, f".env.example da yo'q: {missing}"


def test_dockerfile_has_ffmpeg():
    """Dockerfile ffmpeg o'rnatishi kerak (audio konversiya uchun)."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ffmpeg" in dockerfile, "Dockerfile da ffmpeg yo'q — audio konversiya ishlamaydi!"


def test_docker_compose_has_shared_volume():
    """docker-compose.yml da autodialer-audio shared volume bo'lishi kerak."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "autodialer-audio" in compose, "Shared audio volume yo'q"
    assert "call-recordings" in compose, "Recordings volume yo'q"


def test_extensions_conf_has_dynamic_context():
    """extensions.conf da autodialer-dynamic context bo'lishi kerak."""
    conf = (ROOT / "config/asterisk/extensions.conf").read_text(encoding="utf-8")
    assert "[autodialer-dynamic]" in conf, "autodialer-dynamic context yo'q"
    assert "AUDIO_UZ" in conf, "AUDIO_UZ variable yo'q dialplanda"
    assert "AUDIO_RU" in conf, "AUDIO_RU variable yo'q dialplanda"
    assert "Playback" in conf, "Playback buyrug'i yo'q"


def test_requirements_has_core_deps():
    """requirements.txt asosiy kutubxonalarni o'z ichiga olishi kerak."""
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    required = ["aiohttp", "edge-tts", "python-dotenv"]
    missing = [r for r in required if r not in reqs]
    assert not missing, f"requirements.txt da yo'q: {missing}"
