"""
Autodialer HTTP API Server
===========================

Admin panel uchun REST API - aiohttp.web asosida.
Autodialer ichida ishga tushadi va barcha sozlamalarni
restart qilmasdan boshqarish imkonini beradi.

Port: 8585 (default)
"""

import json
import logging
import os
import subprocess
import asyncio
from datetime import date, timedelta
from pathlib import Path
from aiohttp import web
import aiohttp

logger = logging.getLogger("api_server")

# Data katalogi
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"


class AutodialerAPI:
    """
    Autodialer HTTP API

    autodialer instance ga havola orqali real-time
    sozlamalar va statistikani boshqaradi
    """

    def __init__(self, autodialer=None, port: int = 8585):
        self.autodialer = autodialer
        self.port = port
        self.app = web.Application(middlewares=[self._cors_middleware])
        self._setup_routes()
        self._runner = None

    @web.middleware
    async def _cors_middleware(self, request, handler):
        """CORS middleware — admin panel uchun"""
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as ex:
                resp = ex
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    def _setup_routes(self):
        r = self.app.router
        # Stats
        r.add_get("/api/autodialer/stats", self.get_stats)
        r.add_get("/api/autodialer/calls", self.get_calls)
        r.add_get("/api/autodialer/orders", self.get_orders)
        r.add_get("/api/autodialer/daily-trend", self.get_daily_trend)
        r.add_get("/api/autodialer/order-statuses", self.get_order_statuses)
        r.add_get("/api/autodialer/live-orders", self.get_live_orders)
        # Businesses
        r.add_get("/api/autodialer/businesses", self.get_businesses)
        r.add_post("/api/autodialer/businesses/{biz_id}/toggle-call", self.toggle_call)
        r.add_get("/api/autodialer/businesses/{biz_id}/config", self.get_business_config)
        r.add_post("/api/autodialer/businesses/{biz_id}/config", self.update_business_config)
        r.add_post("/api/autodialer/businesses/{biz_id}/set-group", self.set_business_group)
        r.add_get("/api/autodialer/businesses/{biz_id}/orders", self.get_business_orders)
        # Config
        r.add_get("/api/autodialer/config", self.get_config)
        r.add_post("/api/autodialer/config/update", self.update_config)
        # Service control
        r.add_get("/api/autodialer/service/status", self.get_service_status)
        r.add_post("/api/autodialer/service/{action}", self.control_service)
        # Logs
        r.add_get("/api/autodialer/logs", self.get_logs)
        # Telegram
        r.add_get("/api/autodialer/chat-info", self.get_chat_info)
        # Health
        r.add_get("/api/autodialer/health", self.health)
        # AI trigger — biznes ID bo'yicha qo'ng'iroq
        r.add_post("/api/autodialer/call-business", self.call_business)
        # OPTIONS preflight uchun
        r.add_route("OPTIONS", "/{path:.*}", self._options_handler)

    async def _options_handler(self, request):
        return web.Response()

    # ===== HELPERS =====

    def _json(self, data, status=200):
        return web.json_response(data, status=status)

    def _ok(self, data=None, **kwargs):
        resp = {"success": True}
        if data is not None:
            resp["data"] = data
        resp.update(kwargs)
        return self._json(resp)

    def _err(self, message, status=400):
        return self._json({"success": False, "message": message}, status=status)

    @property
    def _stats(self):
        return self.autodialer.stats if self.autodialer else None

    @property
    def _stats_handler(self):
        return self.autodialer.stats_handler if self.autodialer else None

    # ===== STATS =====

    async def get_stats(self, request):
        """Statistika — davr bo'yicha"""
        period = request.query.get("period", "daily")
        if not self._stats:
            return self._ok({
                "total_calls": 0, "answered_calls": 0, "unanswered_calls": 0,
                "answer_rate": 0, "total_orders": 0, "accepted_orders": 0,
                "rejected_orders": 0
            })

        s = self._stats.get_period_stats(period)
        rate = round(s.answered_calls / s.total_calls * 100, 1) if s.total_calls > 0 else 0
        return self._ok({
            "total_calls": s.total_calls,
            "answered_calls": s.answered_calls,
            "unanswered_calls": s.unanswered_calls,
            "answer_rate": rate,
            "total_orders": s.total_orders,
            "accepted_orders": s.accepted_orders,
            "rejected_orders": s.rejected_orders,
            "calls_1_attempt": s.calls_1_attempt,
            "calls_2_attempts": s.calls_2_attempts,
            "calls_3_attempts": s.calls_3_attempts,
            "accepted_without_telegram": s.accepted_without_telegram,
        })

    async def get_calls(self, request):
        """Qo'ng'iroqlar ro'yxati"""
        period = request.query.get("period", "daily")
        page = int(request.query.get("page", 1))
        page_size = int(request.query.get("page_size", 20))

        if not self._stats:
            return self._ok({"records": [], "total": 0, "page": 1, "total_pages": 0})

        s = self._stats.get_period_stats(period)
        records = list(reversed(s.call_records))  # Yangilari birinchi
        total = len(records)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        end = start + page_size
        return self._ok({
            "records": records[start:end],
            "total": total,
            "page": page,
            "total_pages": total_pages,
        })

    async def get_orders(self, request):
        """Buyurtmalar ro'yxati"""
        period = request.query.get("period", "daily")
        page = int(request.query.get("page", 1))
        page_size = int(request.query.get("page_size", 20))
        status_filter = request.query.get("status", "")

        if not self._stats:
            return self._ok({"records": [], "total": 0, "page": 1, "total_pages": 0})

        s = self._stats.get_period_stats(period)
        records = list(reversed(s.order_records))

        if status_filter:
            records = [r for r in records if r.get("result") == status_filter or r.get("order_status") == status_filter]

        total = len(records)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        end = start + page_size
        return self._ok({
            "records": records[start:end],
            "total": total,
            "page": page,
            "total_pages": total_pages,
        })

    async def get_daily_trend(self, request):
        """Kunlik trend — oxirgi N kun"""
        days = int(request.query.get("days", 7))
        if not self._stats:
            return self._ok([])

        trend = []
        today = date.today()
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            ds = d.isoformat()
            s = self._stats.get_stats_by_date(ds)
            if s:
                rate = round(s.answered_calls / s.total_calls * 100, 1) if s.total_calls > 0 else 0
                trend.append({
                    "date": ds,
                    "total_calls": s.total_calls,
                    "answered_calls": s.answered_calls,
                    "unanswered_calls": s.unanswered_calls,
                    "answer_rate": rate,
                    "total_orders": s.total_orders,
                    "accepted_orders": s.accepted_orders,
                })
            else:
                trend.append({
                    "date": ds,
                    "total_calls": 0, "answered_calls": 0, "unanswered_calls": 0,
                    "answer_rate": 0, "total_orders": 0, "accepted_orders": 0,
                })
        return self._ok(trend)

    async def get_order_statuses(self, request):
        """Buyurtma statuslari statistikasi"""
        period = request.query.get("period", "monthly")
        if not self._stats:
            return self._ok({"results": {}, "order_statuses": {}})

        s = self._stats.get_period_stats(period)
        results = {}
        order_statuses = {}
        for r in s.order_records:
            res = r.get("result", "pending")
            results[res] = results.get(res, 0) + 1
            os_val = r.get("order_status", "")
            if os_val:
                order_statuses[os_val] = order_statuses.get(os_val, 0) + 1
        return self._ok({"results": results, "order_statuses": order_statuses})

    async def get_live_orders(self, request):
        """Nonbor API dan real-time buyurtmalar (alohida HTTP session — polling buzilmaydi)"""
        if not self.autodialer or not self.autodialer.nonbor:
            return self._ok({"orders": [], "total": 0, "status_counts": {}})

        nonbor = self.autodialer.nonbor
        base_url = nonbor.base_url
        headers = dict(nonbor.headers)
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                # 1. Bizneslarni olish
                async with session.get(f"{base_url}/telegram_bot/businesses/accepted/", timeout=timeout) as resp:
                    if resp.status != 200:
                        return self._ok({"orders": [], "total": 0, "status_counts": {}})
                    biz_data = await resp.json()
                    businesses = biz_data.get("result", [])

                if not businesses:
                    return self._ok({"orders": [], "total": 0, "status_counts": {}})

                # 2. Har bir biznes uchun seller_id + buyurtmalar (parallel)
                async def fetch_biz_orders(biz):
                    biz_phone = biz.get("phone_number", "")
                    if not biz_phone:
                        return []
                    username = biz_phone.lstrip("+")

                    # Seller ID olish (cache yoki API)
                    seller_id = nonbor._seller_id_cache.get(f"+{username}")
                    if not seller_id:
                        try:
                            async with session.post(
                                f"{base_url}/telegram_bot/get_seller_info/",
                                json={"username": username}, timeout=timeout
                            ) as r:
                                if r.status == 200:
                                    d = await r.json()
                                    results = d.get("result", [])
                                    if results:
                                        seller_id = results[0].get("id")
                                        if seller_id:
                                            nonbor._seller_id_cache[f"+{username}"] = seller_id
                        except Exception:
                            return []
                    if not seller_id:
                        return []

                    # Buyurtmalarni olish
                    try:
                        async with session.get(
                            f"{base_url}/telegram_bot/sellers/{seller_id}/orders/",
                            timeout=timeout
                        ) as r:
                            if r.status != 200:
                                return []
                            d = await r.json()
                            raw_orders = d.get("result", [])
                    except Exception:
                        return []

                    result = []
                    for o in raw_orders:
                        user = o.get("user") or {}
                        result.append({
                            "id": o.get("id"),
                            "state": o.get("state", ""),
                            "seller_name": biz.get("title", ""),
                            "seller_phone": biz_phone,
                            "client_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
                            "client_phone": user.get("phone", ""),
                            "product_name": "",
                            "total_price": 0,
                            "delivery_method": "",
                            "payment_method": "",
                            "created_at": o.get("created_at", ""),
                            "updated_at": "",
                            "paid": False,
                        })
                    return result

                import asyncio as _asyncio
                all_results = await _asyncio.gather(
                    *[fetch_biz_orders(biz) for biz in businesses],
                    return_exceptions=True
                )
        except Exception as e:
            logger.error(f"live-orders xato: {e}")
            return self._ok({"orders": [], "total": 0, "status_counts": {}})

        orders = []
        for r in all_results:
            if isinstance(r, list):
                orders.extend(r)

        orders.sort(key=lambda x: x.get("id", 0), reverse=True)

        status_counts = {}
        for o in orders:
            s = o["state"]
            status_counts[s] = status_counts.get(s, 0) + 1

        return self._ok({
            "orders": orders,
            "total": len(orders),
            "status_counts": status_counts,
        })

    # ===== BUSINESSES =====

    async def get_businesses(self, request):
        """Barcha Nonbor bizneslarni ko'rsatish (guruh/config ma'lumotlari bilan)"""
        businesses = []
        if self.autodialer and self.autodialer.nonbor:
            # Nonbor API dan BARCHA bizneslarni olish
            all_biz = await self.autodialer.nonbor.get_businesses()
            for biz in all_biz:
                biz_id = biz.get("id")
                if not biz_id:
                    continue

                call_enabled = True
                group_id = ""
                config = {}
                if self._stats_handler:
                    call_enabled = self._stats_handler.is_call_enabled(biz_id)
                    group_id = self._stats_handler._business_groups.get(str(biz_id), "")
                    config = self._stats_handler.get_business_config(biz_id)

                businesses.append({
                    "id": biz_id,
                    "title": biz.get("title", ""),
                    "phone": biz.get("phone_number", "") or biz.get("phone", ""),
                    "image": biz.get("image", ""),
                    "address": biz.get("address", ""),
                    "call_enabled": call_enabled,
                    "group_id": group_id,
                    "max_call_attempts": config.get("max_call_attempts"),
                    "retry_interval": config.get("retry_interval"),
                })

        return self._ok(businesses)

    async def toggle_call(self, request):
        """Biznes uchun avtoqo'ng'iroq yoqish/o'chirish"""
        biz_id = int(request.match_info["biz_id"])
        if not self._stats_handler:
            return self._err("Stats handler mavjud emas")

        if biz_id in self._stats_handler._disabled_businesses:
            self._stats_handler._disabled_businesses.discard(biz_id)
            enabled = True
        else:
            self._stats_handler._disabled_businesses.add(biz_id)
            enabled = False

        self._stats_handler._save_call_settings()
        status = "yoqildi" if enabled else "o'chirildi"
        logger.info(f"Biznes #{biz_id} avtoqo'ng'iroq: {status}")
        return self._json({"success": True, "call_enabled": enabled})

    async def get_business_config(self, request):
        """Biznes uchun individual sozlamalar"""
        biz_id = int(request.match_info["biz_id"])
        if not self._stats_handler:
            return self._ok({})
        config = self._stats_handler.get_business_config(biz_id)
        return self._ok(config)

    async def update_business_config(self, request):
        """Biznes uchun individual sozlamalarni yangilash"""
        biz_id = int(request.match_info["biz_id"])
        if not self._stats_handler:
            return self._err("Stats handler mavjud emas")

        body = await request.json()

        # Fayldan o'qish
        data = self._stats_handler._load_call_settings_raw()
        configs = data.get("business_configs", {})
        biz_config = configs.get(str(biz_id), {})

        # Yangilash
        for key in ["max_call_attempts", "retry_interval"]:
            if key in body:
                val = body[key]
                if val is None:
                    biz_config.pop(key, None)  # null = global ga qaytarish
                else:
                    biz_config[key] = int(val)

        configs[str(biz_id)] = biz_config
        data["business_configs"] = configs

        # Saqlash
        try:
            os.makedirs(os.path.dirname(self._stats_handler._call_settings_file), exist_ok=True)
            with open(self._stats_handler._call_settings_file, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return self._err(f"Saqlash xatosi: {e}")

        logger.info(f"Biznes #{biz_id} config yangilandi: {biz_config}")
        return self._ok(biz_config)

    async def set_business_group(self, request):
        """Biznes uchun Telegram guruhni o'rnatish"""
        biz_id = int(request.match_info["biz_id"])
        if not self._stats_handler:
            return self._err("Stats handler mavjud emas")

        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        source = body.get("_source", "api")  # aylana oldini olish

        self._stats_handler._business_groups[str(biz_id)] = group_id
        self._stats_handler._save_groups()

        logger.info(f"Biznes #{biz_id} guruh o'rnatildi: {group_id}")

        # Admin panelga real-time yuborish (faqat admin paneldan kelmagan bo'lsa)
        if source != "admin":
            asyncio.create_task(self._sync_group_to_admin(biz_id, group_id))

        return self._json({"success": True, "group_id": group_id})

    async def _sync_group_to_admin(self, biz_id: int, group_id: str):
        """Admin panelga guruhni real-time yuborish"""
        admin_url = os.getenv("ADMIN_PANEL_URL", "http://localhost:8088")
        try:
            async with aiohttp.ClientSession() as session:
                await session.put(
                    f"{admin_url}/api/business-groups/{biz_id}",
                    json={"group_id": group_id, "_source": "autodialer"},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            logger.info(f"Admin panelga guruh sinxronlandi: biz={biz_id}")
        except Exception as e:
            logger.warning(f"Admin panel sinxronlash xatosi: {e}")

    async def get_business_orders(self, request):
        """Biznes uchun buyurtmalar"""
        biz_id = int(request.match_info["biz_id"])
        page = int(request.query.get("page", 1))
        page_size = 20

        if not self._stats:
            return self._ok({"records": [], "total": 0, "page": 1, "total_pages": 0, "seller_name": ""})

        # Barcha vaqt uchun buyurtmalar
        s = self._stats.get_period_stats("yearly")
        records = [r for r in reversed(s.order_records)]

        # Business ID yoki seller_name bo'yicha filter (seller_name ichida biz_id qidirish)
        # Buyurtma recordlarda biz_id yo'q, phone bo'yicha filter qilamiz
        seller_name = ""

        # Nonbor orders dan biz telefon olishga harakat qilamiz
        if self.autodialer and self.autodialer.nonbor:
            orders = await self.autodialer.nonbor.get_orders()
            if orders:
                for o in orders:
                    biz = o.get("business") or {}
                    if biz.get("id") == biz_id:
                        seller_name = biz.get("title", "")
                        phone = biz.get("phone", "")
                        if phone:
                            records = [r for r in records if r.get("seller_phone", "").endswith(phone[-9:])]
                        break

        total = len(records)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        end = start + page_size

        return self._ok({
            "records": records[start:end],
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "seller_name": seller_name,
        })

    # ===== CONFIG =====

    async def get_config(self, request):
        """Joriy konfiguratsiyani olish (real-time qiymatlar)"""
        ad = self.autodialer
        if not ad:
            return self._ok({})

        # Nonbor dan haqiqiy URL olish
        nonbor_url = ""
        if ad.nonbor and hasattr(ad.nonbor, 'base_url'):
            nonbor_url = ad.nonbor.base_url
        else:
            nonbor_url = os.getenv("NONBOR_BASE_URL", "")

        # Telegram dan haqiqiy chat_id olish
        tg_chat_id = ""
        if ad.telegram:
            tg_chat_id = ad.telegram.default_chat_id or ""

        return self._ok({
            "wait_before_call": ad.wait_before_call,
            "telegram_alert_time": ad.telegram_alert_time,
            "max_call_attempts": ad.max_call_attempts,
            "retry_interval": ad.retry_interval,
            "seller_phone": ad.seller_phone,
            "skip_asterisk": ad.skip_asterisk,
            "nonbor_base_url": nonbor_url,
            "nonbor_secret": os.getenv("NONBOR_SECRET", ""),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "")[:10] + "..." if os.getenv("TELEGRAM_BOT_TOKEN") else "",
            "telegram_chat_id": tg_chat_id,
            "ami_host": os.getenv("AMI_HOST", "127.0.0.1"),
            "ami_port": int(os.getenv("AMI_PORT", "5038")),
            "planned_reminder_time": ad.planned_reminder_time,
        })

    async def update_config(self, request):
        """Konfiguratsiyani real-time yangilash (restart kerak emas!)"""
        body = await request.json()
        ad = self.autodialer
        if not ad:
            return self._err("Autodialer ishlamayapti")

        changed = []
        env_changes = {}  # .env ga yoziladigan o'zgarishlar

        # === Vaqt sozlamalari (real-time + .env) ===
        if "wait_before_call" in body:
            ad.wait_before_call = int(body["wait_before_call"])
            env_changes["WAIT_BEFORE_CALL"] = str(ad.wait_before_call)
            changed.append(f"wait_before_call={ad.wait_before_call}")

        if "telegram_alert_time" in body:
            ad.telegram_alert_time = int(body["telegram_alert_time"])
            env_changes["TELEGRAM_ALERT_TIME"] = str(ad.telegram_alert_time)
            changed.append(f"telegram_alert_time={ad.telegram_alert_time}")

        if "max_call_attempts" in body:
            ad.max_call_attempts = int(body["max_call_attempts"])
            ad.call_manager.max_attempts = ad.max_call_attempts
            env_changes["MAX_CALL_ATTEMPTS"] = str(ad.max_call_attempts)
            changed.append(f"max_call_attempts={ad.max_call_attempts}")

        if "retry_interval" in body:
            ad.retry_interval = int(body["retry_interval"])
            ad.call_manager.retry_interval = ad.retry_interval
            env_changes["RETRY_INTERVAL"] = str(ad.retry_interval)
            changed.append(f"retry_interval={ad.retry_interval}")

        if "planned_reminder_time" in body:
            ad.planned_reminder_time = int(body["planned_reminder_time"])
            env_changes["PLANNED_REMINDER_TIME"] = str(ad.planned_reminder_time)
            changed.append(f"planned_reminder_time={ad.planned_reminder_time}")

        # === Telegram sozlamalari (real-time) ===
        if "telegram_chat_id" in body:
            new_chat_id = str(body["telegram_chat_id"]).strip()
            if ad.telegram:
                ad.telegram.default_chat_id = new_chat_id
            env_changes["TELEGRAM_CHAT_ID"] = new_chat_id
            changed.append(f"telegram_chat_id={new_chat_id}")

        # === Nonbor API sozlamalari (real-time) ===
        if "nonbor_base_url" in body:
            new_url = str(body["nonbor_base_url"]).strip()
            if ad.nonbor:
                ad.nonbor.base_url = new_url
            env_changes["NONBOR_BASE_URL"] = new_url
            os.environ["NONBOR_BASE_URL"] = new_url
            changed.append(f"nonbor_base_url={new_url}")

        if "nonbor_secret" in body:
            new_secret = str(body["nonbor_secret"]).strip()
            if ad.nonbor and hasattr(ad.nonbor, 'headers'):
                ad.nonbor.headers["X-Telegram-Bot-Secret"] = new_secret
            env_changes["NONBOR_SECRET"] = new_secret
            os.environ["NONBOR_SECRET"] = new_secret
            changed.append("nonbor_secret=***")

        # === Seller telefon ===
        if "seller_phone" in body:
            ad.seller_phone = str(body["seller_phone"]).strip()
            env_changes["SELLER_PHONE"] = ad.seller_phone
            changed.append(f"seller_phone={ad.seller_phone}")

        # === .env faylni yangilash (restart bo'lganda ham saqlansin) ===
        if env_changes:
            self._update_env_file(env_changes)

        if changed:
            logger.info(f"Config yangilandi (real-time): {', '.join(changed)}")
            return self._json({"success": True, "message": f"Yangilandi: {', '.join(changed)}"})

        return self._json({"success": True, "message": "O'zgarish yo'q"})

    def _update_env_file(self, changes: dict):
        """
        .env faylni yangilash — mavjud qiymatlarni o'zgartiradi,
        yangilarini qo'shadi. Restart bo'lganda ham saqlanadi.
        """
        env_path = PROJECT_ROOT / ".env"
        try:
            lines = []
            if env_path.exists():
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            updated_keys = set()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in changes:
                        new_lines.append(f"{key}={changes[key]}\n")
                        updated_keys.add(key)
                        continue
                new_lines.append(line)

            # Yangi kalitlarni qo'shish
            for key, val in changes.items():
                if key not in updated_keys:
                    new_lines.append(f"{key}={val}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            logger.info(f".env yangilandi: {list(changes.keys())}")
        except Exception as e:
            logger.error(f".env yangilashda xato: {e}")

    # ===== SERVICE CONTROL =====

    async def get_service_status(self, request):
        """Servis holati"""
        ad = self.autodialer
        uptime = ""
        if ad and ad._running:
            status = "running"
        else:
            status = "stopped"

        # Systemd orqali tekshirish (Linux da)
        pid = os.getpid()

        return self._ok({
            "status": status,
            "pid": pid,
            "uptime": uptime,
            "skip_asterisk": ad.skip_asterisk if ad else False,
            "polling_interval": 5,
        })

    async def control_service(self, request):
        """Servisni boshqarish (start/stop/restart)"""
        action = request.match_info["action"]

        if action == "restart":
            if os.name != "nt":
                # Systemd orqali restart (faqat Linux da)
                try:
                    result = subprocess.run(
                        ["sudo", "systemctl", "restart", "autodialer"],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        return self._json({"success": True, "message": "Servis qayta ishga tushirildi"})
                    return self._err(f"Restart xatosi: {result.stderr}")
                except Exception as e:
                    return self._err(f"Restart xatosi: {e}")
            else:
                # Windows da stop + start
                if self.autodialer:
                    await self.autodialer.stop()
                    await asyncio.sleep(1)
                    asyncio.create_task(self.autodialer.start())
                    return self._json({"success": True, "message": "Servis qayta ishga tushirilmoqda..."})
                return self._err("Autodialer obyekti topilmadi")

        elif action == "start":
            if self.autodialer and self.autodialer._running:
                return self._json({"success": True, "message": "Servis allaqachon ishlayapti"})
            if self.autodialer:
                asyncio.create_task(self.autodialer.start())
                return self._json({"success": True, "message": "Servis ishga tushirilmoqda..."})
            return self._err("Autodialer obyekti topilmadi")

        elif action == "stop":
            if self.autodialer:
                asyncio.create_task(self.autodialer.stop())
                return self._json({"success": True, "message": "Servis to'xtatilmoqda..."})
            return self._err("Autodialer allaqachon to'xtatilgan")

        return self._err(f"Noma'lum action: {action}")

    # ===== LOGS =====

    async def get_logs(self, request):
        """Oxirgi log yozuvlarini olish"""
        lines = int(request.query.get("lines", 100))
        log_file = LOGS_DIR / "autodialer.log"

        if not log_file.exists():
            return self._json({"success": True, "logs": []})

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return self._json({"success": True, "logs": all_lines[-lines:]})
        except Exception as e:
            return self._json({"success": True, "logs": [f"Log o'qish xatosi: {e}"]})

    # ===== TELEGRAM =====

    async def get_chat_info(self, request):
        """Telegram chat ma'lumotlari"""
        chat_id = request.query.get("chat_id", "")
        if not chat_id:
            return self._err("chat_id kerak")

        if not self.autodialer or not self.autodialer.telegram:
            return self._err("Telegram servis mavjud emas")

        try:
            info = await self.autodialer.telegram.get_chat_info(chat_id)
            if info:
                return self._json({
                    "success": True,
                    "title": info.get("title", ""),
                    "type": info.get("type", ""),
                })
            return self._json({"success": False, "message": "Chat topilmadi"})
        except Exception as e:
            return self._json({"success": False, "message": str(e)})

    # ===== CALL BUSINESS (AI trigger) =====

    async def call_business(self, request):
        """
        Biznes ID bo'yicha qo'ng'iroq qilish.

        POST /api/autodialer/call-business
        Body: {"business_id": 24}

        Nonbor API dan o'sha biznesning telefon raqami va tilini olib,
        TTS audio generatsiya qilib Asterisk orqali qo'ng'iroq qiladi.
        """
        try:
            body = await request.json()
        except Exception:
            return self._err("JSON tanasi noto'g'ri")

        biz_id = body.get("business_id")
        if not biz_id:
            return self._err("business_id talab qilinadi")

        if not self.autodialer:
            return self._err("Autodialer mavjud emas", status=503)

        try:
            result = await self.autodialer.call_business_by_id(int(biz_id))
        except Exception as e:
            logger.error(f"call_business xato: {e}")
            return self._err(str(e), status=500)

        if result.get("success"):
            return self._ok(result)
        else:
            return self._err(result.get("error", "Noma'lum xato"))

    # ===== HEALTH =====

    async def health(self, request):
        """Health check"""
        return self._json({
            "status": "ok",
            "service": "autodialer-api",
            "running": self.autodialer._running if self.autodialer else False,
        })

    # ===== START / STOP =====

    async def start(self):
        """HTTP serverni ishga tushirish"""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        logger.info(f"API server ishga tushdi: http://0.0.0.0:{self.port}")

    async def stop(self):
        """HTTP serverni to'xtatish"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("API server to'xtatildi")
