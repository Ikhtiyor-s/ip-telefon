"""
Import va asosiy funksional testlar.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC  = ROOT / "src"
sys.path.insert(0, str(SRC))


def test_webhook_service_init():
    from services.webhook_service import WebhookService
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        assert ws.list_webhooks() == []


def test_webhook_add_ssrf_blocked():
    from services.webhook_service import WebhookService
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        try:
            ws.add_webhook("http://localhost/hook", ["call.completed"])
            assert False, "SSRF bloklanmadi"
        except ValueError as e:
            assert "ichki tarmoq" in str(e)


def test_webhook_add_valid():
    from services.webhook_service import WebhookService
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = WebhookService(data_dir=d)
        entry = ws.add_webhook("https://example.com/hook", ["call.completed"])
        assert entry["id"]
        assert entry["active"] is True
        assert not entry["has_secret"]


def test_stats_service_record_call():
    from services.stats_service import StatsService, CallResult
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        svc = StatsService(data_dir=d)
        svc.record_call(
            phone="+998901234567",
            seller_name="Test Do'kon",
            order_count=2,
            attempts=1,
            result=CallResult.ANSWERED,
            order_ids=[1, 2],
        )
        today = svc.get_today_stats()
        assert today.total_calls == 1
        assert today.answered_calls == 1
        assert today.calls_1_attempt == 1


def test_stats_service_record_order():
    from services.stats_service import StatsService, OrderResult
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        svc = StatsService(data_dir=d)
        svc.record_order(
            order_id=123,
            order_number="#123",
            seller_name="Test",
            seller_phone="+998901234567",
            client_name="Mijoz",
            product_name="Mahsulot",
            price=50000,
            result=OrderResult.ACCEPTED,
            call_attempts=1,
            telegram_sent=True,
        )
        today = svc.get_today_stats()
        assert today.total_orders == 1
        assert today.accepted_orders == 1


def test_call_tracker_phone_detection():
    """CallTracker tashqi raqamni to'g'ri aniqlashi kerak."""
    from services.asterisk_service import CallTracker
    assert CallTracker._is_external("+998901234567") is True
    assert CallTracker._is_external("998901234567") is True
    assert CallTracker._is_external("101") is False
    assert CallTracker._is_external("1001") is False
    assert CallTracker._is_external("9012345") is True


def test_api_server_importable():
    import api_server
    assert hasattr(api_server, "AutodialerAPI")
