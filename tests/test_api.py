"""
API Server testlari.

AutodialerAPI endpoint'lari va middleware ni tekshiradi.
Haqiqiy autodialer instance'isiz — mock orqali.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def api():
    """Test uchun AutodialerAPI instance'i (autodialer=None)."""
    from api_server import AutodialerAPI
    return AutodialerAPI(autodialer=None, port=18585)


# ── Import tekshiruvi ─────────────────────────────────────────────────────────

def test_api_server_importable():
    """api_server.py import qilinishi kerak."""
    import api_server
    assert hasattr(api_server, "AutodialerAPI")


def test_api_class_has_required_methods(api):
    """AutodialerAPI barcha muhim metodlarga ega bo'lishi kerak."""
    required = [
        "health", "get_stats", "get_calls", "get_orders",
        "get_businesses", "get_config", "list_webhooks",
        "get_admin_call_config", "get_recording",
    ]
    for method in required:
        assert hasattr(api, method), f"AutodialerAPI.{method}() metodi yo'q"


def test_api_has_all_routes(api):
    """Barcha muhim route'lar ro'yxatga olinishi kerak."""
    routes = [r.resource.canonical for r in api.app.router.routes()
              if hasattr(r, 'resource')]

    required_paths = [
        "/api/autodialer/health",
        "/api/autodialer/stats",
        "/api/autodialer/calls",
        "/api/autodialer/orders",
        "/api/autodialer/businesses",
        "/api/autodialer/config",
        "/webhook/inbound",
        "/webhook/inbound/ping",
        "/api/autodialer/webhooks",
        "/api/autodialer/admin-call/phones",
    ]
    for path in required_paths:
        assert path in routes, f"Route yo'q: {path}"


