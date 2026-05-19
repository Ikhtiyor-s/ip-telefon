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
        # 400 yoki 404 bo'lishi kerak, 200 emas
        assert resp.status in (400, 404)


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
        assert resp.status in (400, 404)
