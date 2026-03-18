"""
Autodialer Pro - Asosiy Servis
================================================================================

Professional autodialer tizimi - Nonbor API buyurtmalarini kuzatish va
sotuvchilarga avtomatik qo'ng'iroq qilish

Jarayon:
1. Yangi buyurtma (TEKSHIRILMOQDA) keladi
2. 1.5 daqiqa kutish
3. Sotuvchiga qo'ng'iroq: "Sizda N ta yangi buyurtma bor"
4. Javob bo'lmasa → yana qo'ng'iroq (max 2 marta)
5. 3 daqiqada Telegram xabar
6. Status o'zgarsa → Telegram xabar o'chiriladi

================================================================================
"""

import asyncio
import logging
import signal
import sys
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from pathlib import Path
from collections import OrderedDict
from dotenv import load_dotenv

# .env yuklash
load_dotenv(Path(__file__).parent.parent / ".env")

from services import (
    TTSService,
    NonborService,
    NonborPoller,
    AsteriskAMI,
    CallManager,
    CallStatus,
    TelegramService,
    TelegramNotificationManager,
    TelegramStatsHandler,
    StatsService,
    StatsCallResult,
    OrderResult
)
from api_server import AutodialerAPI

# Logging - UTF-8 encoding (Windows cp1251 muammosini hal qilish)
import sys as _sys
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_stream_handler = logging.StreamHandler()
_stream_handler.stream = open(_sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
_file_handler = logging.FileHandler("logs/autodialer.log", encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
    handlers=[_stream_handler, _file_handler]
)
logger = logging.getLogger("autodialer")


class BoundedOrderCache:
    """
    Buyurtmalar keshi - Memory leak oldini olish uchun

    Xususiyatlar:
    - Maksimal hajm chegaralangan (default: 1000 buyurtma)
    - TTL (Time To Live) - 24 soatdan keyin o'chiriladi
    - OrderedDict asosida - eng eski yozuvlar birinchi o'chiriladi
    """

    def __init__(self, max_size: int = 1000, ttl_hours: int = 24):
        """
        Args:
            max_size: Maksimal buyurtmalar soni
            ttl_hours: Buyurtma esdan chiqish vaqti (soatda)
        """
        self.cache = OrderedDict()
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
        logger.info(f"BoundedOrderCache yaratildi: max_size={max_size}, ttl={ttl_hours}h")

    def add(self, order_id: int):
        """Buyurtmani keshga qo'shish"""
        now = datetime.now()

        # Eskirgan yozuvlarni o'chirish
        expired_keys = [
            k for k, v in self.cache.items()
            if now - v > self.ttl
        ]
        for key in expired_keys:
            del self.cache[key]
            logger.debug(f"Eskirgan buyurtma o'chirildi: #{key}")

        # Hajm chegarasini nazorat qilish
        while len(self.cache) >= self.max_size:
            removed_id = self.cache.popitem(last=False)[0]  # Eng eskisini o'chirish
            logger.debug(f"Kesh to'ldi, eng eski buyurtma o'chirildi: #{removed_id}")

        # Yangi buyurtmani qo'shish
        self.cache[order_id] = now
        logger.debug(f"Buyurtma keshga qo'shildi: #{order_id}")

    def contains(self, order_id: int) -> bool:
        """Buyurtma keshda bormi tekshirish"""
        if order_id not in self.cache:
            return False

        # TTL tekshirish
        now = datetime.now()
        if now - self.cache[order_id] > self.ttl:
            del self.cache[order_id]
            logger.debug(f"Eskirgan buyurtma o'chirildi: #{order_id}")
            return False

        return True

    def size(self) -> int:
        """Kesh hajmi"""
        return len(self.cache)


class AutodialerState:
    """Autodialer holati"""

    def __init__(self):
        self.pending_orders_count: int = 0
        self.pending_order_ids: list = []  # Buyurtma IDlari
        self.last_new_order_time: Optional[datetime] = None
        self.call_attempts: int = 0
        self.call_started: bool = False  # Qo'ng'iroq jarayoni boshlandi
        self.waiting_for_call: bool = False
        self.telegram_notified: bool = False
        self.telegram_notify_time: Optional[datetime] = None
        self.new_order_ids_for_call: list = []  # Yangi qo'ng'iroq qilish uchun buyurtmalar
        self.order_timestamps: dict = {}  # {order_id: datetime} - Har bir buyurtmaning kelgan vaqti
        self.last_communicated_orders: dict = {}  # {seller_phone: [order_ids]} - Har bir sotuvchiga oxirgi marta qaysi buyurtmalar haqida xabar berilgan
        self.unanswered_sellers: set = set()  # Javob bermagan sotuvchilar — keyingi siklda faqat 1 urinish
        self.call_in_progress: bool = False  # Hozir qo'ng'iroq jarayonida
        self.global_retry_count: int = 0  # Global qayta urinish hisoblagichi - barcha buyurtmalar uchun
        self.last_telegram_order_ids: set = set()  # Oxirgi marta Telegram ga yuborilgan buyurtmalar ID lari
        self.last_180s_check_time: Optional[datetime] = None  # Oxirgi 180s timer tekshiruv vaqti
        self.last_group_status_check: Optional[datetime] = None  # Guruh xabarlari status tekshiruvi

    def reset(self):
        """Holatni tozalash - FAQAT qo'ng'iroq state, 180s timer uchun pending_order_ids saqlanadi"""
        # pending_orders_count va pending_order_ids ni SAQLAB qolamiz
        # Chunki buyurtmalar hali TEKSHIRILMOQDA da bo'lishi mumkin va 180s timer davom etishi kerak
        # self.pending_orders_count = 0  # DISABLED - 180s timer uchun
        # self.pending_order_ids = []  # DISABLED - 180s timer uchun kerak
        self.last_new_order_time = None
        self.call_attempts = 0
        self.call_started = False
        self.waiting_for_call = False
        self.telegram_notified = False
        # MUHIM: telegram_notify_time va order_timestamps ni SAQLAB qolamiz
        # 180s timer uchun kerak - buyurtmalar TEKSHIRILMOQDA da qolsa davom etadi
        # self.telegram_notify_time = None  # DISABLED - 180s timer davom etishi uchun
        self.new_order_ids_for_call = []
        # self.order_timestamps = {}  # DISABLED - 180s timer davom etishi uchun
        # MUHIM: last_communicated_orders ni SAQLAB qolamiz - takroriy qo'ng'iroqlarni oldini olish uchun
        # self.last_communicated_orders = {}  # DISABLED - eski buyurtmalar haqida qayta xabar bermaslik uchun
        self.call_in_progress = False
        self.global_retry_count = 0  # Global retry ni ham reset qilamiz
        # self.last_telegram_order_ids = set()  # DISABLED - 180s timer davom etishi uchun saqlab qolamiz

    def new_order_received(self, count: int, order_ids: list):
        """Yangi buyurtma keldi"""
        self.pending_orders_count = count
        self.pending_order_ids = order_ids

        # Agar kutish boshlanmagan YOKI (oldingi qo'ng'iroq tugagan bo'lsa VA qo'ng'iroq jarayonida EMAS)
        # MUHIM: call_in_progress tekshirish - ikki qo'ng'iroq bir vaqtda bo'lmasligi uchun
        if not self.waiting_for_call or (self.call_started and not self.call_in_progress):
            self.last_new_order_time = datetime.now()
            self.waiting_for_call = True
            self.call_started = False  # Yangi qo'ng'iroq uchun reset
            self.call_attempts = 0
            self.telegram_notified = False
            self.global_retry_count = 0  # Yangi buyurtmalar uchun global retry ni reset qilamiz
            # telegram_notify_time ni o'rnatmaymiz - 180s timer avtomatik ishlaydi


class AutodialerPro:
    """
    Autodialer Pro - Asosiy klass

    Barcha komponentlarni birlashtiradi va jarayonni boshqaradi
    """

    def __init__(
        self,
        # Sarkor SIP
        sip_host: str = "127.0.0.1",
        ami_port: int = 5038,
        ami_username: str = "autodialer",
        ami_password: str = "autodialer123",
        # Telegram
        telegram_token: str = None,
        telegram_chat_id: str = None,
        # Sotuvchi
        seller_phone: str = "+998901009300",
        # Vaqtlar
        wait_before_call: int = 90,  # 1.5 daqiqa
        telegram_alert_time: int = 180,  # 3 daqiqa
        max_call_attempts: int = 2,
        retry_interval: int = 30,  # 30 soniya (birinchi qo'ng'iroqdan keyin)
        planned_reminder_time: int = 60,  # Reja buyurtma eslatma vaqti (daqiqa)
        # Yo'llar
        audio_dir: str = "audio",
        # Platform
        skip_asterisk: bool = False,  # Windows da True - Asterisk o'tkazib yuboriladi
    ):
        # Konfiguratsiya
        self.seller_phone = seller_phone
        self.wait_before_call = wait_before_call
        self.telegram_alert_time = telegram_alert_time
        self.max_call_attempts = max_call_attempts
        self.retry_interval = retry_interval
        self.planned_reminder_time = planned_reminder_time
        self.audio_dir = Path(audio_dir)
        self.skip_asterisk = skip_asterisk

        if self.skip_asterisk:
            logger.info("⚠ ASTERISK O'TKAZIB YUBORILADI (Windows rejim - faqat Telegram)")
        else:
            logger.info("✓ ASTERISK FAOL (Linux/Production rejim - to'liq funksional)")

        self.phone_overrides = {}

        # Holat
        self.state = AutodialerState()
        self._running = False
        self._tasks = []

        # Buyurtmalar keshi - Memory leak oldini olish
        self._recorded_orders = BoundedOrderCache(max_size=1000, ttl_hours=24)

        # Guruh xabarlari - order_id -> {msg_id, biz_id, chat_id}
        self._group_order_messages: Dict[int, dict] = {}

        # Buyurtma ma'lumotlari keshi - yangi kelganda darhol olish
        self._order_data_cache: Dict[int, dict] = {}

        # Lock - guruh xabarlarini yangilashda race condition oldini olish uchun
        self._group_messages_lock = asyncio.Lock()

        # In-progress set - xabar yuborilayotgan buyurtmalar (duplicate oldini olish)
        self._sending_order_messages: set = set()

        # Guruh xabari kutayotgan yangi buyurtmalar (2s loopda yuboriladi)
        self._pending_group_message_orders: set = set()

        # Reja buyurtmalar uchun 20 daqiqa oldin eslatma yuborilgan buyurtmalar
        self._planned_reminders_sent: set = set()

        # Qo'ng'iroq urinishlari soni - HAR BIR SOTUVCHI UCHUN ALOHIDA
        # {seller_phone: call_attempts}
        self._seller_call_attempts: Dict[str, int] = {}

        # Qo'ng'iroq natijasi - HAR BIR SOTUVCHI UCHUN
        # {seller_phone: True/False} - javob berdi yoki yo'q
        self._seller_call_answered: Dict[str, bool] = {}

        # Vaqt tekshiruv markerlar - hasattr() dan qochish uchun __init__ da e'lon
        self._last_notif_check = None
        self._last_planned_check = None
        self._debug_logged = False

        # Servislar
        self.tts = TTSService(self.audio_dir, provider="edge")

        self.nonbor = NonborService(status_name="CHECKING")

        self.nonbor_poller = NonborPoller(
            nonbor_service=self.nonbor,
            polling_interval=5,
            on_new_orders=self._on_new_orders,
            on_orders_resolved=self._on_orders_resolved
        )

        self.ami = AsteriskAMI(
            host=sip_host,
            port=ami_port,
            username=ami_username,
            password=ami_password
        )

        self.call_manager = CallManager(
            ami=self.ami,
            max_attempts=max_call_attempts,
            retry_interval=retry_interval
        )

        # Statistika servisi
        # Data katalogi - loyiha ildiziga nisbatan
        project_root = Path(__file__).parent.parent
        data_dir = str(project_root / "data")

        # Guruh xabarlarini saqlash fayli
        self._group_messages_file = project_root / "data" / "group_order_messages.json"
        self._load_group_messages()

        # Reja eslatmalar fayli
        self._planned_reminders_file = project_root / "data" / "planned_reminders.json"
        self._load_planned_reminders()

        self.stats = StatsService(data_dir=data_dir)

        if telegram_token:
            self.telegram = TelegramService(
                bot_token=telegram_token,
                default_chat_id=telegram_chat_id
            )
            self.notification_manager = TelegramNotificationManager(self.telegram, data_dir=data_dir)
            self.stats_handler = TelegramStatsHandler(self.telegram, self.stats, self.nonbor)
        else:
            self.telegram = None
            self.notification_manager = None
            self.stats_handler = None

        # HTTP API server (admin panel uchun)
        api_port = int(os.getenv("API_PORT", "8585"))
        self.api_server = AutodialerAPI(autodialer=self, port=api_port)

        logger.info("AutodialerPro yaratildi")

    def _load_group_messages(self):
        """Guruh xabarlarini fayldan yuklash (restart da davom etish uchun)"""
        try:
            if self._group_messages_file.exists():
                import json
                with open(self._group_messages_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # JSON key lar string bo'lgani uchun int ga o'tkazamiz
                    loaded = {}
                    for k, v in data.items():
                        try:
                            loaded[int(k)] = v
                        except (ValueError, TypeError):
                            logger.warning(f"Noto'g'ri guruh xabar kaliti o'tkazib yuborildi: {k}")
                    self._group_order_messages = loaded
                    logger.info(f"Guruh xabarlari yuklandi: {len(self._group_order_messages)} ta buyurtma")
        except Exception as e:
            logger.error(f"Guruh xabarlarini yuklashda xato: {e}")
            self._group_order_messages = {}

    def _save_group_messages(self):
        """Guruh xabarlarini faylga saqlash"""
        try:
            import json
            # Katalog mavjudligini tekshirish
            self._group_messages_file.parent.mkdir(exist_ok=True)
            with open(self._group_messages_file, "w", encoding="utf-8") as f:
                json.dump(self._group_order_messages, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Guruh xabarlarini saqlashda xato: {e}")

    def _load_planned_reminders(self):
        """Reja eslatmalarini fayldan yuklash"""
        try:
            if self._planned_reminders_file.exists():
                import json
                with open(self._planned_reminders_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._planned_reminders_sent = set(data.get("sent", []))
                    logger.info(f"Reja eslatmalari yuklandi: {len(self._planned_reminders_sent)} ta")
        except Exception as e:
            logger.error(f"Reja eslatmalarini yuklashda xato: {e}")
            self._planned_reminders_sent = set()

    def _save_planned_reminders(self):
        """Reja eslatmalarini faylga saqlash"""
        try:
            import json
            self._planned_reminders_file.parent.mkdir(exist_ok=True)
            with open(self._planned_reminders_file, "w", encoding="utf-8") as f:
                json.dump({"sent": list(self._planned_reminders_sent)}, f, indent=2)
        except Exception as e:
            logger.error(f"Reja eslatmalarini saqlashda xato: {e}")

    async def _check_planned_reminders(self):
        """
        Reja buyurtmalar uchun 60 daqiqa (1 soat) oldin eslatma yuborish VA QO'NG'IROQ QILISH.
        Qabul qilingan (ACCEPTED/READY) reja buyurtmalarning vaqti yaqinlashganda
        biznes guruhiga eslatma xabar yuboriladi va sotuvchiga qo'ng'iroq qilinadi.
        """
        try:
            now = datetime.now(timezone(timedelta(hours=5)))  # UZ vaqt zonasi

            # Biznes bo'yicha reja buyurtmalarni yig'ish
            # {biz_id: {"chat_id": str, "orders": [...], "biz_title": str}}
            biz_planned: Dict[str, dict] = {}

            for order_id, tracked in self._group_order_messages.items():
                order_data = tracked.get("order_data", {})

                # Faqat reja buyurtmalar
                if not order_data.get("is_planned"):
                    continue

                # Faqat qabul qilingan buyurtmalar (CHECKING emas)
                status = order_data.get("status", "").upper()
                if status in ("CHECKING", "ACCEPT_EXPIRED", "CANCELLED", "CANCELLED_SELLER",
                              "CANCELLED_CLIENT", "CANCELLED_USER", "CANCELLED_ADMIN",
                              "COMPLETED", "DELIVERED", "PAYMENT_EXPIRED"):
                    continue

                # Allaqachon eslatma yuborilgan
                if order_id in self._planned_reminders_sent:
                    continue

                # planned_datetime_raw ni tekshirish
                raw_dt = order_data.get("planned_datetime_raw", "")
                if not raw_dt:
                    continue

                try:
                    planned_dt = datetime.fromisoformat(str(raw_dt).replace('Z', '+00:00'))
                    minutes_left = (planned_dt - now).total_seconds() / 60

                    # planned_reminder_time daqiqa oldin eslatma
                    if 0 <= minutes_left <= self.planned_reminder_time:
                        biz_id = tracked.get("biz_id", "")
                        chat_id = tracked.get("chat_id", "")
                        if chat_id and biz_id:
                            if biz_id not in biz_planned:
                                biz_planned[biz_id] = {
                                    "chat_id": chat_id,
                                    "orders": [],
                                    "biz_title": order_data.get("seller_name", ""),
                                }
                            biz_planned[biz_id]["orders"].append({
                                "order_id": order_id,
                                "order_number": order_data.get("order_number", ""),
                                "delivery_time": order_data.get("delivery_time", ""),
                                "product_name": order_data.get("product_name", ""),
                            })
                except Exception:
                    continue

            # Har bir biznesga eslatma yuborish + qo'ng'iroq qilish
            for biz_id, biz_data in biz_planned.items():
                chat_id = biz_data["chat_id"]
                orders = biz_data["orders"]
                count = len(orders)

                # 1. TELEGRAM XABAR yuborish (guruhga)
                text = f"⏰ <b>ESLATMA: {count} ta reja buyurtma!</b>\n"
                text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
                text += f"Sizda <b>{count}</b> ta reja bo'yicha buyurtmalaringiz bor:\n\n"

                for i, o in enumerate(orders, 1):
                    text += f"  {i}. Buyurtma #{o['order_number']}"
                    if o['delivery_time']:
                        text += f" — 📅 {o['delivery_time']}"
                    if o['product_name']:
                        text += f"\n     🏷 {o['product_name']}"
                    text += "\n"

                text += f"\n❗❗❗ {self.planned_reminder_time} daqiqa qoldi! Tayyorlashni boshlang!"

                try:
                    await self.telegram.send_message(
                        text=text, chat_id=chat_id, parse_mode="HTML"
                    )
                    logger.info(f"Reja eslatma yuborildi: chat={chat_id}, {count} ta buyurtma")
                except Exception as e:
                    logger.error(f"Reja eslatma xatosi: {e}")

                # 2. QO'NG'IROQ QILISH (agar Asterisk faol bo'lsa)
                # MUHIM: Alohida task da ishga tushirish (main loop bloklanmasin)
                if not self.skip_asterisk:
                    asyncio.create_task(self._planned_reminder_call(biz_id, count))

                # Eslatma yuborildi - barcha buyurtmalarni belgilash
                for o in orders:
                    self._planned_reminders_sent.add(o["order_id"])
                self._save_planned_reminders()

        except Exception as e:
            logger.error(f"Reja eslatma tekshirish xatosi: {e}")

    async def _planned_reminder_call(self, biz_id: str, order_count: int):
        """Reja eslatma uchun qo'ng'iroq (alohida task da ishlaydi - main loop bloklanmaydi)"""
        try:
            seller_phone = None
            seller_lang = "uz"  # Default til
            businesses = await self.nonbor.get_businesses()
            if businesses:
                for b in businesses:
                    if str(b.get("id")) == str(biz_id):
                        seller_phone = b.get("phone_number", "")
                        # Biznes egasi tilini olish
                        raw_lang = (
                            b.get("language") or
                            b.get("owner_language") or
                            b.get("tg_language") or
                            b.get("language_code") or
                            "uz"
                        )
                        seller_lang = str(raw_lang).lower()[:2]
                        break

            if not seller_phone:
                logger.warning(f"Reja eslatma: biz #{biz_id} telefon raqami topilmadi")
                return

            # Telefon raqamini formatlash
            phone_digits = ''.join(filter(str.isdigit, seller_phone))
            if len(phone_digits) == 9:
                seller_phone = f"+998{phone_digits}"
            elif len(phone_digits) == 12 and phone_digits.startswith("998"):
                seller_phone = f"+{phone_digits}"

            # Avtoqo'ng'iroq o'chirilganmi tekshirish
            biz_id_int = int(biz_id) if str(biz_id).isdigit() else None
            if biz_id_int and self.stats_handler and not self.stats_handler.is_call_enabled(biz_id_int):
                logger.info(f"Reja eslatma: biz #{biz_id} avtoqo'ng'iroq O'CHIRILGAN - qo'ng'iroq qilinmaydi")
                return

            # TTS audio yaratish (tilga qarab - barcha tillar tts_service.py da)
            audio_path = await self.tts.generate_planned_message(
                count=order_count,
                lang=seller_lang
            )
            if not audio_path:
                logger.error(f"Reja eslatma: TTS audio yaratilmadi")
                return

            result = await self.call_manager.make_call_with_retry(
                phone_number=seller_phone,
                audio_file=str(audio_path),
            )
            if result and result.is_answered:
                logger.info(f"Reja eslatma qo'ng'iroq: {seller_phone} - JAVOB BERILDI, til: {seller_lang}")
            else:
                logger.warning(f"Reja eslatma qo'ng'iroq: {seller_phone} - javob berilmadi, til: {seller_lang}")

        except Exception as e:
            logger.error(f"Reja eslatma qo'ng'iroq xatosi: {e}")

    async def start(self):
        """Autodialer ni ishga tushirish"""
        logger.info("=" * 60)
        logger.info("AUTODIALER PRO ISHGA TUSHMOQDA")
        logger.info("=" * 60)

        self._running = True

        # Signal handlers (faqat Unix uchun, Windows da ishlamaydi)
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_event_loop().add_signal_handler(
                    sig, lambda: asyncio.create_task(self.stop())
                )

        # TTS oldindan yaratish (faqat Asterisk faol bo'lganda kerak)
        if not self.skip_asterisk:
            logger.info("TTS xabarlarini tayyorlash...")
            await self.tts.pregenerate_messages(max_count=50)
        else:
            logger.info("TTS o'tkazib yuborildi (Asterisk o'chirilgan)")

        # AMI ulanish (faqat Asterisk faol bo'lganda)
        ami_connected = False
        if not self.skip_asterisk:
            logger.info("Asterisk AMI ga ulanish...")
            ami_connected = await self.ami.connect()
            if not ami_connected:
                logger.error("AMI ulanish muvaffaqiyatsiz!")
                # AMI siz ham davom etish mumkin (faqat Telegram)

            # SIP registratsiya tekshirish
            if ami_connected:
                registered = await self.ami.check_registration()
                if registered:
                    logger.info("SIP registratsiya: OK")
                else:
                    logger.warning("SIP registratsiya: MUVAFFAQIYATSIZ")
        else:
            logger.info("AMI ulanish o'tkazib yuborildi (Windows rejim)")

        # Nonbor API polling boshlash
        logger.info("Nonbor API polling boshlash...")
        await self.nonbor_poller.start()

        # Stats handler polling boshlash
        if self.stats_handler:
            logger.info("Telegram stats handler boshlash...")
            await self.stats_handler.start_polling()

        # Ishga tushganda sinxronizatsiya - Nonbor API va Telegram
        await self._sync_on_startup()

        # HTTP API server ishga tushirish
        logger.info("API server ishga tushirish...")
        await self.api_server.start()

        # Asosiy loop
        logger.info("=" * 60)
        logger.info("AUTODIALER PRO ISHLAYAPTI")
        logger.info("=" * 60)

        # Asosiy task
        self._tasks.append(asyncio.create_task(self._main_loop()))

        # Wait
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Autodialer ni to'xtatish"""
        logger.info("Autodialer to'xtatilmoqda...")
        self._running = False

        # API server to'xtatish
        await self.api_server.stop()

        # Tasks ni bekor qilish
        for task in self._tasks:
            task.cancel()

        # Servislarni yopish
        await self.nonbor_poller.stop()
        if self.stats_handler:
            await self.stats_handler.stop_polling()
        if not self.skip_asterisk:
            await self.ami.disconnect()
        await self.nonbor.close()
        if self.telegram:
            await self.telegram.close()

        logger.info("Autodialer to'xtatildi")

    async def _sync_on_startup(self):
        """
        Ishga tushganda Nonbor API va Telegram ni sinxronlashtirish

        Agar CHECKING statusida buyurtmalar bo'lsa:
        - Telegram ga yangi xabar yuborish
        - Holatni tiklash

        Agar buyurtmalar yo'q bo'lsa:
        - Telegramdagi eski xabarlarni o'chirish
        """
        logger.info("Sinxronizatsiya: Nonbor API va Telegram tekshirilmoqda...")

        try:
            # CHECKING dagi buyurtmalarni olish
            leads = await self.nonbor.get_leads_by_status()

            if not leads:
                logger.info("Sinxronizatsiya: CHECKING da buyurtmalar yo'q")
                # Telegramdagi eski xabarlarni o'chirish (fayldan yuklangan ID lar)
                if self.notification_manager and self.notification_manager._active_message_ids:
                    logger.info(f"Telegram: {len(self.notification_manager._active_message_ids)} ta eski xabarni o'chirish...")
                    await self.notification_manager.delete_all_notifications()
                    logger.info("Telegram: barcha eski xabarlar o'chirildi")
                else:
                    logger.info("Telegram: o'chiriladigan eski xabar topilmadi")
                self.state.reset()
                return

            count = len(leads)
            order_ids = [lead["id"] for lead in leads]

            logger.info(f"Sinxronizatsiya: {count} ta buyurtma topildi")

            # Holatni tiklash
            self.state.pending_orders_count = count
            self.state.pending_order_ids = order_ids

            # Eng eski buyurtmaning vaqtini topish (created_at dan)
            oldest_time = datetime.now()
            for lead in leads:
                created_at = lead.get("created_at")
                if created_at:
                    from datetime import timezone
                    try:
                        lead_time = datetime.fromisoformat(created_at.replace("Z", "+00:00")).replace(tzinfo=None)
                    except (ValueError, AttributeError):
                        lead_time = datetime.now()
                    if lead_time < oldest_time:
                        oldest_time = lead_time

            # Buyurtmalar uchun timestamp qo'shish (180s timer uchun)
            for lead in leads:
                order_id = lead["id"]
                if order_id not in self.state.order_timestamps:
                    created_at = lead.get("created_at")
                    if created_at:
                        try:
                            self.state.order_timestamps[order_id] = datetime.fromisoformat(created_at.replace("Z", "+00:00")).replace(tzinfo=None)
                        except (ValueError, AttributeError):
                            self.state.order_timestamps[order_id] = datetime.now()
                    else:
                        self.state.order_timestamps[order_id] = datetime.now()

            # Eng eski buyurtma necha sekund oldin kelganini hisoblash
            time_since_oldest = (datetime.now() - oldest_time).total_seconds()
            logger.info(f"Sinxronizatsiya: eng eski buyurtma {time_since_oldest:.0f}s oldin kelgan")

            # MUHIM: Agar buyurtma 90s dan oshgan bo'lsa - darhol qo'ng'iroq qilish
            # Agar 90s dan kam bo'lsa - timer boshlash (qolgan vaqtni kutish)
            if time_since_oldest >= self.wait_before_call:
                # 90s o'tgan - darhol qo'ng'iroq qilish
                logger.info(f"Sinxronizatsiya: {time_since_oldest:.0f}s > {self.wait_before_call}s - DARHOL qo'ng'iroq qilinadi")
                self.state.last_new_order_time = oldest_time
                self.state.waiting_for_call = True
                self.state.call_started = False  # call_started=False → qo'ng'iroq qilinadi
            else:
                # 90s hali o'tmagan - timer davom etadi
                remaining = self.wait_before_call - time_since_oldest
                logger.info(f"Sinxronizatsiya: {time_since_oldest:.0f}s < {self.wait_before_call}s - {remaining:.0f}s kutiladi")
                self.state.last_new_order_time = oldest_time
                self.state.waiting_for_call = True
                self.state.call_started = False  # call_started=False → timer tugaganda qo'ng'iroq qilinadi

            # MUHIM: Agar Telegram xabarlari mavjud bo'lsa, telegram_notified = True qilish
            # Bu autodialer qayta ishga tushganda kerak - oldingi Telegram xabarlari saqlanadi
            if self.notification_manager and self.notification_manager.has_active_notification:
                self.state.telegram_notified = True
                logger.info(f"Sinxronizatsiya: Telegram xabarlari mavjud, telegram_notified = True")

            logger.info(f"Sinxronizatsiya tugadi: {count} ta buyurtma, qo'ng'iroq va 180s timer kuzatmoqda")

        except Exception as e:
            logger.error(f"Sinxronizatsiya xatosi: {e}")


    async def _main_loop(self):
        """Asosiy ishlash sikli"""
        ami_check_counter = 0
        while self._running:
            try:
                await self._check_and_process()
                await asyncio.sleep(1)

                # Har 60 sekundda AMI ulanishni tekshirish va qayta ulanish
                if not self.skip_asterisk:
                    ami_check_counter += 1
                    if ami_check_counter >= 60:
                        ami_check_counter = 0
                        if not self.ami._connected:
                            logger.info("AMI ulanish uzilgan, avtomatik qayta ulanish...")
                            try:
                                reconnected = await self.ami.reconnect()
                                if reconnected:
                                    logger.info("AMI qayta ulanish muvaffaqiyatli!")
                                else:
                                    logger.warning("AMI qayta ulanish muvaffaqiyatsiz, 60s dan keyin qayta uriniladi")
                            except Exception as e:
                                logger.warning(f"AMI reconnect xatosi: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop xatosi: {e}")
                await asyncio.sleep(5)

    async def _check_and_process(self):
        """Holatni tekshirish va qayta ishlash"""
        now = datetime.now()

        # Biznes guruhlarni majburiy to'ldirish (fayl o'chirilgan bo'lsa)
        if self.stats_handler and hasattr(self.stats_handler, '_business_groups'):
            if "24" not in self.stats_handler._business_groups:
                self.stats_handler._business_groups["24"] = os.environ.get("TELEGRAM_CHAT_ID", "-5219407458")
                self.stats_handler._save_groups()

        # 90s TIMER: Qo'ng'iroq qilish
        if self.state.waiting_for_call and self.state.last_new_order_time:
            elapsed = (now - self.state.last_new_order_time).total_seconds()

            # Qo'ng'iroq qilish vaqti (faqat bir marta chaqiriladi - ichida barcha urinishlar)
            if elapsed >= self.wait_before_call and not self.state.call_started:
                self.state.call_started = True
                await self._make_call()

        # 180s TIMER: Telegram yuborish - waiting_for_call ga BOG'LIQ EMAS
        # MUHIM: Har 10 sekundda bir marta tekshirish (spam bo'lmaslik uchun)
        if len(self.state.order_timestamps) > 0:
            # Oxirgi tekshiruvdan 10 sekund o'tdimi?
            should_check = False
            if self.state.last_180s_check_time is None:
                should_check = True
            else:
                time_since_last_check = (now - self.state.last_180s_check_time).total_seconds()
                if time_since_last_check >= 10:
                    should_check = True

            if should_check:
                self.state.last_180s_check_time = now

                # Eng eski buyurtmaning vaqtini topish
                oldest_order_time = min(self.state.order_timestamps.values())
                time_since_oldest = (now - oldest_order_time).total_seconds()

                # Agar eng eski buyurtma telegram_alert_time+ bo'lsa
                if time_since_oldest >= self.telegram_alert_time:
                    # Tekshirish: telegram_alert_time+ buyurtmalar bor va ular hali yuborilmagan
                    old_order_ids = []
                    for order_id, timestamp in self.state.order_timestamps.items():
                        if (now - timestamp).total_seconds() >= self.telegram_alert_time:
                            old_order_ids.append(order_id)

                    # Agar 180s+ buyurtmalar bor
                    if old_order_ids:
                        current_old_ids = set(old_order_ids)
                        # MUHIM FIX: Yangi 180s+ buyurtmalar bormi tekshirish
                        # (ya'ni hali Telegram'da yo'q buyurtmalar)
                        new_old_ids = current_old_ids - self.state.last_telegram_order_ids
                        if new_old_ids:
                            logger.info(f"{self.telegram_alert_time}s timer: {len(new_old_ids)} ta YANGI buyurtma {self.telegram_alert_time}s+ eski, Telegram yuborilmoqda")
                            await self._send_telegram_for_remaining()

        # GURUH XABARLARI: Status o'zgarishlarini kuzatish (har 2 soniyada)
        # Biznes guruhlar mavjud bo'lsa yoki xabarlar track qilinayotgan bo'lsa
        has_business_groups = self.stats_handler and self.stats_handler._business_groups
        if has_business_groups or self._group_order_messages:
            should_check_status = False
            if self.state.last_group_status_check is None:
                should_check_status = True
            else:
                time_since_check = (now - self.state.last_group_status_check).total_seconds()
                if time_since_check >= 5:  # Har 5 soniyada tekshirish (server yuklamasini kamaytirish)
                    should_check_status = True

            if should_check_status:
                self.state.last_group_status_check = now
                # Pending buyurtmalarni olish va tozalash (atomik)
                pending_orders = set(self._pending_group_message_orders)
                self._pending_group_message_orders.clear()
                # MUHIM: Allaqachon tracking da bo'lgan buyurtmalarni olib tashlash
                pending_orders = pending_orders - set(self._group_order_messages.keys())
                # Guruh xabarlarini yangilash (pending bilan)
                if pending_orders:
                    logger.info(f"Guruh xabarlari: {len(pending_orders)} ta yangi buyurtma yuborilmoqda")
                await self._update_group_messages(new_order_ids=pending_orders if pending_orders else None)

        # XABARNOMALAR SCHEDULER: Har 30 sekundda tekshirish
        if self.stats_handler:
            should_check_notif = False
            if self._last_notif_check is None:
                should_check_notif = True
            else:
                time_since_notif = (now - self._last_notif_check).total_seconds()
                if time_since_notif >= 30:
                    should_check_notif = True

            if should_check_notif:
                self._last_notif_check = now
                await self._process_scheduled_notifications()

        # REJA ESLATMA: Har 60 sekundda reja buyurtmalarni tekshirish
        if self.telegram and self._group_order_messages:
            should_check_planned = False
            if self._last_planned_check is None:
                should_check_planned = True
            else:
                time_since_planned = (now - self._last_planned_check).total_seconds()
                if time_since_planned >= 60:
                    should_check_planned = True

            if should_check_planned:
                self._last_planned_check = now
                await self._check_planned_reminders()

    async def _process_scheduled_notifications(self):
        """Rejalashtirilgan xabarnomalarni tekshirish va yuborish"""
        try:
            pending = self.stats_handler.get_pending_notifications()
            if not pending:
                return

            for notif in pending:
                notif_id = notif.get("id", "?")
                target_ids = notif.get("target_ids", [])
                text = notif.get("text", "")

                if not target_ids or not text:
                    self.stats_handler.mark_notification_sent(notif_id, 0, 0)
                    continue

                logger.info(f"Xabarnoma yuborilmoqda: id={notif_id}, targets={len(target_ids)}")

                sent_count = 0
                total_count = len(target_ids)

                for biz_id in target_ids:
                    group_id = self.stats_handler._business_groups.get(str(biz_id), "")
                    if not group_id:
                        continue
                    try:
                        await self.telegram.send_message(
                            text=f"📢 <b>XABARNOMA</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{text}",
                            chat_id=group_id,
                            parse_mode="HTML"
                        )
                        sent_count += 1
                        await asyncio.sleep(0.5)  # Telegram rate limit
                    except Exception as e:
                        logger.error(f"Xabarnoma yuborish xatosi (biz={biz_id}, group={group_id}): {e}")

                self.stats_handler.mark_notification_sent(notif_id, sent_count, total_count)
                logger.info(f"Xabarnoma yuborildi: id={notif_id}, sent={sent_count}/{total_count}")

        except Exception as e:
            logger.error(f"Xabarnomalar scheduler xatosi: {e}")

    async def _update_group_messages(self, new_order_ids: set = None):
        """
        Biriktirilgan guruhlarga har bir buyurtma uchun alohida xabar yuborish/yangilash.
        new_order_ids: yangi kelgan buyurtma ID lari (faqat ular uchun xabar yuboriladi)
        """
        # Lock bilan ishlaymiz - race condition oldini olish
        async with self._group_messages_lock:
            await self._update_group_messages_internal(new_order_ids)

    async def _update_group_messages_internal(self, new_order_ids: set = None):
        """Internal: Lock ichida chaqiriladi"""
        if not self.stats_handler:
            logger.debug("Guruh xabarlari: stats_handler yo'q, o'tkazib yuborildi")
            return
        try:
            orders = await self.nonbor.get_orders()
            if not orders:
                if new_order_ids:
                    logger.warning(f"Guruh xabarlari: get_orders() bo'sh qaytdi, {len(new_order_ids)} ta buyurtma qayta navbatga qo'shildi")
                    self._pending_group_message_orders.update(new_order_ids)
                return

            # Biznes title -> ID mapping (businesses cache dan)
            title_to_id = {}
            for bid, bdata in self.nonbor._businesses_cache.items():
                title = bdata.get("title", "").strip().lower()
                if title:
                    title_to_id[title] = str(bid)

            # Agar cache bo'sh bo'lsa, API dan yuklash
            if not title_to_id:
                await self.nonbor.get_businesses()
                for bid, bdata in self.nonbor._businesses_cache.items():
                    title = bdata.get("title", "").strip().lower()
                    if title:
                        title_to_id[title] = str(bid)

            for order in orders:
                order_id = order.get("id")
                business = order.get("business") or {}

                # Business ID ni aniqlash: avval to'g'ridan-to'g'ri, keyin title orqali
                biz_id = str(business.get("id", ""))
                biz_title = business.get("title", "")
                if not biz_id:
                    biz_title_lower = biz_title.strip().lower()
                    biz_id = title_to_id.get(biz_title_lower, "")

                # Buyurtma va biznes ma'lumotlarini ko'rsatish
                if biz_id:
                    in_groups = biz_id in self.stats_handler._business_groups
                    logger.info(f"Buyurtma #{order_id}: biznes='{biz_title}' (ID={biz_id}), guruhda={in_groups}")
                    # DEBUG: Birinchi buyurtmaning barcha kalitlarini ko'rsatish (faqat bir marta)
                    if not self._debug_logged:
                        self._debug_logged = True
                        logger.info(f"DEBUG #{order_id}: order_keys={list(order.keys())}")
                        logger.info(f"DEBUG #{order_id}: user={order.get('user')}")
                        logger.info(f"DEBUG #{order_id}: delivery={order.get('delivery')}")
                        # Vaqt bilan bog'liq barcha fieldlarni ko'rsatish
                        for key in order.keys():
                            val = order.get(key)
                            if val and ('time' in str(key).lower() or 'date' in str(key).lower() or 'plan' in str(key).lower() or 'schedule' in str(key).lower() or 'expect' in str(key).lower()):
                                logger.info(f"DEBUG #{order_id}: {key}={val}")

                if not biz_id or biz_id not in self.stats_handler._business_groups:
                    if biz_id:
                        logger.info(f"Buyurtma #{order_id}: biznes #{biz_id} guruhlar ro'yxatida yo'q, mavjud: {list(self.stats_handler._business_groups.keys())}")
                    continue

                group_chat_id = self.stats_handler._business_groups[biz_id]
                status = order.get("state", "CHECKING").upper()

                # PENDING va to'lov kutilayotgan buyurtmalarni o'tkazib yuborish
                skip_statuses = ["PENDING", "WAITING_PAYMENT", "PAYMENTPENDING", "PAYMENT_PENDING"]
                if status in skip_statuses:
                    continue

                # Buyurtma ma'lumotlarini tayyorlash
                user = order.get("user") or {}
                items = order.get("order_item") or order.get("items") or []
                delivery = order.get("delivery") or {}
                client_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Noma'lum"
                product_name = ""
                quantity = 1
                if items:
                    first_item = items[0]
                    product = first_item.get("product") or {}
                    product_name = product.get("title", "") or product.get("name", "")
                    quantity = first_item.get("count", 1) or first_item.get("quantity", 1)

                # Telefon raqami - user, delivery, order yoki boshqa maydonlardan
                client_phone = (
                    user.get("phone") or
                    user.get("phone_number") or
                    user.get("mobile") or
                    user.get("tel") or
                    delivery.get("phone") or
                    delivery.get("phone_number") or
                    delivery.get("recipient_phone") or
                    order.get("phone") or
                    order.get("client_phone") or
                    order.get("customer_phone") or
                    ""
                )

                # Agar telefon topilmasa va status READY yoki undan keyin - /orders/{id}/ dan olish
                need_phone_statuses = ["READY", "DELIVERING", "DELIVERED", "COMPLETED"]
                if not client_phone and status in need_phone_statuses:
                    try:
                        details = await self.nonbor.get_order_details(order_id)
                        if details:
                            # Turli joylarda telefon qidirish
                            detail_user = details.get("user") or {}
                            detail_delivery = details.get("delivery") or {}
                            client_phone = (
                                detail_user.get("phone") or
                                detail_user.get("phone_number") or
                                detail_delivery.get("phone") or
                                detail_delivery.get("phone_number") or
                                detail_delivery.get("recipient_phone") or
                                details.get("phone") or
                                details.get("client_phone") or
                                ""
                            )
                            if client_phone:
                                logger.info(f"Buyurtma #{order_id}: telefon /orders/ dan olindi: {client_phone}")
                            else:
                                logger.debug(f"Buyurtma #{order_id}: /orders/ da ham telefon yo'q. Keys: {list(details.keys())}")
                    except Exception as e:
                        logger.debug(f"Buyurtma #{order_id}: /orders/ endpoint xato: {e}")

                # Yetkazib berish manzili
                delivery_address = delivery.get("address") or delivery.get("location") or ""
                delivery_lat = delivery.get("lat") or delivery.get("latitude") or ""
                delivery_lon = delivery.get("lon") or delivery.get("longitude") or delivery.get("lng") or ""

                # Yetkazib berish vaqti (rejalashtirilgan buyurtmalar uchun)
                # planned_datetime - asosiy field
                raw_planned_time = order.get("planned_datetime") or order.get("planned_time") or ""

                # Vaqtni formatlash (2026-01-29T11:00:00+05:00 -> 29.01 11:00)
                # MUHIM: UTC dan O'zbekiston vaqtiga (UTC+5) o'tkazish
                delivery_time = ""
                if raw_planned_time:
                    try:
                        from datetime import timezone, timedelta
                        uz_tz = timezone(timedelta(hours=5))
                        dt = datetime.fromisoformat(str(raw_planned_time).replace('Z', '+00:00'))
                        # O'zbekiston vaqtiga o'tkazish
                        dt_uz = dt.astimezone(uz_tz)
                        delivery_time = dt_uz.strftime("%d.%m %H:%M")
                    except:
                        delivery_time = str(raw_planned_time)

                # Agar planned_datetime yo'q bo'lsa, boshqa fieldlardan qidirish
                if not delivery_time:
                    delivery_time = (
                        delivery.get("time") or
                        delivery.get("scheduled_time") or
                        delivery.get("delivery_time") or
                        order.get("delivery_time") or
                        order.get("scheduled_at") or
                        ""
                    )

                # Debug: is_planned buyurtmalar uchun
                is_planned = order.get("is_planned") or order.get("is_planner")
                if is_planned:
                    logger.info(f"Buyurtma #{order_id} PLANNED: delivery_time='{delivery_time}', ready_time={order.get('ready_time')}")
                    if not delivery_time:
                        logger.warning(f"Buyurtma #{order_id} is_planned=True lekin delivery_time topilmadi. Order keys: {list(order.keys())}")

                # Qabul qilish muddati tugadimi tekshirish
                display_status = status
                if status == "CHECKING" and order_id in self.state.order_timestamps:
                    order_age = (datetime.now() - self.state.order_timestamps[order_id]).total_seconds()
                    # Qo'ng'iroqlar tugagan va muddat o'tgan bo'lsa
                    if order_age >= self.telegram_alert_time and not self.state.waiting_for_call:
                        display_status = "ACCEPT_EXPIRED"
                        logger.debug(f"Buyurtma #{order_id} qabul muddati tugadi ({order_age:.0f}s)")

                order_data = {
                    "order_number": str(order_id),
                    "status": display_status,
                    "seller_name": biz_title or "Noma'lum",
                    "client_name": client_name,
                    "client_phone": client_phone,
                    "product_name": product_name,
                    "quantity": quantity,
                    "price": (order.get("total_price", 0) or 0) / 100,
                    "delivery_address": delivery_address,
                    "delivery_lat": delivery_lat,
                    "delivery_lon": delivery_lon,
                    "delivery_time": delivery_time,
                    "delivery_method": order.get("delivery_method", ""),
                    "payment_method": order.get("payment_method", ""),
                    "is_planned": bool(is_planned),
                    "planned_datetime_raw": str(raw_planned_time) if raw_planned_time else "",
                }

                if order_id in self._group_order_messages:
                    # Mavjud xabar - status o'zgargan bo'lsa yangilash
                    tracked = self._group_order_messages[order_id]
                    if tracked.get("status") != display_status:
                        success = await self.telegram.update_business_order_message(
                            message_id=tracked["msg_id"],
                            order_data=order_data,
                            chat_id=group_chat_id
                        )
                        if success:
                            tracked["status"] = display_status
                            tracked["order_data"] = order_data
                            self._save_group_messages()
                            logger.info(f"Guruh: buyurtma #{order_id} status yangilandi: {display_status}")
                else:
                    # Yangi buyurtma - tracking da yo'q
                    # MUHIM: Agar buyurtma allaqachon yakuniy statusda bo'lsa, yangi xabar yubormaymiz
                    # (bu buyurtmani tracking qilishni o'tkazib yubordik)
                    final_statuses_for_skip = ["COMPLETED", "CANCELLED", "DELIVERED", "CANCELLED_SELLER", "CANCELLED_USER", "CANCELLED_ADMIN", "PAYMENT_EXPIRED"]
                    if status in final_statuses_for_skip:
                        # Yakuniy statusdagi buyurtma - yangi xabar yubormaymiz
                        continue

                    # MUHIM: Yangi xabar FAQAT new_order_ids berilganda yuboriladi
                    # Status tekshirish loopida (new_order_ids=None) yangi xabar YUBORILMAYDI
                    should_send = (new_order_ids is not None) and (order_id in new_order_ids)

                    # DUPLICATE OLDINI OLISH: Agar xabar allaqachon yuborilayotgan bo'lsa, o'tkazib yuboring
                    if order_id in self._sending_order_messages:
                        logger.debug(f"Buyurtma #{order_id} uchun xabar allaqachon yuborilmoqda, o'tkazib yuborildi")
                        continue

                    # Qayta tekshirish: tracking da bormi (lock ichida ham)
                    if order_id in self._group_order_messages:
                        logger.debug(f"Buyurtma #{order_id} allaqachon tracking da, o'tkazib yuborildi")
                        continue

                    if should_send:
                        # Xabar yuborilayotgan buyurtma sifatida belgilash
                        self._sending_order_messages.add(order_id)
                        try:
                            logger.info(f"Guruhga xabar yuborilmoqda: buyurtma #{order_id}, chat_id={group_chat_id}, status={display_status}")
                            msg_id = await self.telegram.send_business_order_message(
                                order_data=order_data, chat_id=group_chat_id
                            )
                            if msg_id:
                                self._group_order_messages[order_id] = {
                                    "msg_id": msg_id,
                                    "biz_id": biz_id,
                                    "chat_id": group_chat_id,
                                    "status": display_status,
                                    "order_data": order_data,
                                }
                                self._save_group_messages()
                                logger.info(f"Guruhga xabar yuborildi: buyurtma #{order_id}, msg_id={msg_id}, status: {display_status}")
                            else:
                                logger.error(f"Guruhga xabar YUBORILMADI: buyurtma #{order_id}, chat_id={group_chat_id} - Telegram None qaytardi")
                                # Qayta urinish uchun navbatga qo'shish
                                self._pending_group_message_orders.add(order_id)
                        except Exception as send_err:
                            logger.error(f"Guruhga xabar yuborishda xato: buyurtma #{order_id}: {send_err}")
                            self._pending_group_message_orders.add(order_id)
                        finally:
                            # Yuborish tugadi - ro'yxatdan o'chirish
                            self._sending_order_messages.discard(order_id)

            # For loop tugadi - endi API dan yo'qolgan buyurtmalarni tozalash
            # MUHIM: Faqat API da YO'Q bo'lgan buyurtmalarni o'chiramiz
            # Final statusdagi buyurtmalar API da bo'lsa ham qoladi (yangi xabar yuborilmasligi uchun)
            current_order_ids = {order.get("id") for order in orders if order.get("id")}

            deleted_any = False
            for tracked_order_id in list(self._group_order_messages.keys()):
                # Faqat API da yo'q bo'lgan buyurtmalarni tracking dan o'chirish
                if tracked_order_id not in current_order_ids:
                    tracked = self._group_order_messages[tracked_order_id]
                    tracked_status = tracked.get("status", "")
                    del self._group_order_messages[tracked_order_id]
                    deleted_any = True
                    logger.info(f"Guruh tracking tozalandi (API da yo'q): buyurtma #{tracked_order_id}, status={tracked_status}")

            if deleted_any:
                self._save_group_messages()

        except Exception as e:
            logger.error(f"Guruh xabarlarini yangilashda xato: {e}", exc_info=True)
            # Xato bo'lsa, yuborilmagan buyurtmalarni qayta navbatga qo'shish
            if new_order_ids:
                unsent = new_order_ids - set(self._group_order_messages.keys())
                if unsent:
                    self._pending_group_message_orders.update(unsent)
                    logger.info(f"Guruh xabarlari xato: {len(unsent)} ta buyurtma qayta navbatga qo'shildi")

    async def _on_new_orders(self, count: int, new_ids: list):
        """Yangi buyurtmalar callback"""
        logger.info(f"Yangi buyurtmalar: {len(new_ids)} ta, Jami: {count} ta")

        # Holatni yangilash
        old_count = self.state.pending_orders_count
        old_ids = set(self.state.pending_order_ids)
        new_order_ids = set(new_ids)

        # TOZALASH (BIRINCHI): TEKSHIRILMOQDA statusidan chiqqan buyurtmalarni last_communicated_orders dan o'chirish
        # Bu AVVAL qilinishi kerak - uncommunicated_ids ni to'g'ri hisoblash uchun
        for _sp in list(self.state.last_communicated_orders.keys()):
            _still = [oid for oid in self.state.last_communicated_orders[_sp] if oid in new_order_ids]
            if _still:
                self.state.last_communicated_orders[_sp] = _still
            else:
                del self.state.last_communicated_orders[_sp]
                # Buyurtmalari hal qilindi — unanswered dan ham tozalash
                self.state.unanswered_sellers.discard(_sp)
                logger.debug(f"Sotuvchi {_sp}: barcha buyurtmalari hal qilindi, tozalandi")

        # MUHIM: Allaqachon qo'ng'iroq qilingan/xabar berilgan buyurtmalarni topish (tozalangan ma'lumot bilan)
        all_communicated_ids = set()
        for seller_phone, communicated_ids in self.state.last_communicated_orders.items():
            all_communicated_ids.update(communicated_ids)

        # MUHIM: Faqat YANGI (hali xabar berilmagan) buyurtmalarni aniqlash
        # 1. Haqiqatan yangi kelgan buyurtmalar (ilgari yo'q edi)
        truly_new_ids = new_order_ids - old_ids

        # 2. Barcha hozirgi buyurtmalardan allaqachon xabar berilganlarni olib tashlash
        uncommunicated_ids = new_order_ids - all_communicated_ids

        if len(all_communicated_ids) > 0:
            logger.debug(f"Allaqachon xabar berilgan: {len(new_order_ids & all_communicated_ids)} ta buyurtma")

        # MUHIM: Agar BARCHA buyurtmalar allaqachon xabar berilgan bo'lsa, ularni e'tiborsiz qoldiramiz
        # Bu buyurtmalar allaqachon qo'ng'iroq qilingan/Telegram yuborilgan
        if len(uncommunicated_ids) == 0 and count > 0:
            logger.info(f"Barcha {count} ta buyurtma haqida allaqachon xabar berilgan, yangi qo'ng'iroq kerak emas")
            # Holatni yangilash (faqat pending_order_ids)
            self.state.pending_order_ids = new_ids
            self.state.pending_orders_count = count
            # MUHIM: waiting_for_call ni False qoldiramiz, chunki qo'ng'iroq kerak emas
            # Agar waiting_for_call allaqachon True bo'lsa, uni False qilamiz
            if self.state.waiting_for_call:
                self.state.waiting_for_call = False
                logger.debug("waiting_for_call = False (barcha buyurtmalar xabar berilgan)")
            # MUHIM: order_timestamps ni O'CHIRMAYMIZ!
            # Chunki Telegram hali yuborilmagan bo'lishi mumkin (180s kutish kerak)
            # Faqat qo'ng'iroq qilingan, lekin Telegram hali kutilmoqda
            # order_timestamps faqat _on_orders_resolved() da o'chiriladi
            # yoki _send_telegram_for_remaining() dan keyin o'chiriladi
            # Agar order_timestamps bo'sh bo'lsa, last_180s_check_time ni ham reset qilamiz
            if len(self.state.order_timestamps) == 0:
                self.state.last_180s_check_time = None
                logger.debug("order_timestamps bo'sh, 180s timer to'xtatildi")
            # MUHIM: return qilamiz - bu buyurtmalar uchun hech narsa qilmaymiz
            return

        self.state.new_order_received(count, new_ids)

        now = datetime.now()

        # MUHIM: BARCHA yangi buyurtmalar uchun timestamp qo'yish (180s timer uchun)
        # Bu birinchi marta kelgan buyurtmalar uchun ham, keyingi buyurtmalar uchun ham ishlaydi
        if len(truly_new_ids) > 0:
            for new_id in truly_new_ids:
                if new_id not in self.state.order_timestamps:
                    self.state.order_timestamps[new_id] = now
                    logger.debug(f"Buyurtma #{new_id} uchun timestamp qo'yildi: {now}")

            # MUHIM: Yangi buyurtmalar ma'lumotlarini DARHOL keshga olish
            # Resolved bo'lganda API dan topilmasligi mumkin, shuning uchun hozir olib qo'yamiz
            for new_id in truly_new_ids:
                if new_id not in self._order_data_cache:
                    try:
                        order_data = await self.nonbor.get_order_full_data(new_id)
                        if order_data and order_data.get("seller_name") != "Noma'lum":
                            self._order_data_cache[new_id] = order_data
                            logger.info(f"Buyurtma #{new_id} keshga olindi: {order_data.get('seller_name')} / {order_data.get('product_name')}")
                        else:
                            self._order_data_cache[new_id] = order_data or {}
                            logger.debug(f"Buyurtma #{new_id} keshga olindi (to'liq emas)")
                    except Exception as e:
                        logger.warning(f"Buyurtma #{new_id} keshga olishda xato: {e}")

        # Agar yangi buyurtma qo'shilgan bo'lsa
        if len(truly_new_ids) > 0:
            if old_count > 0:
                logger.info(f"YANGI buyurtma qo'shildi: {len(truly_new_ids)} ta, Jami: {old_count} -> {count}")
            else:
                logger.info(f"Birinchi buyurtmalar keldi: {len(truly_new_ids)} ta")

            # Yangi buyurtmalarni to'plash listiga qo'shish
            for new_id in truly_new_ids:
                if new_id not in self.state.new_order_ids_for_call:
                    self.state.new_order_ids_for_call.append(new_id)

            logger.info(f"To'planayotgan yangi buyurtmalar: {len(self.state.new_order_ids_for_call)} ta")

            # Guruh xabari uchun navbatga qo'shish - 2s loopda yuboriladi
            # MUHIM: _on_new_orders dan TO'G'RIDAN-TO'G'RI yubormaymiz (duplicate oldini olish)
            if self.stats_handler and hasattr(self.stats_handler, '_business_groups') and self.stats_handler._business_groups:
                added_count = 0
                for new_id in truly_new_ids:
                    # MUHIM: Allaqachon tracking da bo'lsa QOSHMAYMIZ
                    if new_id not in self._group_order_messages and new_id not in self._pending_group_message_orders:
                        self._pending_group_message_orders.add(new_id)
                        added_count += 1
                if added_count > 0:
                    logger.info(f"Guruh xabari navbatiga qo'shildi: {added_count} ta buyurtma")

            # 1. MUHIM: Yangi buyurtma kelganda Telegram xabar DARHOL yangilanmaydi
            # Har bir buyurtma uchun 180s kutish kerak - _check_and_process() da 180s timer ishlaydi
            logger.info(f"Yangi buyurtmalar uchun 180s timer boshlandi: {len(truly_new_ids)} ta buyurtma")

            # 2. Timer/qo'ng'iroq holati bo'yicha harakat
            if self.state.call_in_progress:
                # Qo'ng'iroq jarayonida - yangi buyurtmalar KEYINGI qo'ng'iroqqa qoladi
                logger.info(f"Qo'ng'iroq jarayonida, yangi buyurtmalar keyingi qo'ng'iroq uchun to'planmoqda: {len(truly_new_ids)} ta")
            elif not self.state.call_started:
                if not self.state.waiting_for_call:
                    # Timer hali boshlanmagan - yangi timer boshlash
                    logger.info(f"Yangi buyurtmalar uchun 90s timer boshlandi")
                    logger.info(f"90 soniyadan keyin BARCHA yangi buyurtmalar uchun BITTA qo'ng'iroq")
                    self.state.last_new_order_time = now
                    self.state.waiting_for_call = True
                    self.state.call_started = False
                else:
                    # Timer allaqachon ishlayapti - buyurtma to'plamga qo'shiladi (timer QAYTA BOSHLANMAYDI)
                    logger.info(f"90s timer ishlayapti, yangi buyurtma to'plamga qo'shildi (Jami: {len(self.state.new_order_ids_for_call)} ta)")

        elif old_count > 0:
            logger.debug(f"Buyurtmalar soni yangilandi: {old_count} -> {count}")

        # TOZALASH: pending_order_ids da yo'q bo'lgan buyurtmalarni order_timestamps dan o'chirish
        # Bu buyurtmalar boshqa statusga o'tgan yoki o'chirilgan
        removed_ids = []
        for order_id in list(self.state.order_timestamps.keys()):
            if order_id not in new_order_ids:
                del self.state.order_timestamps[order_id]
                removed_ids.append(order_id)
                logger.debug(f"Buyurtma #{order_id} TEKSHIRILMOQDA statusidan chiqdi, vaqt yozuvlari o'chirildi")

        # TELEGRAM YANGILASH: Agar birinchi Telegram xabar allaqachon yuborilgan bo'lsa,
        # buyurtmalar o'zgarganda faqat 180s+ eski buyurtmalarni yangilash
        if self.state.telegram_notified and (len(truly_new_ids) > 0 or len(removed_ids) > 0):
            if len(truly_new_ids) > 0:
                logger.info(f"Telegram yangilanmoqda: {len(truly_new_ids)} ta yangi buyurtma qo'shildi")
            if len(removed_ids) > 0:
                logger.info(f"Telegram yangilanmoqda: {len(removed_ids)} ta buyurtma hal qilindi")
            await self._send_telegram_for_remaining()
        elif len(truly_new_ids) > 0:
            # Birinchi Telegram hali yuborilmagan - 180s kutish kerak
            logger.info(f"Yangi buyurtmalar: {len(truly_new_ids)} ta, 180s kutilmoqda (birinchi Telegram hali yuborilmagan)")
        elif len(removed_ids) > 0:
            logger.info(f"Hal qilingan buyurtmalar: {len(removed_ids)} ta")
        else:
            logger.debug(f"O'zgarish yo'q")

    async def _on_orders_resolved(self, resolved_ids: list, remaining_count: int):
        """Buyurtmalar tekshirildi callback"""
        resolved_count = len(resolved_ids)
        logger.info(f"Tekshirildi: {resolved_count} ta, Qoldi: {remaining_count} ta")

        # Qabul qilingan buyurtmalarni statistikaga yozish
        # (Telegram yuborilgan yoki yuborilmaganini aniqlash)
        telegram_was_sent = self.state.telegram_notified

        # Qaysi sotuvchilar uchun o'zgarish bo'lganini aniqlash
        affected_sellers = set()

        # Har bir hal qilingan buyurtma uchun statistika
        resolved_order_ids = resolved_ids
        for order_id in resolved_order_ids:
            # Agar bu buyurtma allaqachon qayd etilgan bo'lsa, o'tkazib yuborish
            if self._recorded_orders.contains(order_id):
                logger.debug(f"Buyurtma #{order_id} allaqachon qayd etilgan, o'tkazib yuborilmoqda")
                continue

            try:
                # MUHIM: Avval order_data_cache dan olish (yangi kelganda olingan)
                order_data = None
                if order_id in self._order_data_cache:
                    order_data = self._order_data_cache[order_id]
                    logger.debug(f"Buyurtma #{order_id} order_data_cache dan olindi")

                # Keyin guruh xabarlaridan olish
                if not order_data and order_id in self._group_order_messages:
                    cached = self._group_order_messages[order_id]
                    cached_data = cached.get("order_data", {})
                    if cached_data:
                        order_data = cached_data
                        logger.debug(f"Buyurtma #{order_id} guruh keshdan olindi")

                # Keshda yo'q bo'lsa yoki ma'lumotlar to'liq emas ("Noma'lum") - API dan olish
                has_unknown = (
                    not order_data or
                    order_data.get("client_name", "Noma'lum") == "Noma'lum" or
                    order_data.get("seller_name", "Noma'lum") == "Noma'lum" or
                    order_data.get("product_name", "Noma'lum") == "Noma'lum"
                )
                if has_unknown:
                    api_data = await self.nonbor.get_order_full_data(order_id)
                    if not order_data:
                        order_data = api_data
                    else:
                        # Kesh bor, lekin ba'zi fieldlar "Noma'lum" - API dan to'ldirish
                        for field in ("client_name", "seller_name", "product_name", "seller_phone", "price"):
                            if order_data.get(field, "Noma'lum") in ("Noma'lum", "", None, 0):
                                api_val = api_data.get(field)
                                if api_val and api_val != "Noma'lum":
                                    order_data = dict(order_data)  # copy
                                    order_data[field] = api_val
                    logger.debug(f"Buyurtma #{order_id} API dan to'ldirildi")

                seller_phone = order_data.get("seller_phone", "Noma'lum")
                affected_sellers.add(seller_phone)

                # MUHIM: Buyurtma statusiga qarab natijani aniqlash
                # Avval order_data dan, keyin individual API dan status olish
                order_status = order_data.get("state", order_data.get("status", ""))
                if not order_status:
                    api_status = await self.nonbor.get_order_status(order_id)
                    if api_status:
                        order_status = api_status
                        logger.info(f"Buyurtma #{order_id} status API dan olindi: {order_status}")
                rejected_statuses = [
                    "CANCELLED", "CANCELLED_SELLER", "CANCELLED_USER", "CANCELLED_ADMIN",
                    "ACCEPT_EXPIRED", "PAYMENT_EXPIRED", "REJECTED"
                ]
                if order_status in rejected_statuses:
                    order_result = OrderResult.REJECTED
                else:
                    order_result = OrderResult.ACCEPTED

                self.stats.record_order(
                    order_id=order_id,
                    order_number=order_data.get("order_number", str(order_id)),
                    seller_name=order_data.get("seller_name", "Noma'lum"),
                    seller_phone=seller_phone,
                    client_name=order_data.get("client_name", "Noma'lum"),
                    product_name=order_data.get("product_name", "Noma'lum"),
                    price=order_data.get("price", 0),
                    result=order_result,
                    call_attempts=self.state.call_attempts,
                    telegram_sent=telegram_was_sent,
                    order_status=order_status
                )
                self._recorded_orders.add(order_id)
                logger.info(f"Buyurtma #{order_id} statistikaga yozildi: {order_result.value} (status={order_status})")
            except Exception as e:
                logger.error(f"Buyurtma #{order_id} statistika yozishda xato: {e}")

        # Tekshirilgan buyurtmalarni order_timestamps dan o'chirish
        for order_id in resolved_order_ids:
            if order_id in self.state.order_timestamps:
                del self.state.order_timestamps[order_id]
                logger.debug(f"Buyurtma #{order_id} vaqt yozuvlari o'chirildi")

        # Tekshirilgan buyurtmalarni last_telegram_order_ids dan ham o'chirish
        for order_id in resolved_order_ids:
            if order_id in self.state.last_telegram_order_ids:
                self.state.last_telegram_order_ids.discard(order_id)
                logger.debug(f"Buyurtma #{order_id} Telegram tracking dan o'chirildi")

        # Tekshirilgan buyurtmalarni last_communicated_orders dan ham o'chirish
        # (agar buyurtma tekshirilgan bo'lsa, uni qayta xabar berish kerak emas)
        for seller_phone in list(self.state.last_communicated_orders.keys()):
            # Tekshirilgan buyurtmalarni olib tashlash
            original_count = len(self.state.last_communicated_orders[seller_phone])
            self.state.last_communicated_orders[seller_phone] = [
                oid for oid in self.state.last_communicated_orders[seller_phone]
                if oid not in resolved_order_ids
            ]
            removed_count = original_count - len(self.state.last_communicated_orders[seller_phone])
            if removed_count > 0:
                logger.debug(f"Sotuvchi {seller_phone}: {removed_count} ta tekshirilgan buyurtma tracking dan o'chirildi")

            # Agar sotuvchining hech qanday buyurtmasi qolmagan bo'lsa, uni ham o'chirish
            if len(self.state.last_communicated_orders[seller_phone]) == 0:
                del self.state.last_communicated_orders[seller_phone]
                logger.debug(f"Sotuvchi {seller_phone} tracking dan butunlay o'chirildi (buyurtmalar qolmagan)")

        # Agar hammasi tekshirilgan bo'lsa
        if remaining_count == 0:
            logger.info("Barcha buyurtmalar tekshirildi!")
            # TEKSHIRILMOQDA statusida buyurtmalar qolmasa Telegram xabar o'chiriladi
            await self._delete_telegram_messages()
            # Telegram buyurtmalar ro'yxatini tozalash
            self.state.last_telegram_order_ids = set()
            self.state.reset()
        else:
            # Qolgan buyurtmalar - holatni yangilash va Telegram ni yangilash
            self.state.pending_orders_count = remaining_count
            # Resolved ID larni pending dan o'chirish
            self.state.pending_order_ids = [
                oid for oid in self.state.pending_order_ids
                if oid not in resolved_order_ids
            ]
            logger.info(f"Qolgan {remaining_count} ta buyurtma, Telegram yangilanmoqda")

            # Buyurtma hal qilinganda Telegram yangilanadi
            # telegram_notified o'rniga active_message_ids tekshiramiz
            # chunki state.reset() telegram_notified ni False qiladi
            has_active_messages = (
                self.notification_manager and
                self.notification_manager._active_message_ids
            )
            if self.state.telegram_notified or has_active_messages:
                logger.info(f"Telegram xabar yangilanmoqda: {remaining_count} ta buyurtma qoldi")
                await self._send_telegram_for_remaining()

        # MUHIM: Guruh xabarlarini bu yerda O'CHIRMAYMIZ!
        # Buyurtma CHECKING dan chiqsa ham, xabar guruhda qolishi kerak
        # Xabar faqat yakuniy statusda (COMPLETED, CANCELLED, DELIVERED) o'chiriladi
        # Bu _update_group_messages da avtomatik amalga oshiriladi

    async def _make_call(self):
        """Har bir sotuvchiga alohida qo'ng'iroq qilish"""

        # Windows rejimda qo'ng'iroq o'tkazib yuboriladi
        if self.skip_asterisk:
            logger.info("⚠ QO'NG'IROQ O'TKAZIB YUBORILDI (Windows rejim) - faqat Telegram xabar yuboriladi")
            self.state.call_in_progress = False
            self.state.reset()  # call_started=False va waiting_for_call=False - qo'ng'iroq siklini tugatish
            return

        # AMI ulanishni tekshirish va kerak bo'lsa qayta ulanish
        if not self.ami._connected:
            logger.warning("AMI ulanish uzilgan, qayta ulanmoqda...")
            reconnected = await self.ami.reconnect()
            if not reconnected:
                logger.error("AMI qayta ulanish muvaffaqiyatsiz! Qo'ng'iroq qilib bo'lmaydi")
                self.state.call_in_progress = False
                return

        # Qo'ng'iroq jarayonini boshlash - yangi buyurtmalar keyingi qo'ng'iroqqa qoladi
        self.state.call_in_progress = True

        # 90s da: FAQAT TELEFON (Telegram 180s da yuboriladi)

        # QO'NG'IROQ VAQTIDAGI BARCHA buyurtmalarni olish (90s davomida to'plangan)
        # Bu yerda pending_order_ids ni ishlatamiz - bu hozirgi holatdagi BARCHA buyurtmalar
        order_ids = list(self.state.pending_order_ids)
        logger.info(f"QO'NG'IROQ vaqtida: {len(order_ids)} ta buyurtma (90s davomida to'plangan)")

        # MUHIM: Qo'ng'iroq qilishdan oldin statusni tekshirish
        # Buyurtmalar qabul qilingan bo'lishi mumkin (TEKSHIRILMOQDA dan chiqgan)
        current_leads = await self.nonbor.get_leads_by_status()
        if current_leads is None:
            current_leads = []
        current_lead_ids = {lead["id"] for lead in current_leads}

        logger.info(f"Nonbor API dan hozirgi CHECKING statusidagi buyurtmalar: {len(current_lead_ids)} ta")

        # Faqat hali TEKSHIRILMOQDA statusida bo'lgan buyurtmalar uchun qo'ng'iroq qilish
        order_ids = [oid for oid in order_ids if oid in current_lead_ids]

        if len(order_ids) < len(self.state.pending_order_ids):
            removed_count = len(self.state.pending_order_ids) - len(order_ids)
            logger.info(f"Qo'ng'iroq qilishdan oldin {removed_count} ta buyurtma qabul qilindi, ular o'tkazib yuboriladi")

        if not order_ids:
            logger.info("Barcha buyurtmalar allaqachon qabul qilindi, qo'ng'iroq qilish kerak emas")
            self.state.call_in_progress = False
            # Pending buyurtmalarni yangilash - ular qabul qilindi
            self.state.pending_order_ids = []
            self.state.pending_orders_count = 0
            return

        # Barcha buyurtmalarni olish va sotuvchi bo'yicha guruhlash
        sellers = {}
        for order_id in order_ids:
            try:
                order_data = await self.nonbor.get_order_full_data(order_id)
                seller_phone = order_data.get("seller_phone", "Noma'lum")
                seller_name = order_data.get("seller_name", "")
                logger.info(f"Buyurtma #{order_id}: seller_name='{seller_name}', seller_phone='{seller_phone}'")

                # Telefon raqamini formatlash
                if seller_phone and seller_phone != "Noma'lum":
                    # Faqat raqamlarni olish
                    phone_digits = ''.join(filter(str.isdigit, seller_phone))
                    if len(phone_digits) >= 9:
                        # +998 formatiga o'tkazish
                        if len(phone_digits) == 9:
                            seller_phone = f"+998{phone_digits}"
                        elif len(phone_digits) == 12 and phone_digits.startswith("998"):
                            seller_phone = f"+{phone_digits}"
                        else:
                            seller_phone = f"+{phone_digits}"
                    else:
                        seller_phone = None
                else:
                    # "Noma'lum" yoki bo'sh → None
                    seller_phone = None

                if not seller_phone:
                    logger.warning(f"Buyurtma #{order_id}: sotuvchi telefoni topilmadi, qo'ng'iroq o'tkazib yuborildi")
                    continue

                # MUHIM: Agar bu buyurtma haqida sotuvchiga allaqachon xabar berilgan bo'lsa, uni o'tkazib yuboramiz
                if seller_phone in self.state.last_communicated_orders:
                    if order_id in self.state.last_communicated_orders[seller_phone]:
                        logger.debug(f"Buyurtma #{order_id} sotuvchi {seller_phone} ga allaqachon xabar berilgan, o'tkazib yuborildi")
                        continue

                if seller_phone not in sellers:
                    sellers[seller_phone] = {
                        "seller_name": order_data.get("seller_name", "Noma'lum"),
                        "seller_phone": seller_phone,
                        "business_id": order_data.get("business_id"),
                        "language": order_data.get("seller_language", "uz"),
                        "orders": []
                    }
                sellers[seller_phone]["orders"].append(order_data)

            except Exception as e:
                logger.error(f"Buyurtma #{order_id} ma'lumotini olishda xato: {e}")

        if not sellers:
            logger.info("Barcha buyurtmalar haqida allaqachon xabar berilgan, qo'ng'iroq qilish kerak emas")
            # Qo'ng'iroq jarayonini tugatish
            self.state.call_in_progress = False
            # State ni tozalash (last_communicated_orders saqlanadi)
            self.state.reset()
            return

        # Har bir sotuvchiga PARALLEL qo'ng'iroq qilish
        logger.info(f"Parallel qo'ng'iroq: {len(sellers)} ta sotuvchiga bir vaqtda qo'ng'iroq qilinmoqda")

        async def call_single_seller(seller_phone: str, seller_data: dict):
            """Bitta sotuvchiga qo'ng'iroq qilish"""
            order_count = len(seller_data["orders"])
            seller_name = seller_data["seller_name"]
            seller_biz_id = seller_data.get("business_id")
            seller_lang = (seller_data.get("language") or "uz").lower()

            if order_count == 0:
                logger.debug(f"Sotuvchi {seller_name} ({seller_phone}) uchun yangi buyurtmalar yo'q")
                return None

            # Biznes uchun avtoqo'ng'iroq o'chirilganmi tekshirish
            if seller_biz_id and self.stats_handler and not self.stats_handler.is_call_enabled(seller_biz_id):
                logger.info(f"Avtoqo'ng'iroq O'CHIRILGAN: {seller_name} (biz_id={seller_biz_id}) - qo'ng'iroq qilinmaydi")
                return None

            # Per-business config (fayldan o'qish, bo'lmasa global)
            biz_config = {}
            if seller_biz_id and self.stats_handler:
                biz_config = self.stats_handler.get_business_config(seller_biz_id)
            biz_max_attempts = biz_config.get("max_call_attempts", self.max_call_attempts)
            biz_retry_interval = biz_config.get("retry_interval", self.retry_interval)

            # Avval javob bermagan sotuvchi bo'lsa — faqat 1 urinish
            is_retry = seller_phone in self.state.unanswered_sellers
            if is_retry:
                biz_max_attempts = 1
                logger.info(f"QAYTA qo'ng'iroq (1 urinish): {seller_name} ({seller_phone}, biz={seller_biz_id})")
            else:
                logger.info(f"Config: {seller_name} (biz={seller_biz_id}) max_attempts={biz_max_attempts}, retry={biz_retry_interval}s")

            logger.info(f"Qo'ng'iroq: {seller_name} ({seller_phone}), {order_count} ta buyurtma, til: {seller_lang}")

            # TTS audio olish (tilga qarab)
            audio_path = await self.tts.generate_order_message(order_count, lang=seller_lang)
            if not audio_path:
                logger.error(f"TTS audio yaratilmadi: {seller_phone}")
                return None

            # Buyurtma IDlari
            order_ids = [o.get("lead_id") for o in seller_data["orders"]]

            # Status tekshirish va yangi buyurtmalar sonini olish callback
            seller_biz_id = seller_data.get("business_id")

            async def check_orders_still_pending():
                # API dan hozirgi CHECKING buyurtmalarni olish
                current_orders = await self.nonbor.get_orders()
                if current_orders is None:
                    logger.warning("API so'rov muvaffaqiyatsiz, qo'ng'iroq davom ettirilmoqda")
                    return (True, None)
                checking_orders = [o for o in current_orders if o.get("state") == "CHECKING"]

                # Shu sotuvchining CHECKING buyurtmalari (business ID bo'yicha)
                seller_checking = [
                    o for o in checking_orders
                    if (o.get("business") or {}).get("id") == seller_biz_id
                ]

                new_count = len(seller_checking)
                logger.info(f"Qayta tekshirish: {seller_name} ({seller_phone}, biz_id={seller_biz_id}) - {new_count} ta CHECKING buyurtma")

                if new_count == 0:
                    logger.info(f"Barcha buyurtmalar qabul qilindi, qo'ng'iroq to'xtatildi")
                    return (False, None)

                # Eski buyurtmalar hali CHECKING da ekanini tekshirish
                # Agar hech bo'lmasa BITTA buyurtma hali CHECKING bo'lsa — davom etamiz
                still_checking = 0
                confirmed_accepted = 0
                for order_id in order_ids:
                    status = await self.nonbor.get_order_status(order_id)
                    if status is None:
                        # API xato/noma'lum → CHECKING deb hisoblaymiz (fail-safe)
                        still_checking += 1
                        logger.info(f"Buyurtma #{order_id}: status noma'lum (API xato), CHECKING deb hisoblanadi")
                    elif status == "CHECKING":
                        still_checking += 1
                    else:
                        confirmed_accepted += 1
                        logger.info(f"Buyurtma #{order_id} statusi o'zgardi: {status}")
                if still_checking == 0 and confirmed_accepted > 0:
                    logger.info(f"Barcha eski buyurtmalar qabul qilindi, qo'ng'iroq to'xtatildi")
                    return (False, None)
                logger.info(f"{still_checking}/{len(order_ids)} ta buyurtma hali CHECKING da — qayta qo'ng'iroq qilinadi")

                # Yangi TTS audio yaratish (yangilangan son bilan, xuddi shu til bilan)
                new_audio_path = await self.tts.generate_order_message(new_count, lang=seller_lang)
                logger.info(f"Yangi audio yaratildi: {new_count} ta buyurtma (til: {seller_lang})")

                return (True, str(new_audio_path) if new_audio_path else None)

            # Qo'ng'iroq qilish (per-business config bilan)
            result = await self.call_manager.make_call_with_retry(
                phone_number=seller_phone,
                audio_file=str(audio_path),
                on_attempt=self._on_call_attempt,
                before_retry_check=check_orders_still_pending,
                max_attempts_override=biz_max_attempts,
                retry_interval_override=biz_retry_interval,
            )

            # Statistika
            self._seller_call_attempts[seller_phone] = self.state.call_attempts
            self._seller_call_answered[seller_phone] = result.is_answered

            if result.is_answered:
                # JAVOB BERDI — buyurtmalarni belgilash, unanswered dan olib tashlash
                if seller_phone not in self.state.last_communicated_orders:
                    self.state.last_communicated_orders[seller_phone] = []
                existing_ids = set(self.state.last_communicated_orders[seller_phone])
                new_ids = [oid for oid in order_ids if oid not in existing_ids]
                self.state.last_communicated_orders[seller_phone].extend(new_ids)
                self.state.unanswered_sellers.discard(seller_phone)
                logger.info(f"[OK] Qo'ng'iroq muvaffaqiyatli: {seller_name} ({seller_phone})")
                self.stats.record_call(
                    phone=seller_phone,
                    seller_name=seller_name,
                    order_count=order_count,
                    attempts=self.state.call_attempts,
                    result=StatsCallResult.ANSWERED,
                    order_ids=order_ids
                )
            else:
                # JAVOB BERMADI — keyingi siklda 1 urinish bilan qayta qo'ng'iroq
                self.state.unanswered_sellers.add(seller_phone)
                logger.warning(f"[X] Qo'ng'iroq javobsiz: {seller_name} ({seller_phone}) - keyingi siklda qayta qo'ng'iroq (1 urinish)")
                self.stats.record_call(
                    phone=seller_phone,
                    seller_name=seller_name,
                    order_count=order_count,
                    attempts=self.state.call_attempts,
                    result=StatsCallResult.NO_ANSWER,
                    order_ids=order_ids
                )

            return result

        # Barcha sotuvchilarga PARALLEL qo'ng'iroq
        call_tasks = [
            call_single_seller(seller_phone, seller_data)
            for seller_phone, seller_data in sellers.items()
        ]
        results = await asyncio.gather(*call_tasks, return_exceptions=True)

        # Natijalarni log qilish
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Qo'ng'iroq xatosi (istisno): {r}", exc_info=r)
        answered_count = sum(1 for r in results if r and hasattr(r, 'is_answered') and r.is_answered)
        failed_count = sum(1 for r in results if not r or not hasattr(r, 'is_answered') or not r.is_answered)
        logger.info(f"Parallel qo'ng'iroq tugadi: [OK] {answered_count} javob, [X] {failed_count} javobsiz")

        # Barcha qo'ng'iroqlar tugadi
        # Qo'ng'iroq jarayonini tugatish
        self.state.call_in_progress = False

        # Yangi buyurtma qo'ng'iroqlari listini tozalash
        self.state.new_order_ids_for_call = []

        # MUHIM: Hozir BARCHA buyurtmalar allaqachon xabar berilgan yoki javob berilmagan urinishlar tugagan
        # Tekshirish: hali xabar berilmagan buyurtmalar bormi?
        uncommunicated_count = 0
        all_communicated_ids = set()
        for seller_phone, communicated_ids in self.state.last_communicated_orders.items():
            all_communicated_ids.update(communicated_ids)

        for order_id in self.state.pending_order_ids:
            if order_id not in all_communicated_ids:
                uncommunicated_count += 1

        # Qo'ng'iroq tugadi - javob berilgan yoki berilmagan, state ni tozalash
        # MUHIM: Har bir sotuvchi uchun call_attempts allaqachon yuqorida saqlangan (_seller_call_attempts)

        if failed_count == 0:
            logger.info("Barcha qo'ng'iroqlar muvaffaqiyatli, state tozalanmoqda")
            # Telegram xabarlarni HECH QACHON O'CHIRMAYMIZ - ular doim qoladi
            logger.info("Telegram xabarlar saqlanadi (o'chirilmaydi)")
            # To'liq reset - javob berilgan, qayta qo'ng'iroq kerak emas
            self.state.reset()
        elif uncommunicated_count == 0:
            # BARCHA buyurtmalar haqida xabar berilgan (javob berilgan yoki berilmagan)
            # Qayta urinish kerak emas
            logger.info(f"Barcha {len(self.state.pending_order_ids)} ta buyurtma haqida xabar berilgan, qayta qo'ng'iroq KERAK EMAS")
            logger.info("180s timer davom etmoqda (Telegram uchun)")
            # State ni tozalash (lekin last_communicated_orders saqlanadi)
            self.state.reset()
        else:
            # Javob berilmadi — keyingi polling siklida qayta qo'ng'iroq qilinadi
            # last_communicated_orders da faqat javob berganlar bor,
            # javob bermaganlar keyingi siklda yangi buyurtmalar bilan birga jamlanadi
            logger.warning(f"{failed_count} ta qo'ng'iroqqa javob berilmadi, {uncommunicated_count} ta buyurtma uchun")
            logger.info("Javob bermaganlar keyingi polling siklida qayta qo'ng'iroq qilinadi")
            # State ni tozalash (lekin last_communicated_orders SAQLANADI)
            self.state.reset()

    async def _on_call_attempt(self, attempt: int, max_attempts: int):
        """Qo'ng'iroq urinishi callback"""
        self.state.call_attempts = attempt
        logger.info(f"Qo'ng'iroq urinishi: {attempt}/{max_attempts}")

    async def _send_telegram_alert(self):
        """Telegram xabar yuborish - sotuvchi bo'yicha guruhlangan"""
        if not self.notification_manager:
            return

        order_ids = self.state.pending_order_ids
        logger.info(f"Telegram xabar yuborish: {len(order_ids)} ta buyurtma")

        # Barcha buyurtmalarni olish
        all_orders = []
        for order_id in order_ids:
            try:
                order_data = await self.nonbor.get_order_full_data(order_id)
                all_orders.append(order_data)
            except Exception as e:
                logger.error(f"Buyurtma #{order_id} ma'lumotini olishda xato: {e}")

        # Sotuvchi bo'yicha guruhlash
        sellers = {}
        for order in all_orders:
            seller_phone = order.get("seller_phone", "Noma'lum")
            if seller_phone not in sellers:
                sellers[seller_phone] = {
                    "seller_name": order.get("seller_name", "Noma'lum"),
                    "seller_phone": seller_phone,
                    "seller_address": order.get("seller_address", "Noma'lum"),
                    "orders": [],
                    "chat_id": None,
                }
            sellers[seller_phone]["orders"].append(order)

            # Biznes guruh chat_id ni aniqlash
            if not sellers[seller_phone]["chat_id"]:
                order_id = order.get("lead_id") or order.get("order_number")
                if order_id and order_id in self._group_order_messages:
                    biz_id = self._group_order_messages[order_id].get("biz_id")
                    if biz_id and biz_id in self.stats_handler._business_groups:
                        sellers[seller_phone]["chat_id"] = self.stats_handler._business_groups[biz_id]

        # Har bir sotuvchi uchun bitta xabar
        for seller_phone, seller_data in sellers.items():
            try:
                # Har bir sotuvchi uchun o'z qo'ng'iroq urinishlari soni
                seller_attempts = self._seller_call_attempts.get(seller_phone, 0)
                # Alert xabari ADMIN guruhga yuboriladi (default chat_id)
                logger.info(f"Sotuvchi {seller_data['seller_name']}: {len(seller_data['orders'])} ta buyurtma, {seller_attempts} urinish, chat=ADMIN")
                await self.notification_manager.notify_seller_orders(
                    seller_data,
                    seller_attempts,
                    chat_id=None  # Admin guruhga
                )
            except Exception as e:
                logger.error(f"Sotuvchi {seller_phone} xabar yuborishda xato: {e}")

    async def _delete_telegram_messages(self):
        """Telegram xabarlarni o'chirish"""
        if not self.notification_manager:
            return

        # Avval pending deletions ni qayta urinish
        await self.notification_manager.retry_pending_deletions()

        if self.notification_manager._active_message_ids:
            logger.info(f"Telegram xabarlarni o'chirish: {len(self.notification_manager._active_message_ids)} ta")
            failed_ids = []
            for msg_id in self.notification_manager._active_message_ids:
                try:
                    success = await self.telegram.delete_message(msg_id)
                    if success:
                        logger.debug(f"Xabar o'chirildi: {msg_id}")
                    else:
                        failed_ids.append(msg_id)
                        logger.warning(f"Xabar o'chirilmadi, pending ga qo'shilmoqda: {msg_id}")
                except Exception as e:
                    failed_ids.append(msg_id)
                    logger.error(f"Xabar o'chirishda xato {msg_id}: {e}")

            # Muvaffaqiyatsiz o'chirishlarni pending ga qo'shish
            if failed_ids:
                for msg_id in failed_ids:
                    if msg_id not in self.notification_manager._pending_deletions:
                        self.notification_manager._pending_deletions.append(msg_id)

            self.notification_manager._active_message_ids = []
            self.notification_manager._seller_message_ids = {}
            self.notification_manager._combined_message_id = None
            self.notification_manager._save_messages()

    async def _send_telegram_for_remaining(self, affected_sellers: set = None, force_all: bool = False):
        """
        Qolgan buyurtmalar uchun Telegram xabar yuborish
        affected_sellers - faqat o'zgargan sotuvchilar uchun xabar yangilash
        force_all - True bo'lsa, 180s filterni o'tkazib yuborish (barcha CHECKING buyurtmalar)

        MUHIM: force_all=False bo'lsa faqat 180 soniyadan oshgan buyurtmalarni Telegram ga yuborish
        """
        if not self.notification_manager:
            return

        # Avval pending deletions ni qayta urinish
        await self.notification_manager.retry_pending_deletions()

        # Hozirgi TEKSHIRILMOQDA dagi barcha buyurtmalarni olish
        leads = await self.nonbor.get_leads_by_status()
        if not leads:
            return

        order_ids = [lead["id"] for lead in leads]

        if force_all:
            # BARCHA CHECKING buyurtmalarni yuborish (180s filtersiz)
            old_order_ids = order_ids
        else:
            # Faqat 180 soniyadan oshgan buyurtmalarni filterlash
            now = datetime.now()
            old_order_ids = []
            for order_id in order_ids:
                if order_id in self.state.order_timestamps:
                    order_age = (now - self.state.order_timestamps[order_id]).total_seconds()
                    if order_age >= self.telegram_alert_time:  # telegram_alert_time soniyadan oshgan
                        old_order_ids.append(order_id)
                    else:
                        logger.debug(f"Buyurtma #{order_id} hali 180s dan yosh ({order_age:.0f}s), Telegram uchun kutilmoqda")
                else:
                    # Agar vaqt ma'lumoti yo'q bo'lsa, bu buyurtma yangi kelgan - 180s kutish kerak
                    logger.debug(f"Buyurtma #{order_id} uchun timestamp yo'q, Telegram yuborilmaydi (180s kutish kerak)")

        if not old_order_ids:
            logger.info(f"Telegram uchun buyurtmalar yo'q (barcha buyurtmalar 180s dan yangi)")
            return

        # MUHIM: Tekshirish - buyurtmalar ro'yxati o'zgardimi?
        # force_all=True bo'lsa, har doim yangilash (buyurtma qabul/bekor qilinganda)
        current_order_ids = set(old_order_ids)
        if not force_all and current_order_ids == self.state.last_telegram_order_ids:
            logger.info(f"Telegram yangilanmaydi - buyurtmalar o'zgarmagan ({len(old_order_ids)} ta)")
            return

        # Buyurtmalar ro'yxati o'zgardi - yangilash kerak
        added_orders = current_order_ids - self.state.last_telegram_order_ids
        removed_orders = self.state.last_telegram_order_ids - current_order_ids
        if added_orders:
            logger.info(f"Yangi buyurtmalar qo'shildi: {len(added_orders)} ta")
        if removed_orders:
            logger.info(f"Buyurtmalar hal qilindi: {len(removed_orders)} ta")

        logger.info(f"Qolgan buyurtmalar uchun Telegram: {len(old_order_ids)} ta (180s+ eski, jami: {len(order_ids)} ta)")

        # Barcha buyurtmalarni olish (faqat 180s+ eski)
        # MUHIM: Avval keshdan olish, keyin API dan
        all_orders = []
        for order_id in old_order_ids:
            try:
                # 1. Avval guruh xabarlari keshidan olish
                if order_id in self._group_order_messages:
                    cached = self._group_order_messages[order_id]
                    cached_data = cached.get("order_data", {})
                    # Keshdan olingan ma'lumotlarni to'ldirish
                    order_data = {
                        "lead_id": order_id,
                        "order_number": cached_data.get("order_number", str(order_id)),
                        "client_name": cached_data.get("client_name", "Noma'lum"),
                        "client_phone": cached_data.get("client_phone", "Noma'lum"),
                        "product_name": cached_data.get("product_name", "Noma'lum"),
                        "quantity": cached_data.get("quantity", 1),
                        "price": cached_data.get("price", 0),
                        "seller_name": cached_data.get("seller_name", "Noma'lum"),
                        "seller_phone": "Noma'lum",
                        "seller_address": "Noma'lum",
                        "delivery_time": cached_data.get("delivery_time", ""),
                    }
                    # Sotuvchi ma'lumotlarini API dan olish (business_id orqali)
                    biz_id = cached.get("biz_id")
                    if biz_id and hasattr(self.nonbor, '_businesses_cache'):
                        for biz in self.nonbor._businesses_cache.values():
                            if str(biz.get("id")) == str(biz_id):
                                order_data["seller_name"] = biz.get("title", "Noma'lum")
                                order_data["seller_address"] = biz.get("address", "Noma'lum")
                                phone = biz.get("phone_number", "")
                                if phone:
                                    order_data["seller_phone"] = f"+{phone}" if not str(phone).startswith("+") else phone
                                break
                    all_orders.append(order_data)
                    logger.debug(f"Buyurtma #{order_id} keshdan olindi")
                else:
                    # 2. Keshda yo'q - API dan olish
                    order_data = await self.nonbor.get_order_full_data(order_id)
                    all_orders.append(order_data)
            except Exception as e:
                logger.error(f"Buyurtma #{order_id} ma'lumotini olishda xato: {e}")

        # Sotuvchi bo'yicha guruhlash
        sellers = {}
        for order in all_orders:
            seller_phone = order.get("seller_phone", "Noma'lum")
            if seller_phone not in sellers:
                sellers[seller_phone] = {
                    "seller_name": order.get("seller_name", "Noma'lum"),
                    "seller_phone": seller_phone,
                    "seller_address": order.get("seller_address", "Noma'lum"),
                    "orders": [],
                    "chat_id": None,  # Biznes guruh chat_id
                }
            sellers[seller_phone]["orders"].append(order)

            # Biznes guruh chat_id ni aniqlash (buyurtma order_id orqali)
            if not sellers[seller_phone]["chat_id"]:
                order_id = order.get("lead_id") or order.get("order_number")
                if order_id and order_id in self._group_order_messages:
                    biz_id = self._group_order_messages[order_id].get("biz_id")
                    if biz_id and biz_id in self.stats_handler._business_groups:
                        sellers[seller_phone]["chat_id"] = self.stats_handler._business_groups[biz_id]

        # MUHIM: Mavjud xabarlarni yangilash yoki yangi yuborish
        if not hasattr(self.notification_manager, '_combined_message_id'):
            self.notification_manager._combined_message_id = None

        existing_seller_msgs = dict(self.notification_manager._seller_message_ids) if self.notification_manager._seller_message_ids else {}

        # Endi ro'yxatda bo'lmagan sotuvchilarning xabarlarini o'chirish
        removed_sellers = set(existing_seller_msgs.keys()) - set(sellers.keys())
        for seller_phone in removed_sellers:
            msg_id = existing_seller_msgs[seller_phone]
            try:
                success = await self.telegram.delete_message(msg_id)
                if success:
                    if msg_id in self.notification_manager._active_message_ids:
                        self.notification_manager._active_message_ids.remove(msg_id)
                    logger.info(f"Sotuvchi xabari o'chirildi (buyurtma yo'q): {seller_phone} ({msg_id})")
                else:
                    if msg_id not in self.notification_manager._pending_deletions:
                        self.notification_manager._pending_deletions.append(msg_id)
            except Exception as e:
                if msg_id not in self.notification_manager._pending_deletions:
                    self.notification_manager._pending_deletions.append(msg_id)
                logger.error(f"Sotuvchi xabarini o'chirishda xato: {e}")
            del self.notification_manager._seller_message_ids[seller_phone]

        # Har bir sotuvchi uchun: mavjud xabarni YANGILASH yoki YANGI yuborish
        try:
            total_orders = sum(len(s["orders"]) for s in sellers.values())
            logger.info(f"Sotuvchilar xabarlarini yangilash: {len(sellers)} ta sotuvchi, {total_orders} ta buyurtma")

            for seller_phone, seller_data in sellers.items():
                # Har bir sotuvchi uchun o'z qo'ng'iroq urinishlari soni
                seller_attempts = self._seller_call_attempts.get(seller_phone, 0)

                # Alert xabari ADMIN guruhga yuboriladi (default chat_id)
                biz_chat_id = None  # Admin guruhga

                # Qo'ng'iroq holati izohi
                if seller_phone == "Noma'lum" or seller_phone not in self._seller_call_attempts:
                    call_note = "📵 Telefon raqami topilmadi"
                elif self._seller_call_answered.get(seller_phone, False):
                    call_note = f"✅ {seller_attempts}-qo'ng'iroqda javob berdi"
                else:
                    call_note = f"📞 {seller_attempts} marta qo'ng'iroq qilindi, javob yo'q"

                if seller_phone in existing_seller_msgs:
                    # Mavjud xabarni TAHRIRLASH (faqat buyurtma soni o'zgaradi)
                    msg_id = existing_seller_msgs[seller_phone]
                    success = await self.telegram.update_seller_orders_alert(
                        message_id=msg_id,
                        seller_orders=seller_data,
                        call_attempts=seller_attempts,
                        chat_id=biz_chat_id,
                        call_note=call_note
                    )
                    if success:
                        logger.info(f"Sotuvchi xabari yangilandi: {seller_phone} ({msg_id}), chat={biz_chat_id}, buyurtmalar: {len(seller_data['orders'])}, urinishlar: {seller_attempts}")
                    else:
                        # Edit ishlamadi - avval eskisini O'CHIRISH, keyin yangi yuborish
                        logger.warning(f"Xabar tahrirlanmadi, eskisi o'chirilib yangi yuborilmoqda: {seller_phone}")
                        try:
                            await self.telegram.delete_message(msg_id, chat_id=biz_chat_id)
                            if msg_id in self.notification_manager._active_message_ids:
                                self.notification_manager._active_message_ids.remove(msg_id)
                        except Exception:
                            pass
                        new_msg_id = await self.telegram.send_seller_orders_alert(
                            seller_data, seller_attempts, chat_id=biz_chat_id, call_note=call_note
                        )
                        if new_msg_id:
                            self.notification_manager._seller_message_ids[seller_phone] = new_msg_id
                            if new_msg_id not in self.notification_manager._active_message_ids:
                                self.notification_manager._active_message_ids.append(new_msg_id)
                else:
                    # YANGI sotuvchi - yangi xabar yuborish
                    new_msg_id = await self.telegram.send_seller_orders_alert(
                        seller_data, seller_attempts, chat_id=biz_chat_id, call_note=call_note
                    )
                    if new_msg_id:
                        self.notification_manager._seller_message_ids[seller_phone] = new_msg_id
                        if new_msg_id not in self.notification_manager._active_message_ids:
                            self.notification_manager._active_message_ids.append(new_msg_id)
                        logger.info(f"Yangi sotuvchi xabari: {seller_phone} ({new_msg_id}), chat={biz_chat_id}, urinishlar: {seller_attempts}")

            # Combined message ID yangilash (birinchi sotuvchi xabari)
            if self.notification_manager._seller_message_ids:
                first_msg = list(self.notification_manager._seller_message_ids.values())[0]
                self.notification_manager._combined_message_id = first_msg

            self.notification_manager._save_messages()
            logger.info(f"Xabarlar yangilandi: {len(self.notification_manager._seller_message_ids)} ta sotuvchi")

        except Exception as e:
            logger.error(f"Sotuvchi xabarlarini yangilashda xato: {e}")

        # Oxirgi yuborilgan buyurtmalar ro'yxatini yangilash
        self.state.last_telegram_order_ids = current_order_ids
        self.state.telegram_notified = True  # Birinchi Telegram yuborildi - keyingi o'zgarishlarda darhol yangilanadi
        logger.debug(f"Oxirgi Telegram buyurtmalar ro'yxati yangilandi: {len(current_order_ids)} ta")

        # 180s timer davom etadi - buyurtmalar uchun
        logger.info("Telegram xabar yuborildi, 180s timer davom etmoqda")

    # DEPRECATED: _send_new_order_alert endi ishlatilmaydi
    # Yangi buyurtmalar uchun 180s timer kutiladi - _check_and_process() da
    # Eski kod saqlab qolindi - kelajakda kerak bo'lishi mumkin
    #
    # async def _send_new_order_alert(self, new_order_ids: list):
    #     """
    #     Yangi buyurtma kelganda TEKSHIRILMOQDA status xabarini yangilash
    #     ENDI ISHLATILMAYDI - 180s timer orqali yuboriladi
    #     """
    #     pass


async def main():
    """Asosiy funksiya"""

    # Platformaga qarab default AMI host
    # Windows (WSL) = 172.29.124.85, Linux (prod) = 127.0.0.1
    default_ami_host = "172.29.124.85" if os.name == "nt" else "127.0.0.1"

    # SKIP_ASTERISK - Windows da avtomatik True, Linux da False
    # .env da o'zgartirish mumkin
    default_skip = "true" if os.name == "nt" else "false"
    skip_asterisk = os.getenv("SKIP_ASTERISK", default_skip).lower() in ("true", "1", "yes")

    logger.info(f"Platform: {'Windows' if os.name == 'nt' else 'Linux'}")
    logger.info(f"Asterisk: {'O`chirilgan (faqat Telegram)' if skip_asterisk else 'Faol (to`liq rejim)'}")

    autodialer = AutodialerPro(
        # Asterisk AMI
        sip_host=os.getenv("AMI_HOST", default_ami_host),
        ami_port=int(os.getenv("AMI_PORT", "5038")),
        ami_username=os.getenv("AMI_USERNAME", "autodialer"),
        ami_password=os.getenv("AMI_PASSWORD", "autodialer123"),

        # Telegram
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),

        # Sotuvchi
        seller_phone=os.getenv("SELLER_PHONE", "+998901009300"),

        # Vaqtlar
        wait_before_call=int(os.getenv("WAIT_BEFORE_CALL", "90")),
        telegram_alert_time=int(os.getenv("TELEGRAM_ALERT_TIME", "180")),
        max_call_attempts=int(os.getenv("MAX_CALL_ATTEMPTS", "2")),
        retry_interval=int(os.getenv("RETRY_INTERVAL", "30")),
        planned_reminder_time=int(os.getenv("PLANNED_REMINDER_TIME", "60")),

        # Platform
        skip_asterisk=skip_asterisk,
    )

    await autodialer.start()


if __name__ == "__main__":
    asyncio.run(main())