# ── Health endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint_no_auth(api):
    """Health endpoint autentifikatsiyasiz ishlashi kerak."""
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get("/api/autodialer/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(api):
    """Himoyalangan endpoint'lar X-API-Key talab qilishi kerak."""
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        # Auth headerisiz
        resp = await client.get("/api/autodialer/stats")
        assert resp.status == 401

        data = await resp.json()
        assert data["success"] is False


@pytest.mark.asyncio
async def test_empty_api_key_rejected(api):
    """Bo'sh X-API-Key rad etilishi kerak."""
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get(
            "/api/autodialer/stats",
            headers={"X-API-Key": ""}
        )
        assert resp.status == 401


@pytest.mark.asyncio
async def test_wrong_api_key_rejected(api):
    """Noto'g'ri X-API-Key rad etilishi kerak."""
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get(
            "/api/autodialer/stats",
            headers={"X-API-Key": "wrong-key"}
        )
        assert resp.status == 401


# ── Stats endpoint (autodialer=None holatida) ─────────────────────────────────

@pytest.mark.asyncio
async def test_stats_returns_zeros_when_no_autodialer(api):
    """autodialer=None bo'lganda stats 0 qaytarishi kerak (crash emas)."""
    import os
    api.api_key = "test-key-for-unit-test"

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get(
            "/api/autodialer/stats",
            headers={"X-API-Key": "test-key-for-unit-test"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["data"]["total_calls"] == 0


@pytest.mark.asyncio
async def test_webhooks_list_empty_when_no_service(api):
    """webhook_service=None bo'lganda 503 qaytarishi kerak."""
    api.api_key = "test-key-for-unit-test"

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get(
            "/api/autodialer/webhooks",
            headers={"X-API-Key": "test-key-for-unit-test"}
        )
        # webhook_service=None → 503
        assert resp.status == 503


# ── Recording endpoint ────────────────────────────────────────────────────────

# ── Inbound webhook ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inbound_webhook_ping(api):
    """/webhook/inbound/ping auth kerak emas."""
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get("/webhook/inbound/ping")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert "supported_events" in data


@pytest.mark.asyncio
async def test_inbound_webhook_no_secret_rejected(api, monkeypatch):
    """Secret sozlanmasa — 401."""
    monkeypatch.delenv("INBOUND_WEBHOOK_SECRET", raising=False)
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post("/webhook/inbound",
                                 json={"event": "new_order", "phone": "+998901234567"})
        assert resp.status == 401


@pytest.mark.asyncio
async def test_inbound_webhook_wrong_secret_rejected(api, monkeypatch):
    """Noto'g'ri secret — 401."""
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "correct-secret")
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json={"event": "new_order", "phone": "+998901234567"},
            headers={"X-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status == 401


@pytest.mark.asyncio
async def test_inbound_webhook_valid_secret_accepted(api, monkeypatch):
    """To'g'ri secret — 200."""
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-webhook-secret")
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json={"event": "order_update", "business_id": 1, "status": "completed"},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True


@pytest.mark.asyncio
async def test_inbound_webhook_unknown_event_ignored(api, monkeypatch):
    """Noma'lum event — 200 bilan ignored."""
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-webhook-secret")
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json={"event": "unknown_event"},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["action"] == "ignored"


@pytest.mark.asyncio
async def test_inbound_webhook_invalid_json(api, monkeypatch):
    """Noto'g'ri JSON — 400."""
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-webhook-secret")
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            data=b"not-json",
            headers={
                "X-Webhook-Secret": "test-webhook-secret",
                "Content-Type": "application/json",
            },
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_inbound_webhook_call_now_no_phone(api, monkeypatch):
    """call_now phone'siz — 400."""
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-webhook-secret")
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json={"event": "call_now"},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert resp.status == 400


# ── Recording endpoint ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recording_path_traversal_blocked(api):
    """../  path traversal bloklanishi kerak."""
    api.api_key = "test-key-for-unit-test"

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        # Path traversal urinish
        resp = await client.get(
            "/api/autodialer/recordings/../etc/passwd.wav",
            headers={"X-API-Key": "test-key-for-unit-test"}
        )
        # 400, 404 yoki 405 bo'lishi kerak (aiohttp URL'ni normallashtiradi, 200 emas)
        assert resp.status in (400, 404, 405)


@pytest.mark.asyncio
async def test_recording_invalid_filename_rejected(api):
    """Noto'g'ri fayl nomi (shell chars) rad etilishi kerak."""
    api.api_key = "test-key-for-unit-test"

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get(
            "/api/autodialer/recordings/../../secret.wav",
            headers={"X-API-Key": "test-key-for-unit-test"}
        )
        assert resp.status in (400, 404, 405)


# ── Nonbor v2 webhook spec ────────────────────────────────────────────────────

def _v2_sign(secret: str, timestamp: str, body: bytes) -> str:
    """Yangi spec imzosi: sha256=HMAC(secret, "{ts}.{body}")."""
    import hmac as _hmac
    import hashlib
    msg = timestamp.encode("utf-8") + b"." + body
    return "sha256=" + _hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


@pytest.fixture
def api_with_mock_autodialer():
    """v2 order eventlarini sinash uchun mock autodialer'li API."""
    from api_server import AutodialerAPI
    ad = MagicMock()
    ad._order_data_cache = {}
    ad._on_new_orders = AsyncMock()
    ad._on_orders_resolved = AsyncMock()
    return AutodialerAPI(autodialer=ad, port=18586), ad


def _v2_body(event: str, order_id: int = 9001, new_state: str = "") -> dict:
    body = {
        "event_id": f"evt-{order_id}",
        "event": event,
        "created_at": "2026-06-03T10:30:00Z",
        "business_id": 42,
        "order": {
            "id": order_id,
            "state": "CHECKING",
            "total_price": 85000,
            "business": {
                "id": 42, "title": "Test Restoran",
                "phone_number": "+998901234567",
            },
            "user": {"first_name": "Ali", "last_name": "Valiyev", "phone": "+998907654321"},
            "order_item": [{"count": 1, "product": {"name": "Lag'mon"}}],
        },
    }
    if new_state:
        body["new_values"] = {"state": new_state}
    return body


@pytest.mark.asyncio
async def test_v2_signature_valid(api, monkeypatch):
    """v2 imzo (sha256=HMAC(secret, ts.body)) qabul qilinishi kerak."""
    import json as _json
    import time as _time

    secret = "test-v2-secret"
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", secret)

    body_dict = {"event": "new_orders", "orders": []}
    body_bytes = _json.dumps(body_dict).encode()
    ts = str(int(_time.time()))
    sig = _v2_sign(secret, ts, body_bytes)

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Timestamp": ts,
                "X-Webhook-Signature": sig,
            },
        )
        # 400 (bo'sh orders) ham OK — auth o'tdi degani
        assert resp.status in (200, 400)


@pytest.mark.asyncio
async def test_v2_timestamp_too_old_rejected(api, monkeypatch):
    """600s eski timestamp → 401."""
    import json as _json
    import time as _time

    secret = "test-v2-secret"
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("INBOUND_WEBHOOK_TIMESTAMP_WINDOW", "300")

    body_bytes = _json.dumps({"event": "order.paid", "order": {"id": 1}}).encode()
    ts = str(int(_time.time()) - 600)  # 10 daqiqa eski
    sig = _v2_sign(secret, ts, body_bytes)

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Timestamp": ts,
                "X-Webhook-Signature": sig,
            },
        )
        assert resp.status == 401


