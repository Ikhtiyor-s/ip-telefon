"""
Webhook Servisi
================

Tashqi servislar uchun event-based webhook tizimi.

Qo'llab-quvvatlanadigan eventlar:
  - call.completed  — qo'ng'iroq tugadi (javob berildi/berilmadi)
  - order.updated   — buyurtma statusi o'zgardi (accepted/rejected)

Har bir webhook POST so'rov sifatida yuboriladi:
  {
    "event": "call.completed",
    "timestamp": "2024-01-01T10:00:00",
    "data": { ... }
  }

Xavfsizlik: agar `secret` berilsa, har so'rovda
  X-Webhook-Signature: sha256=<hmac>  header qo'shiladi.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = {"call.completed", "order.updated"}
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_MAX_RETRIES = 2
_MAX_WEBHOOKS = 20  # Maksimal webhook soni


def _is_safe_url(url: str) -> bool:
    """SSRF himoya: ichki IP va localhost ga yo'l qo'yilmaydi."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        # DNS ni hal qilish
        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except socket.gaierror:
            pass  # DNS topilmasa — runtime da xato beradi, OK
        return True
    except Exception:
        return False


class WebhookService:
    """
    Webhook tizimini boshqaradi:
      - webhook ro'yxatga olish / o'chirish / ko'rsatish  (tashqi, SSRF-himoyali)
      - eventlar yuz berganda HTTP POST yuborish
      - CALL_REPORT_URL: ichki admin.nonbor uchun ishonchli kanal (.env orqali)
    """

    def __init__(self, data_dir: str = "data", trusted_report_url: str = ""):
        self._path = Path(data_dir) / "webhooks.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._webhooks: list[dict] = []
        # CALL_REPORT_URL — env var orqali berilgan ichki URL (SSRF tekshiruvisiz)
        self._trusted_url = trusted_report_url.strip() if trusted_report_url else ""
        self._load()
        if self._trusted_url:
            logger.info(f"WebhookService: ichki report URL sozlangan → {self._trusted_url}")
        logger.info(f"WebhookService ishga tushdi ({len(self._webhooks)} ta webhook)")

    # ─── persistence ──────────────────────────────────────────────────────────

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._webhooks = json.load(f)
            except Exception as e:
                logger.error(f"Webhook fayl o'qish xatosi: {e}")
                self._webhooks = []

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._webhooks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Webhook fayl saqlash xatosi: {e}")

    # ─── CRUD ─────────────────────────────────────────────────────────────────

    def list_webhooks(self) -> list[dict]:
        return [self._safe_view(w) for w in self._webhooks]

    def add_webhook(self, url: str, events: list[str], secret: str = "") -> dict:
        unknown = set(events) - SUPPORTED_EVENTS
        if unknown:
            raise ValueError(f"Noma'lum eventlar: {unknown}. Qabul qilinadi: {SUPPORTED_EVENTS}")
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL http:// yoki https:// bilan boshlanishi kerak")
        if not _is_safe_url(url):
            raise ValueError("URL ichki tarmoq manzillariga yo'naltirish mumkin emas (SSRF himoya)")
        if len(self._webhooks) >= _MAX_WEBHOOKS:
            raise ValueError(f"Maksimal webhook soni chegarasiga yetildi ({_MAX_WEBHOOKS})")

        entry = {
            "id": str(uuid.uuid4())[:8],
            "url": url,
            "events": list(set(events)),
            "secret": secret,
            "active": True,
            "created_at": datetime.now().isoformat(),
            "last_fired_at": None,
            "fail_count": 0,
        }
        self._webhooks.append(entry)
        self._save()
        logger.info(f"Webhook qo'shildi: {entry['id']} → {url} {entry['events']}")
        return self._safe_view(entry)

    def remove_webhook(self, webhook_id: str) -> bool:
        before = len(self._webhooks)
        self._webhooks = [w for w in self._webhooks if w["id"] != webhook_id]
        if len(self._webhooks) < before:
            self._save()
            logger.info(f"Webhook o'chirildi: {webhook_id}")
            return True
        return False

    def toggle_webhook(self, webhook_id: str) -> Optional[dict]:
        for w in self._webhooks:
            if w["id"] == webhook_id:
                w["active"] = not w["active"]
                self._save()
                return self._safe_view(w)
        return None

    # ─── firing ───────────────────────────────────────────────────────────────

    def schedule_event(self, event: str, data: dict):
        """Async event loop ichida webhook'larni yuborish uchun task yaratadi."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.fire_event(event, data))
        except RuntimeError:
            logger.warning(f"Webhook scheduleda event loop topilmadi: {event}")

    async def fire_event(self, event: str, data: dict):
        """Barcha mos webhook'larga + ichki admin URL ga event yuboradi."""
        targets = [w for w in self._webhooks if w.get("active") and event in w.get("events", [])]

        payload = json.dumps({
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }, ensure_ascii=False)

        async with aiohttp.ClientSession() as session:
            tasks = [self._send(session, w, event, payload) for w in targets]

            # Ichki admin.nonbor URL — SSRF tekshiruvisiz (env var = ishonchli)
            if self._trusted_url and event in ("call.completed", "order.updated"):
                tasks.append(self._send_trusted(session, event, payload))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_trusted(self, session: aiohttp.ClientSession, event: str, payload: str):
        """admin.nonbor uchun ichki kanal — SSRF himoyasisiz, env var orqali sozlangan."""
        import os
        nonbor_secret = os.getenv("NONBOR_SECRET", "")
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event,
            "User-Agent": "AutodialerWebhook/1.0",
        }
        if nonbor_secret:
            headers["X-Telegram-Bot-Secret"] = nonbor_secret
        try:
            async with session.post(
                self._trusted_url,
                data=payload,
                headers=headers,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status < 300:
                    logger.info(f"Admin report OK [{event}] → {resp.status}")
                else:
                    logger.warning(f"Admin report xato [{event}] → HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"Admin report yuborishda xato [{event}]: {e}")

    async def _send(self, session: aiohttp.ClientSession, webhook: dict, event: str, payload: str):
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event,
            "User-Agent": "AutodialerWebhook/1.0",
        }
        if webhook.get("secret"):
            sig = hmac.new(
                webhook["secret"].encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with session.post(
                    webhook["url"],
                    data=payload,
                    headers=headers,
                    timeout=_TIMEOUT,
                ) as resp:
                    webhook["last_fired_at"] = datetime.now().isoformat()
                    if resp.status < 300:
                        webhook["fail_count"] = 0
                        logger.info(f"Webhook OK {webhook['id']} [{event}] → {resp.status}")
                    else:
                        webhook["fail_count"] = webhook.get("fail_count", 0) + 1
                        logger.warning(
                            f"Webhook {webhook['id']} [{event}] HTTP {resp.status} "
                            f"(urinish {attempt}/{_MAX_RETRIES})"
                        )
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(2)
                            continue
                    self._save()
                    return
            except Exception as e:
                webhook["fail_count"] = webhook.get("fail_count", 0) + 1
                logger.warning(
                    f"Webhook {webhook['id']} [{event}] xato: {e} "
                    f"(urinish {attempt}/{_MAX_RETRIES})"
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2)

        self._save()

    # ─── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_view(w: dict) -> dict:
        """Secret ni ko'rsatmasdan webhook ma'lumotlarini qaytaradi."""
        return {
            "id": w["id"],
            "url": w["url"],
            "events": w["events"],
            "active": w["active"],
            "created_at": w["created_at"],
            "last_fired_at": w.get("last_fired_at"),
            "fail_count": w.get("fail_count", 0),
            "has_secret": bool(w.get("secret")),
        }
