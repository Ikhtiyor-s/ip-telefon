"""
Admin Call Service
Yangi biznes CHECKING ga tushganda adminlarga qo'ng'iroq
Kunlik hisobot - har kuni belgilangan vaqtda
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "admin_phones": [],
    "call_mode": "sequential",  # "sequential" yoki "parallel"
    "wait_before_call": 300,    # 5 daqiqa - shuncha vaqt o'tib hali CHECKING bo'lsa qo'ng'iroq
    "max_call_attempts": 2,
    "retry_interval": 30,
    "daily_report_enabled": False,
    "daily_report_time": "08:00",
    "daily_report_language": "uz",
    "new_business_call_enabled": True,
    "new_business_call_language": "uz",
    "work_hours_start": "08:00",
    "work_hours_end": "22:00",
    "known_checking_biz_ids": [],
    # Nonbor API health monitoring
    "api_health_enabled": True,
    "api_health_check_interval": 60,    # har 60 soniyada tekshirish
    "api_health_call_interval": 300,    # API o'chiq bo'lganda har 5 daqiqada qayta qo'ng'iroq
    "api_health_timeout": 10,           # HTTP timeout soniyada
    "api_health_language": "uz",
}


class AdminCallService:
    """Admin qo'ng'iroq servisi"""

    def __init__(self, tts, call_manager, nonbor, skip_asterisk: bool, data_dir: str = "data"):
        self.tts = tts
        self.call_manager = call_manager
        self.nonbor = nonbor
        self.skip_asterisk = skip_asterisk

        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / "admin_call_config.json"
        self.config = self._load_config()

        # State
        self._known_biz_ids = set(self.config.get("known_checking_biz_ids", []))
        self._check_task: Optional[asyncio.Task] = None
        self._daily_task: Optional[asyncio.Task] = None
        self._api_health_task: Optional[asyncio.Task] = None
        self._running = False
        self._night_new_count = 0  # Tunda topilgan yangi bizneslar soni

        # API health state
        self._api_down: bool = False
        self._api_down_since: Optional[datetime] = None
        self._api_last_called_at: Optional[datetime] = None

        logger.info(f"AdminCallService yaratildi: {len(self.config['admin_phones'])} ta admin raqam")

    # ── Config ──

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                config = {**DEFAULT_CONFIG, **saved}
                return config
            except Exception as e:
                logger.error(f"Admin config yuklash xatosi: {e}")
        return {**DEFAULT_CONFIG}

    def _save_config(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.config["known_checking_biz_ids"] = list(self._known_biz_ids)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Admin config saqlash xatosi: {e}")

    def get_config(self) -> dict:
        return {k: v for k, v in self.config.items() if k != "known_checking_biz_ids"}

    def _is_work_hours(self) -> bool:
        """Ish vaqtida ekanligini tekshirish"""
        now = datetime.now()
        start_str = self.config.get("work_hours_start", "08:00")
        end_str = self.config.get("work_hours_end", "22:00")
        try:
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            start_min = sh * 60 + sm
            end_min = eh * 60 + em
            now_min = now.hour * 60 + now.minute
            return start_min <= now_min < end_min
        except Exception:
            return True  # Xato bo'lsa doim ishlaydi

    def update_config(self, updates: dict):
        for key in ["call_mode", "wait_before_call", "max_call_attempts", "retry_interval",
                     "daily_report_enabled", "daily_report_time", "daily_report_language",
                     "new_business_call_enabled", "new_business_call_language",
                     "work_hours_start", "work_hours_end"]:
            if key in updates:
                self.config[key] = updates[key]
        self._save_config()
        logger.info(f"Admin config yangilandi: {updates}")

        # Tasklar avtomatik qayta boshlanishi
        asyncio.ensure_future(self._restart_tasks())

    def add_admin_phone(self, phone: str, name: str = "", lang: str = "uz") -> bool:
        phones = self.config["admin_phones"]
        for p in phones:
            if p["phone"] == phone:
                return False
        phones.append({"phone": phone, "name": name, "enabled": True, "lang": lang or "uz"})
        self._save_config()
        logger.info(f"Admin raqam qo'shildi: {phone} ({name})")
        return True

    def remove_admin_phone(self, phone: str) -> bool:
        phones = self.config["admin_phones"]
        before = len(phones)
        self.config["admin_phones"] = [p for p in phones if p["phone"] != phone]
        if len(self.config["admin_phones"]) < before:
            self._save_config()
            logger.info(f"Admin raqam o'chirildi: {phone}")
            return True
        return False

    def get_admin_phones(self) -> list:
        return self.config["admin_phones"]

    def _get_enabled_phones(self) -> List[str]:
        return [p["phone"] for p in self.config["admin_phones"] if p.get("enabled", True)]

    # ── Lifecycle ──

    async def start(self):
        self._running = True
        await self._start_tasks()

    async def _start_tasks(self):
        """Configga qarab tasklar boshlash"""
        if self.config.get("new_business_call_enabled", True):
            if not self._check_task or self._check_task.done():
                self._check_task = asyncio.create_task(self._business_check_loop())
                logger.info("Admin: yangi biznes tekshirish boshlandi")
        if self.config.get("daily_report_enabled", False):
            if not self._daily_task or self._daily_task.done():
                self._daily_task = asyncio.create_task(self._daily_report_loop())
                logger.info(f"Admin: kunlik hisobot boshlandi ({self.config['daily_report_time']})")
        if self.config.get("api_health_enabled", True):
            if not self._api_health_task or self._api_health_task.done():
                self._api_health_task = asyncio.create_task(self._api_health_loop())
                logger.info("Admin: Nonbor API health monitoring boshlandi")

    def _stop_task(self, task):
        if task and not task.done():
            task.cancel()

    async def _restart_tasks(self):
        """Config o'zgarganda tasklar qayta boshlash"""
        # Yangi biznes tekshirish
        if self.config.get("new_business_call_enabled", True):
            if not self._check_task or self._check_task.done():
                self._check_task = asyncio.create_task(self._business_check_loop())
                logger.info("Admin: yangi biznes tekshirish YOQILDI")
        else:
            if self._check_task and not self._check_task.done():
                self._check_task.cancel()
                logger.info("Admin: yangi biznes tekshirish O'CHIRILDI")

        # Kunlik hisobot
        if self.config.get("daily_report_enabled", False):
            if self._daily_task and not self._daily_task.done():
                self._daily_task.cancel()
            self._daily_task = asyncio.create_task(self._daily_report_loop())
            logger.info(f"Admin: kunlik hisobot YANGILANDI ({self.config['daily_report_time']})")
        else:
            if self._daily_task and not self._daily_task.done():
                self._daily_task.cancel()
                logger.info("Admin: kunlik hisobot O'CHIRILDI")

        # API health monitoring
        if self.config.get("api_health_enabled", True):
            if not self._api_health_task or self._api_health_task.done():
                self._api_health_task = asyncio.create_task(self._api_health_loop())
                logger.info("Admin: API health monitoring YOQILDI")
        else:
            if self._api_health_task and not self._api_health_task.done():
                self._api_health_task.cancel()
                self._api_down = False
                self._api_down_since = None
                logger.info("Admin: API health monitoring O'CHIRILDI")

    async def stop(self):
        self._running = False
        self._stop_task(self._check_task)
        self._stop_task(self._daily_task)
        self._stop_task(self._api_health_task)

    # ── Yangi biznes tekshirish ──

    async def _business_check_loop(self):
        """Har 30s da CHECKING bizneslarni tekshirish"""
        await asyncio.sleep(10)  # Startup kutish
        logger.info("Admin: check loop boshlandi")
        while self._running:
            try:
                await self._check_new_businesses()
            except asyncio.CancelledError:
                logger.info("Admin: check loop bekor qilindi")
                break
            except Exception as e:
                logger.error(f"Admin biznes tekshirish xatosi: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _check_new_businesses(self):
        """CHECKING bizneslarni kuzatish.
        Yangi biznes CHECKING ga tushganda wait_before_call (5 daqiqa) kutadi,
        keyin hali CHECKING holatida bo'lsa adminga qo'ng'iroq qiladi.
        """
        checking = await self.nonbor.get_checking_businesses()
        current_ids = {b.get("id") for b in checking if b.get("id")}

        # Yangi CHECKING bizneslar
        new_ids = current_ids - self._known_biz_ids

        # Hozirgi CHECKING larni saqlash
        self._known_biz_ids = current_ids
        self._save_config()

        if not new_ids:
            return

        logger.info(f"Admin: {len(new_ids)} ta yangi CHECKING biznes topildi: {new_ids}")

        # Ish vaqtini tekshirish
        if not self._is_work_hours():
            self._night_new_count += len(new_ids)
            logger.info(f"Admin: ish vaqti emas, tunda {self._night_new_count} ta yangi biznes to'plandi")
            return

        wait = self.config.get("wait_before_call", 300)
        logger.info(f"Admin: {wait}s ({wait//60} daqiqa) kutish - status o'zgarmasa qo'ng'iroq qilinadi...")
        await asyncio.sleep(wait)

        # Kutish tugagandan keyin - hali CHECKING da turgan yangi bizneslarni tekshirish
        still_checking = await self.nonbor.get_checking_businesses()
        still_ids = {b.get("id") for b in still_checking if b.get("id")}

        # Faqat hali CHECKING da turgan yangi bizneslar uchun qo'ng'iroq
        still_new = new_ids & still_ids
        if not still_new:
            logger.info(f"Admin: {len(new_ids)} ta yangi biznes {wait}s ichida tekshirildi, qo'ng'iroq kerak emas")
            return

        logger.info(f"Admin: {len(still_new)} ta biznes {wait}s o'tib hali CHECKING da - qo'ng'iroq qilinadi")
        await self._call_admin_new_business(len(still_ids))

    async def _call_admin_new_business(self, checking_count: int):
        lang = self.config.get("new_business_call_language", "uz")
        audio = await self.tts.generate_admin_new_business(checking_count, lang=lang)
        if not audio:
            logger.error("Admin: TTS audio yaratilmadi")
            return

        logger.info(f"Admin qo'ng'iroq: {checking_count} ta biznes hali tekshiruvda, til: {lang}")
        await self._call_admins(str(audio), "yangi_biznes")

    # ── Kunlik hisobot ──

    async def _daily_report_loop(self):
        """Har kuni belgilangan vaqtda hisobot qo'ng'iroq"""
        while self._running:
            try:
                now = datetime.now()
                time_str = self.config.get("daily_report_time", "08:00")
                hour, minute = map(int, time_str.split(":"))
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                logger.info(f"Admin: keyingi kunlik hisobot {target.strftime('%Y-%m-%d %H:%M')}, {wait_seconds:.0f}s qoldi")
                await asyncio.sleep(wait_seconds)

                if not self._running:
                    logger.info("Admin: kunlik hisobot - servis to'xtatilgan")
                    break

                logger.info("Admin: kunlik hisobot vaqti keldi, yuborilmoqda...")
                await self._send_daily_report()

            except asyncio.CancelledError:
                logger.info("Admin: kunlik hisobot task bekor qilindi")
                break
            except Exception as e:
                logger.error(f"Admin kunlik hisobot xatosi: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _send_daily_report(self):
        # CHECKING bizneslar va mahsulotlar
        biz_count = await self.nonbor.get_checking_businesses_count()
        product_count = await self.nonbor.get_checking_products_count()

        lang = self.config.get("daily_report_language", "uz")
        audio = await self.tts.generate_admin_daily_report(biz_count, product_count, lang=lang)

        if not audio:
            logger.error("Admin: kunlik hisobot TTS xatosi")
            return

        logger.info(f"Admin kunlik hisobot: {biz_count} checking biznes, {product_count} checking mahsulot")
        await self._call_admins(str(audio), "kunlik_hisobot")

    # ── Qo'ng'iroq logikasi ──

    async def _call_admins(self, audio_path: str, purpose: str):
        """Adminlarga qo'ng'iroq - sequential yoki parallel"""
        if self.skip_asterisk:
            logger.info(f"Admin qo'ng'iroq o'tkazib yuborildi (skip_asterisk): {purpose}")
            return

        phones = self._get_enabled_phones()
        if not phones:
            return

        mode = self.config.get("call_mode", "sequential")
        if mode == "parallel":
            await self._call_parallel(phones, audio_path, purpose)
        else:
            await self._call_sequential(phones, audio_path, purpose)

    async def _call_sequential(self, phones: List[str], audio_path: str, purpose: str):
        """Ketma-ket qo'ng'iroq: birinchisi javob bermasa keyingisiga"""
        max_attempts = self.config.get("max_call_attempts", 2)
        retry_interval = self.config.get("retry_interval", 30)

        for phone in phones:
            logger.info(f"Admin qo'ng'iroq [{purpose}]: {phone}")
            result = await self.call_manager.make_call_with_retry(
                phone_number=phone,
                audio_file=audio_path,
                max_attempts_override=max_attempts,
                retry_interval_override=retry_interval,
            )
            if result and result.is_answered:
                logger.info(f"Admin qo'ng'iroq [{purpose}]: {phone} - JAVOB BERILDI")
                return
            logger.info(f"Admin qo'ng'iroq [{purpose}]: {phone} - javob berilmadi, keyingisi...")

        logger.warning(f"Admin qo'ng'iroq [{purpose}]: hech kim javob bermadi")

    async def _call_parallel(self, phones: List[str], audio_path: str, purpose: str):
        """Barcha adminlarga bir vaqtda qo'ng'iroq"""
        logger.info(f"Admin parallel qo'ng'iroq [{purpose}]: {len(phones)} ta raqam")

        async def call_one(phone):
            result = await self.call_manager.make_call_with_retry(
                phone_number=phone,
                audio_file=audio_path,
                max_attempts_override=self.config.get("max_call_attempts", 2),
                retry_interval_override=self.config.get("retry_interval", 30),
            )
            answered = result and result.is_answered
            logger.info(f"Admin [{purpose}]: {phone} - {'JAVOB' if answered else 'javobsiz'}")
            return answered

        results = await asyncio.gather(*[call_one(p) for p in phones])
        answered = sum(1 for r in results if r)
        logger.info(f"Admin parallel [{purpose}]: {answered}/{len(phones)} javob berdi")

    # ── Nonbor API Health Monitoring ──

    async def _api_health_loop(self):
        """Har N soniyada Nonbor API ni tekshirish. Javob bermasa adminga qo'ng'iroq."""
        await asyncio.sleep(30)  # Startup kutish
        logger.info("Admin: API health loop boshlandi")

        while self._running:
            try:
                await self._check_nonbor_api()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"API health check xatosi: {e}", exc_info=True)

            interval = self.config.get("api_health_check_interval", 60)
            await asyncio.sleep(interval)

    async def _check_nonbor_api(self):
        """Nonbor API ga so'rov yuborib, javob bor-yo'qligini tekshirish."""
        url = os.getenv("NONBOR_BASE_URL", "").rstrip("/")
        if not url:
            return

        # Health URL: /api/v2/... → domain/api/v2/telegram_bot/businesses/accepted/
        check_url = f"{url}/telegram_bot/businesses/accepted/"
        secret = os.getenv("NONBOR_SECRET", "")
        timeout = aiohttp.ClientTimeout(total=self.config.get("api_health_timeout", 10))

        is_ok = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    check_url,
                    headers={"X-Telegram-Bot-Secret": secret},
                    timeout=timeout,
                ) as resp:
                    is_ok = resp.status < 500
        except Exception as e:
            logger.warning(f"API health check muvaffaqiyatsiz: {e}")
            is_ok = False

        if is_ok:
            if self._api_down:
                # Tiklandi
                down_minutes = self._down_minutes()
                logger.info(f"Nonbor API TIKLANDI — {down_minutes} daqiqa o'chiq edi")
                self._api_down = False
                self._api_down_since = None
                self._api_last_called_at = None
        else:
            if not self._api_down:
                # Yangi xato — birinchi marta
                self._api_down = True
                self._api_down_since = datetime.now()
                logger.error(f"Nonbor API JAVOB BERMAYAPTI — qo'ng'iroq boshlanadi")
                await self._call_api_down()
            else:
                # Hali ham o'chiq — qayta qo'ng'iroq intervalini tekshirish
                call_interval = self.config.get("api_health_call_interval", 300)
                if self._api_last_called_at is None or \
                   (datetime.now() - self._api_last_called_at).total_seconds() >= call_interval:
                    down_minutes = self._down_minutes()
                    logger.warning(f"Nonbor API hali o'chiq ({down_minutes} daqiqa) — qayta qo'ng'iroq")
                    await self._call_api_down()

    def _down_minutes(self) -> int:
        """API qancha daqiqadan beri o'chiq"""
        if not self._api_down_since:
            return 0
        return max(1, int((datetime.now() - self._api_down_since).total_seconds() / 60))

    async def _call_api_down(self):
        """Adminga API o'chiq degan qo'ng'iroq.

        Agar AUTODIALER_NOTIFY_URL sozlangan bo'lsa — Asterisk serveridagi
        webhook listener'ga POST yuboriladi (u o'zi qo'ng'iroq qiladi).
        Aks holda — to'g'ridan-to'g'ri AMI orqali qo'ng'iroq.
        """
        phones = self._get_enabled_phones()
        if not phones:
            logger.warning("API down: admin raqamlar yo'q, qo'ng'iroq qilinmadi")
            return

        self._api_last_called_at = datetime.now()
        minutes = self._down_minutes()

        notify_url = os.getenv("AUTODIALER_NOTIFY_URL", "").strip()
        if notify_url:
            await self._notify_asterisk_server(notify_url, phones, minutes)
        else:
            # To'g'ridan-to'g'ri AMI orqali
            lang = self.config.get("api_health_language", "uz")
            audio = await self.tts.generate_api_down_message(minutes, lang=lang)
            if not audio:
                logger.error("API down: TTS audio yaratilmadi")
                return
            self.ensure_for_asterisk_if_available(audio)
            logger.info(f"Admin API-down qo'ng'iroq (AMI): {minutes} daqiqa, {len(phones)} ta raqam")
            await self._call_admins(str(audio), "api_down")

    async def _notify_asterisk_server(self, url: str, phones: list, minutes: int):
        """Asterisk serveridagi webhook listener'ga event yuborish."""
        secret = os.getenv("WEBHOOK_SECRET", os.getenv("NONBOR_SECRET", ""))
        payload = {
            "event": "api_down",
            "admin_phones": phones,
            "down_minutes": minutes,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers={"X-Webhook-Secret": secret},
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        called = data.get("called", 0)
                        logger.info(f"Asterisk server qo'ng'iroq qildi: {called}/{len(phones)} ta")
                    else:
                        logger.error(f"Asterisk server xatosi: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Asterisk server bilan bog'lanib bo'lmadi: {e}")

    def ensure_for_asterisk_if_available(self, audio_path):
        """TTS faylni Asterisk pathga ko'chirish (mavjud bo'lsa)"""
        try:
            self.tts.ensure_for_asterisk(audio_path)
        except Exception:
            pass

    # ── Test qo'ng'iroq ──

    async def test_call(self, lang: str = None) -> dict:
        """Test qo'ng'iroq - haqiqiy CHECKING ma'lumotlar bilan"""
        phones = self._get_enabled_phones()
        if not phones:
            return {"success": False, "error": "Admin raqamlar yo'q"}

        lang = lang or self.config.get("new_business_call_language", "uz")

        try:
            biz_count = await self.nonbor.get_checking_businesses_count()
            product_count = await self.nonbor.get_checking_products_count()
        except Exception as e:
            logger.warning(f"Test call: ma'lumot olishda xato: {e}")
            biz_count = 0
            product_count = 0

        audio = await self.tts.generate_admin_daily_report(biz_count, product_count, lang=lang)
        if not audio:
            return {"success": False, "error": "TTS audio yaratilmadi"}

        if self.skip_asterisk:
            return {"success": True, "message": "Skip asterisk rejimda", "phones": phones,
                    "checking_biz": biz_count, "checking_products": product_count}

        await self._call_admins(str(audio), "test")
        return {"success": True, "phones": phones,
                "checking_biz": biz_count, "checking_products": product_count}