@pytest.mark.asyncio
async def test_v2_timestamp_invalid_format(api, monkeypatch):
    """X-Webhook-Timestamp raqam bo'lmasa — 400."""
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-v2-secret")
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json={"event": "order.paid"},
            headers={
                "X-Webhook-Timestamp": "not-a-number",
                "X-Webhook-Signature": "sha256=deadbeef",
            },
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_v2_event_id_dedup(api_with_mock_autodialer, monkeypatch):
    """Bir xil X-Webhook-Id ikki marta → ikkinchisida deduplicated=true."""
    api, _ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    body = _v2_body("order.paid", order_id=7777)
    headers = {
        "X-Webhook-Secret": "test-secret",
        "X-Webhook-Id": "unique-event-id-7777",
        "X-Webhook-Event": "order.paid",
    }

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        r1 = await client.post("/webhook/inbound", json=body, headers=headers)
        assert r1.status == 200
        d1 = await r1.json()
        assert d1.get("deduplicated") is not True

        r2 = await client.post("/webhook/inbound", json=body, headers=headers)
        assert r2.status == 200
        d2 = await r2.json()
        assert d2.get("deduplicated") is True


@pytest.mark.asyncio
async def test_v2_order_created_triggers_autodialer(api_with_mock_autodialer, monkeypatch):
    """order.created → autodialer._on_new_orders chaqirilishi."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json=_v2_body("order.created", order_id=9001),
            headers={
                "X-Webhook-Secret": "test-secret",
                "X-Webhook-Event": "order.created",
                "X-Webhook-Id": "evt-create-9001",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["event"] == "order.created"
        assert data["action"] == "queued"
        assert data["order_id"] == 9001

        # Task asinxron yaratiladi — kutamiz
        await asyncio.sleep(0.05)
        ad._on_new_orders.assert_called_once()
        kwargs = ad._on_new_orders.call_args.kwargs
        assert kwargs["new_ids"] == [9001]
        assert 9001 in ad._order_data_cache


@pytest.mark.asyncio
async def test_v2_status_changed_accepted_resolves(api_with_mock_autodialer, monkeypatch):
    """order.status_changed → ACCEPTED → _on_orders_resolved."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json=_v2_body("order.status_changed", order_id=9002, new_state="ACCEPTED"),
            headers={
                "X-Webhook-Secret": "test-secret",
                "X-Webhook-Id": "evt-status-9002",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["action"] == "resolved"
        assert data["state"] == "ACCEPTED"

        await asyncio.sleep(0.05)
        ad._on_orders_resolved.assert_called_once()
        kwargs = ad._on_orders_resolved.call_args.kwargs
        assert kwargs["resolved_ids"] == [9002]


@pytest.mark.asyncio
async def test_v2_status_changed_non_terminal_noted(api_with_mock_autodialer, monkeypatch):
    """order.status_changed → CHECKING → noted (resolve chaqirilmaydi)."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json=_v2_body("order.status_changed", order_id=9003, new_state="CHECKING"),
            headers={
                "X-Webhook-Secret": "test-secret",
                "X-Webhook-Id": "evt-status-9003",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["action"] == "noted"

        await asyncio.sleep(0.05)
        ad._on_orders_resolved.assert_not_called()


@pytest.mark.asyncio
async def test_v2_order_cancelled_resolves(api_with_mock_autodialer, monkeypatch):
    """order.cancelled → _on_orders_resolved."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json=_v2_body("order.cancelled", order_id=9004),
            headers={
                "X-Webhook-Secret": "test-secret",
                "X-Webhook-Id": "evt-cancel-9004",
            },
        )
        assert resp.status == 200
        assert (await resp.json())["action"] == "resolved"
        await asyncio.sleep(0.05)
        ad._on_orders_resolved.assert_called_once()


