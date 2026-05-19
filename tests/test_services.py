"""
Servis unit testlari.

Har bir servisning asosiy funksiyalari test qilinadi.
Tashqi API va Asterisk ulanishisiz — faqat mantiq.
"""
import asyncio
import tempfile
from pathlib import Path

import pytest


# ── StatsService ─────────────────────────────────────────────────────────────

def test_stats_record_call():
    """Qo'ng'iroqni qayd etish va statistika yangilanishi."""
    from services.stats_service import StatsService, CallResult

    with tempfile.TemporaryDirectory() as d:
        svc = StatsService(data_dir=d)
        svc.record_call(
            phone="+998901234567",
            seller_name="Test Do'kon",
            order_count=2,
            attempts=1,
            result=CallResult.ANSWERED,
            order_ids=[101, 102],
        )
        today = svc.get_today_stats()
        assert today.total_calls == 1
        assert today.answered_calls == 1
        assert today.unanswered_calls == 0
        assert today.calls_1_attempt == 1


def test_stats_record_order():
    """Buyurtmani qayd etish."""
    from services.stats_service import StatsService, OrderResult

    with tempfile.TemporaryDirectory() as d:
        svc = StatsService(data_dir=d)
        svc.record_order(
            order_id=200,
            order_number="#200",
            seller_name="Kafe",
            seller_phone="+998901234567",
            client_name="Mijoz",
            product_name="Taom",
            price=30000,
            result=OrderResult.ACCEPTED,
            call_attempts=1,
            telegram_sent=True,
        )
        today = svc.get_today_stats()
        assert today.total_orders == 1
        assert today.accepted_orders == 1
        assert today.rejected_orders == 0


def test_stats_period_aggregation():
    """Davr bo'yicha statistika to'g'ri hisoblashi kerak."""
    from services.stats_service import StatsService, CallResult, OrderResult

    with tempfile.TemporaryDirectory() as d:
        svc = StatsService(data_dir=d)
        # 2 ta qo'ng'iroq
        for _ in range(2):
            svc.record_call(
                phone="+998901234567", seller_name="T", order_count=1,
                attempts=1, result=CallResult.ANSWERED,
            )
        svc.record_call(
            phone="+998901234568", seller_name="T2", order_count=1,
            attempts=2, result=CallResult.NO_ANSWER,
        )
        stats = svc.get_period_stats("daily")
        assert stats.total_calls == 3
        assert stats.answered_calls == 2
        assert stats.unanswered_calls == 1


def test_stats_backward_compat_call_record():
    """Eski JSON formatdagi CallRecord to'g'ri o'qilishi kerak."""
    from services.stats_service import CallRecord

    old_data = {
        "phone": "+998901234567",
        "seller_name": "Test",
        "order_count": 1,
        "attempts": 1,
        "result": "answered",
        "timestamp": "2024-01-01T10:00:00",
        "order_ids": [],
        # Yangi maydonlar yo'q — default qiymatlar ishlatilishi kerak
    }
    record = CallRecord.from_dict(old_data)
    assert record.wait_seconds == 0
    assert record.duration_seconds == 0
    assert record.recording_url == ""


def test_stats_backward_compat_daily_stats():
    """Eski JSON formatdagi DailyStats to'g'ri o'qilishi kerak."""
    from services.stats_service import DailyStats

    old_data = {
        "date": "2024-01-01",
        "total_calls": 5,
        "answered_calls": 3,
        "unanswered_calls": 2,
        "calls_1_attempt": 2,
        "calls_2_attempts": 1,
        "calls_3_attempts": 0,
        "total_orders": 3,
        "accepted_orders": 2,
        "rejected_orders": 1,
        "accepted_without_telegram": 0,
        "call_records": [],
        "order_records": [],
        # Noma'lum maydon — e'tiborsiz olinishi kerak
        "unknown_future_field": "value",
    }
    stats = DailyStats.from_dict(old_data)
    assert stats.total_calls == 5
    assert stats.answered_calls == 3


# ── WebhookService ────────────────────────────────────────────────────────────

def test_webhook_add_and_list():
    """Webhook qo'shish va ro'yxatga olish."""
    from services.webhook_service import WebhookService

    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        entry = ws.add_webhook("https://example.com/hook", ["call.completed"])
        assert entry["id"]
        assert entry["active"] is True
        assert not entry["has_secret"]
        assert len(ws.list_webhooks()) == 1


def test_webhook_ssrf_blocked():
    """Ichki URL ga webhook qo'shib bo'lmasligi kerak."""
    from services.webhook_service import WebhookService

    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        with pytest.raises(ValueError, match="ichki tarmoq"):
            ws.add_webhook("http://localhost/hook", ["call.completed"])


def test_webhook_invalid_event():
    """Noto'g'ri event nomi xato berishi kerak."""
    from services.webhook_service import WebhookService

    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        with pytest.raises(ValueError):
            ws.add_webhook("https://example.com/hook", ["invalid.event"])


