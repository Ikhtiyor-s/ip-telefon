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

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "admin_phones": [],
    "call_mode": "sequential",  # "sequential" yoki "parallel"
    "wait_before_call": 60,
    "max_call_attempts": 2,
    "retry_interval": 30,
    "daily_report_enabled": False,
    "daily_report_time": "08:00",
    "daily_report_language": "uz",
    "new_business_call_enabled": True,
    "new_business_call_language": "uz",
    "known_checking_biz_ids": [],
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
        self._running = False

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

    def update_config(self, updates: dict):
        for key in ["call_mode", "wait_before_call", "max_call_attempts", "retry_interval",
                     "daily_report_enabled", "daily_report_time", "daily_report_language",
                     "new_business_call_enabled", "new_business_call_language"]:
            if key in updates:
                self.config[key] = updates[key]
        self._save_config()
        logger.info(f"Admin config yangilandi: {updates}")

    def add_admin_phone(self, phone: str, name: str = "") -> bool:
        phones = self.config["admin_phones"]
        for p in phones:
            if p["phone"] == phone:
                return False
        phones.append({"phone": phone, "name": name, "enabled": True})
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
        if self.config.get("new_business_call_enabled", True):
            self._check_task = asyncio.create_task(self._business_check_loop())
            logger.info("Admin: yangi biznes tekshirish boshlandi")
        if self.config.get("daily_report_enabled", False):
            self._daily_task = asyncio.create_task(self._daily_report_loop())
            logger.info(f"Admin: kunlik hisobot boshlandi ({self.config['daily_report_time']})")

    async def stop(self):
        self._running = False
        for task in [self._check_task, self._daily_task]:
            if task and not task.done():
                task.cancel()

    # ── Yangi biznes tekshirish ──

    async def _business_check_loop(self):
        """Har 30s da CHECKING bizneslarni tekshirish"""
        await asyncio.sleep(10)  # Startup kutish
        while self._running:
            try:
                await self._check_new_businesses()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Admin biznes tekshirish xatosi: {e}")
            await asyncio.sleep(30)

    async def _check_new_businesses(self):
        # 1. Avval checking endpoint ni sinab ko'rish
        checking = await self.nonbor.get_checking_businesses()
        if checking:
            # CHECKING endpoint ishlaydi
            current_ids = {b.get("id") for b in checking if b.get("id")}
            new_ids = current_ids - self._known_biz_ids
            if new_ids:
                logger.info(f"Admin: {len(new_ids)} ta yangi CHECKING biznes: {new_ids}")
                self._known_biz_ids = current_ids
                self._save_config()
                wait = self.config.get("wait_before_call", 60)
                if wait > 0:
                    await asyncio.sleep(wait)
                await self._call_admin_new_business(len(current_ids))
            else:
                removed = self._known_biz_ids - current_ids
                if removed:
                    self._known_biz_ids = current_ids
                    self._save_config()
            return

        # 2. Fallback: accepted bizneslar kuzatish
        accepted = await self.nonbor.get_all_businesses()
        if not accepted:
            return
        current_ids = {b.get("id") for b in accepted if b.get("id")}
        new_ids = current_ids - self._known_biz_ids

        if not self._known_biz_ids:
            # Birinchi ishga tushish - hamma ID larni yozib olish
            self._known_biz_ids = current_ids
            self._save_config()
            logger.info(f"Admin: boshlang'ich {len(current_ids)} ta biznes kuzatuvga olindi")
            return

        if new_ids:
            logger.info(f"Admin: {len(new_ids)} ta yangi biznes topildi: {new_ids}")
            self._known_biz_ids = current_ids
            self._save_config()
            wait = self.config.get("wait_before_call", 60)
            if wait > 0:
                await asyncio.sleep(wait)
            await self._call_admin_new_business(len(new_ids))

    async def _call_admin_new_business(self, checking_count: int):
        phones = self._get_enabled_phones()
        if not phones:
            logger.warning("Admin: raqamlar yo'q, qo'ng'iroq qilinmadi")
            return

        lang = self.config.get("new_business_call_language", "uz")
        audio = await self.tts.generate_admin_new_business(checking_count, lang=lang)
        if not audio:
            logger.error("Admin: TTS audio yaratilmadi")
            return

        logger.info(f"Admin qo'ng'iroq: yangi biznes, {checking_count} ta tekshiruvda, til: {lang}")
        await self._call_admins(str(audio), "yangi_biznes")

    # ── Kunlik hisobot ──

    async def _daily_report_loop(self):
        """Har kuni belgilangan vaqtda hisobot qo'ng'iroq"""
        while self._running:
            try:
                # Keyingi hisobot vaqtini hisoblash
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
                    break

                await self._send_daily_report()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Admin kunlik hisobot xatosi: {e}")
                await asyncio.sleep(60)

    async def _send_daily_report(self):
        phones = self._get_enabled_phones()
        if not phones:
            logger.warning("Admin: kunlik hisobot - raqamlar yo'q")
            return

        # Ma'lumotlar olish
        checking_biz = await self.nonbor.get_checking_businesses()
        biz_count = len(checking_biz)
        product_count = await self.nonbor.get_checking_products_count()

        lang = self.config.get("daily_report_language", "uz")
        audio = await self.tts.generate_admin_daily_report(biz_count, product_count, lang=lang)
        if not audio:
            logger.error("Admin: kunlik hisobot TTS xatosi")
            return

        logger.info(f"Admin kunlik hisobot: {biz_count} biznes, {product_count} mahsulot tekshiruvda")
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

    # ── Test qo'ng'iroq ──

    async def test_call(self, lang: str = None) -> dict:
        """Test qo'ng'iroq - haqiqiy CHECKING ma'lumotlar bilan"""
        phones = self._get_enabled_phones()
        if not phones:
            return {"success": False, "error": "Admin raqamlar yo'q"}

        lang = lang or self.config.get("new_business_call_language", "uz")

        # Haqiqiy CHECKING bizneslar va mahsulotlar sonini olish
        try:
            checking_biz = await self.nonbor.get_checking_businesses()
            biz_count = len(checking_biz)
            product_count = await self.nonbor.get_checking_products_count()
        except Exception as e:
            logger.warning(f"Test call: CHECKING ma'lumotlar olishda xato: {e}")
            biz_count = 0
            product_count = 0

        # Hisobot audio yaratish
        audio = await self.tts.generate_admin_daily_report(biz_count, product_count, lang=lang)
        if not audio:
            return {"success": False, "error": "TTS audio yaratilmadi"}

        if self.skip_asterisk:
            return {"success": True, "message": "Skip asterisk rejimda", "phones": phones,
                    "biz_count": biz_count, "product_count": product_count}

        await self._call_admins(str(audio), "test")
        return {"success": True, "phones": phones,
                "biz_count": biz_count, "product_count": product_count}