@pytest.mark.asyncio
async def test_v2_order_paid_log_only(api_with_mock_autodialer, monkeypatch):
    """order.paid → action=logged, autodialer flow chaqirilmaydi."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json=_v2_body("order.paid", order_id=9005),
            headers={
                "X-Webhook-Secret": "test-secret",
                "X-Webhook-Id": "evt-paid-9005",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["action"] == "logged"

        await asyncio.sleep(0.05)
        ad._on_new_orders.assert_not_called()
        ad._on_orders_resolved.assert_not_called()


@pytest.mark.asyncio
async def test_v2_order_missing_id_rejected(api_with_mock_autodialer, monkeypatch):
    """order.id yo'q bo'lsa — 400."""
    api, _ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json={"event": "order.created", "order": {}},
            headers={"X-Webhook-Secret": "test-secret"},
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_v2_ping_advertises_v2(api):
    """/webhook/inbound/ping yangi v2 spec maydonlarini ko'rsatishi kerak."""
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.get("/webhook/inbound/ping")
        data = await resp.json()
        assert "supported_events_v2" in data
        assert "order.created" in data["supported_events_v2"]
        assert "auth_v2" in data
        assert "X-Webhook-Timestamp" in data["auth_v2"]["headers"]


# ── EventDedup unit testlari ──────────────────────────────────────────────────

def test_event_dedup_first_seen_returns_false():
    from utils.webhook_dedup import EventDedup
    d = EventDedup()
    assert d.seen("evt-1") is False


def test_event_dedup_second_seen_returns_true():
    from utils.webhook_dedup import EventDedup
    d = EventDedup()
    d.seen("evt-1")
    assert d.seen("evt-1") is True


def test_event_dedup_empty_id_returns_false():
    from utils.webhook_dedup import EventDedup
    d = EventDedup()
    assert d.seen("") is False
    assert d.seen("") is False  # bo'sh id hech qachon dedup qilinmaydi


def test_event_dedup_lru_eviction():
    from utils.webhook_dedup import EventDedup
    d = EventDedup(maxsize=3, ttl_seconds=3600)
    for i in range(5):
        d.seen(f"evt-{i}")
    assert d.size() == 3
    # Eng eski ikkitasi siqib chiqarilgan
    assert d.seen("evt-0") is False
    assert d.seen("evt-1") is False
    # Yangi uchtasi hali kesh ichida
    assert d.seen("evt-4") is True


def test_event_dedup_ttl_expiry(monkeypatch):
    """TTL chiqgan event qayta seen=False bo'lishi kerak."""
    import time as _time
    from utils.webhook_dedup import EventDedup

    base = [1000000.0]

    def fake_time():
        return base[0]

    monkeypatch.setattr(_time, "time", fake_time)
    d = EventDedup(maxsize=100, ttl_seconds=60)
    d.seen("evt-1")

    base[0] += 30
    assert d.seen("evt-1") is True  # hali 30s o'tdi, hali kesh ichida

    base[0] += 60  # jami 90s — TTL chiqdi
    assert d.seen("evt-1") is False  # qayta yangi sifatida qaraladi


# ── Nonbor v2 spec body parser (normalize_order_from_dict) ────────────────────

def _v2_spec_body() -> dict:
    """Nonbor v2 spec namunaga aynan mos order obyekti — phone_number YO'Q."""
    return {
        "id": 1001,
        "state": "ACCEPTED",
        "type": "DINE_IN",
        "source": "APP",
        "price": "85000.00",
        "total_price": "95000.00",
        "payment_method": "CASH",
        "delivery_method": "DELIVERY",
        "paid": False,
        "business": {"id": 42, "title": "Nonbor Restoran"},
        "delivery": {
            "provider": "YANDEX",
            "lat": 41.2995,
            "long": 69.2401,
            "address": "Toshkent, Yunusobod 7",
            "price": "10000.00",
        },
        "items": [
            {
                "id": 501,
                "product_id": 88,
                "product_name": "Lag'mon",
                "quantity": 2,
                "price": "25000.00",
                "guest_name": None,
            }
        ],
    }