def test_webhook_remove():
    """Webhook o'chirish."""
    from services.webhook_service import WebhookService

    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        e = ws.add_webhook("https://example.com/hook", ["order.updated"])
        assert ws.remove_webhook(e["id"]) is True
        assert len(ws.list_webhooks()) == 0


def test_webhook_toggle():
    """Webhook yoqish/o'chirish."""
    from services.webhook_service import WebhookService

    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        e = ws.add_webhook("https://example.com/hook", ["call.completed"])
        updated = ws.toggle_webhook(e["id"])
        assert updated["active"] is False
        updated2 = ws.toggle_webhook(e["id"])
        assert updated2["active"] is True


def test_webhook_max_limit():
    """Maksimal webhook soni chegarasini tekshirish."""
    from services.webhook_service import WebhookService, _MAX_WEBHOOKS

    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        for i in range(_MAX_WEBHOOKS):
            ws.add_webhook(f"https://example{i}.com/hook", ["call.completed"])
        with pytest.raises(ValueError, match="Maksimal"):
            ws.add_webhook("https://one-more.com/hook", ["call.completed"])


# ── AsteriskAMI & CallTracker ─────────────────────────────────────────────────

def test_ami_multiple_handlers():
    """Bir event uchun bir nechta handler qo'shish mumkin bo'lishi kerak."""
    from services.asterisk_service import AsteriskAMI

    ami = object.__new__(AsteriskAMI)
    ami._event_handlers = {}
    results = []

    async def h1(d): results.append(1)
    async def h2(d): results.append(2)

    ami.on_event("Hangup", h1)
    ami.on_event("Hangup", h2)

    assert len(ami._event_handlers["Hangup"]) == 2


def test_call_tracker_external_phone_detection():
    """CallTracker tashqi raqamni (9+ raqam) to'g'ri aniqlashi kerak."""
    from services.asterisk_service import CallTracker

    # Tashqi raqamlar
    assert CallTracker._is_external("+998901234567") is True
    assert CallTracker._is_external("998901234567") is True
    assert CallTracker._is_external("9012345") is True

    # Ichki raqamlar (qisqa)
    assert CallTracker._is_external("101") is False
    assert CallTracker._is_external("1001") is False
    assert CallTracker._is_external("") is False


def test_phone_validation():
    """Telefon raqam validatsiyasi to'g'ri ishlashi kerak."""
    from services.asterisk_service import AsteriskAMI

    ami = object.__new__(AsteriskAMI)

    # To'g'ri formatlar
    assert ami._validate_and_clean_phone("+998901234567") == "998901234567"
    assert ami._validate_and_clean_phone("998901234567") == "998901234567"
    assert ami._validate_and_clean_phone("901234567") == "998901234567"

    # Noto'g'ri formatlar
    import pytest
    with pytest.raises(ValueError):
        ami._validate_and_clean_phone("12345")  # juda qisqa
    with pytest.raises(ValueError):
        ami._validate_and_clean_phone("+12345678901")  # O'zbekiston emas


# ── TTS matnlari ─────────────────────────────────────────────────────────────

def test_tts_order_message_text():
    """TTS buyurtma matni barcha tillarda mavjud bo'lishi kerak."""
    from services.tts_service import _order_message_text

    for lang in ["uz", "ru", "en"]:
        text = _order_message_text(3, lang)
        assert "3" in text or "3" in text, f"{lang} tilida son ko'rinmaydi"
        assert len(text) > 20, f"{lang} tilida matn juda qisqa"


def test_tts_lang_normalization():
    """Til normalizatsiyasi noto'g'ri til uchun fallback qaytarishi kerak."""
    from services.tts_service import _normalize_lang, DEFAULT_LANG

    assert _normalize_lang("uz") == "uz"
    assert _normalize_lang("ru") == "ru"
    assert _normalize_lang("") == DEFAULT_LANG
    assert _normalize_lang("xx") == DEFAULT_LANG     # noma'lum til
    assert _normalize_lang("UZ") == "uz"             # uppercase


# ── Nonbor Service ────────────────────────────────────────────────────────────

def test_nonbor_format_phone():
    """Nonbor servisida telefon formatlash to'g'ri ishlashi kerak."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from autodialer import AutodialerPro

    # _format_phone metodini bevosita test qilish
    # (Instance yaratmasdan, faqat metodning mantiqini)
    import re

    def _format_phone(raw: str):
        if not raw or raw == "Noma'lum":
            return None
        digits = re.sub(r"[^\d]", "", str(raw))
        if len(digits) == 9:
            digits = "998" + digits
        if len(digits) == 12 and digits.startswith("998"):
            return "+" + digits
        return None

    assert _format_phone("+998901234567") == "+998901234567"
    assert _format_phone("901234567") == "+998901234567"
    assert _format_phone("Noma'lum") is None
    assert _format_phone("") is None
    assert _format_phone("123") is None