def test_normalize_v2_spec_product_name():
    """v2 spec items[].product_name to'g'ri o'qilishi kerak (nested product.name emas)."""
    from services.nonbor_service import NonborService
    result = NonborService.normalize_order_from_dict(_v2_spec_body())
    assert result["product_name"] == "Lag'mon"


def test_normalize_v2_spec_quantity():
    """v2 spec items[].quantity to'g'ri o'qilishi kerak (count emas)."""
    from services.nonbor_service import NonborService
    result = NonborService.normalize_order_from_dict(_v2_spec_body())
    assert result["quantity"] == 2


def test_normalize_v2_spec_total_price_string():
    """v2 spec total_price string ("95000.00") — TypeError emas, 95000 so'm."""
    from services.nonbor_service import NonborService
    result = NonborService.normalize_order_from_dict(_v2_spec_body())
    assert result["price"] == 95000.0


def test_normalize_v1_total_price_int_kopecks():
    """Eski format — int kopeyka (150000) — 1500 so'mga aylantirilishi kerak."""
    from services.nonbor_service import NonborService
    result = NonborService.normalize_order_from_dict({
        "id": 1, "total_price": 150000,
        "business": {"id": 1, "title": "X", "phone_number": "+998901234567"},
        "order_item": [{"count": 1, "product": {"name": "Test"}}],
    })
    assert result["price"] == 1500.0


def test_normalize_v2_spec_seller_phone_missing():
    """v2 spec business obyektida phone_number yo'q — seller_phone "Noma'lum"."""
    from services.nonbor_service import NonborService
    result = NonborService.normalize_order_from_dict(_v2_spec_body())
    assert result["seller_phone"] == "Noma'lum"


def test_normalize_total_price_garbage():
    """total_price tushunarsiz bo'lsa — 0, hech qanday exception emas."""
    from services.nonbor_service import NonborService
    result = NonborService.normalize_order_from_dict({
        "id": 1, "total_price": "not-a-number",
        "business": {"id": 1, "title": "X"},
    })
    assert result["price"] == 0


# ── Cache skip behavior — webhook'da phone yo'q bo'lsa ───────────────────────

@pytest.mark.asyncio
async def test_v2_no_phone_skips_cache(api_with_mock_autodialer, monkeypatch):
    """v2 spec body (business.phone_number yo'q) — cache'ga yozilmaydi,
    lekin autodialer flow baribir ishga tushadi (API fallback uchun)."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    body = {
        "event_id": "evt-no-phone-1",
        "event": "order.created",
        "business_id": 42,
        "order": _v2_spec_body(),  # phone_number yo'q
    }

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post(
            "/webhook/inbound",
            json=body,
            headers={
                "X-Webhook-Secret": "test-secret",
                "X-Webhook-Id": "evt-no-phone-1",
            },
        )
        assert resp.status == 200

        await asyncio.sleep(0.05)
        # Autodialer flow ishga tushdi
        ad._on_new_orders.assert_called_once()
        # Lekin cache bo'sh — autodialer keyin get_order_full_data orqali oladi
        assert 1001 not in ad._order_data_cache


@pytest.mark.asyncio
async def test_v2_with_phone_caches(api_with_mock_autodialer, monkeypatch):
    """Webhook body'da phone_number bor bo'lsa — cache'ga yoziladi."""
    api, ad = api_with_mock_autodialer
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", "test-secret")

    body = _v2_body("order.created", order_id=8888)  # phone_number BOR
    headers = {"X-Webhook-Secret": "test-secret", "X-Webhook-Id": "evt-8888"}

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(api.app)) as client:
        resp = await client.post("/webhook/inbound", json=body, headers=headers)
        assert resp.status == 200

        await asyncio.sleep(0.05)
        assert 8888 in ad._order_data_cache
        cached = ad._order_data_cache[8888]
        assert cached["seller_phone"].startswith("+")
