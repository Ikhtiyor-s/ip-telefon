"""
Statistika Servisi
==================

Qo'ng'iroqlar va buyurtmalar statistikasini saqlash va ko'rsatish
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class CallResult(Enum):
    """Qo'ng'iroq natijasi"""
    ANSWERED = "answered"           # Javob berildi
    NO_ANSWER = "no_answer"         # Javob berilmadi
    BUSY = "busy"                   # Band
    FAILED = "failed"               # Muvaffaqiyatsiz


class OrderResult(Enum):
    """Buyurtma natijasi"""
    ACCEPTED = "accepted"           # Qabul qilindi
    REJECTED = "rejected"           # Bekor qilindi
    PENDING = "pending"             # Kutilmoqda


@dataclass
class CallRecord:
    """Qo'ng'iroq yozuvi"""
    phone: str
    seller_name: str
    order_count: int
    attempts: int
    result: str  # CallResult value
    timestamp: str
    order_ids: List[int] = field(default_factory=list)
    started_at: str = ""        # qo'ng'iroq boshlangan vaqt (ISO)
    answered_at: str = ""       # javob berilgan vaqt (ISO)
    wait_seconds: int = 0       # ring vaqti (soniya)
    duration_seconds: int = 0   # gaplashish vaqti (soniya)
    recording_file: str = ""    # yozuv fayl nomi (extension'siz)
    recording_url: str = ""     # yozuv URL (tashqi servis uchun)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CallRecord":
        # Eski ma'lumotlar uchun yangi maydonlarni qo'shish
        for f in ("started_at", "answered_at", "recording_file", "recording_url"):
            data.setdefault(f, "")
        for f in ("wait_seconds", "duration_seconds"):
            data.setdefault(f, 0)
        return cls(**data)


@dataclass
class OrderRecord:
    """Buyurtma yozuvi"""
    order_id: int
    order_number: str  # AmoCRM buyurtma raqami (#1570)
    seller_name: str
    seller_phone: str
    client_name: str
    product_name: str
    price: int
    result: str  # OrderResult value
    call_attempts: int
    telegram_sent: bool
    timestamp: str
    order_status: str = ""  # API dan kelgan asl status (ACCEPT_EXPIRED, CANCELLED_SELLER, etc.)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OrderRecord":
        # Eski ma'lumotlar uchun order_number ni qo'shish
        if "order_number" not in data:
            data["order_number"] = str(data.get("order_id", ""))
        return cls(**data)


@dataclass
class DailyStats:
    """Kunlik statistika"""
    date: str
    total_calls: int = 0
    answered_calls: int = 0
    unanswered_calls: int = 0
    calls_1_attempt: int = 0      # 1 ta urinishda javob berdi
    calls_2_attempts: int = 0     # 2 ta urinishda javob berdi
    calls_3_attempts: int = 0     # 3+ ta urinishda javob berdi
    unanswered_1_attempt: int = 0  # 1 urinishdan keyin javobsiz
    unanswered_2_attempts: int = 0 # 2 urinishdan keyin javobsiz
    unanswered_3_attempts: int = 0 # 3+ urinishdan keyin javobsiz
    total_orders: int = 0
    accepted_orders: int = 0      # Qabul qilingan
    rejected_orders: int = 0      # Bekor qilingan
    accepted_without_telegram: int = 0  # Telegram yuborilmasdan qabul qilindi
    call_records: List[dict] = field(default_factory=list)
    order_records: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DailyStats":
        # Faqat DailyStats maydonlari qabul qilinadi (eski/noma'lum maydonlar e'tiborsiz)
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class StatsService:
    """
    Statistika servisi

    Kunlik statistikalarni saqlaydi va ko'rsatadi
    """

    def __init__(self, data_dir: str = "data", webhook_service=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.stats_file = self.data_dir / "stats.json"
        self._webhook = webhook_service

        # Joriy kunlik statistika
        self._today_stats: Optional[DailyStats] = None
        self._all_stats: Dict[str, DailyStats] = {}

        self._load_stats()
        logger.info("StatsService ishga tushdi")

    def _load_stats(self):
        """Statistikalarni fayldan yuklash"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for date_str, stats_data in data.items():
                        self._all_stats[date_str] = DailyStats.from_dict(stats_data)
                logger.info(f"Statistika yuklandi: {len(self._all_stats)} kun")
            except Exception as e:
                logger.error(f"Statistika yuklashda xato: {e}")
                self._all_stats = {}

        # Bugungi statistika
        today = date.today().isoformat()
        if today not in self._all_stats:
            self._all_stats[today] = DailyStats(date=today)
        self._today_stats = self._all_stats[today]

    def _save_stats(self):
        """Statistikalarni faylga saqlash"""
        try:
            data = {date_str: stats.to_dict() for date_str, stats in self._all_stats.items()}
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Statistika saqlashda xato: {e}")

    def _ensure_today(self):
        """Bugungi statistika mavjudligini tekshirish"""
        today = date.today().isoformat()
        if self._today_stats is None or self._today_stats.date != today:
            if today not in self._all_stats:
                self._all_stats[today] = DailyStats(date=today)
            self._today_stats = self._all_stats[today]

    def record_call(
        self,
        phone: str,
        seller_name: str,
        order_count: int,
        attempts: int,
        result: CallResult,
        order_ids: List[int] = None,
        started_at: str = "",
        answered_at: str = "",
        wait_seconds: int = 0,
        duration_seconds: int = 0,
        recording_file: str = "",
        recording_url: str = "",
    ):
        """Qo'ng'iroqni qayd etish"""
        self._ensure_today()

        record = CallRecord(
            phone=phone,
            seller_name=seller_name,
            order_count=order_count,
            attempts=attempts,
            result=result.value,
            timestamp=datetime.now().isoformat(),
            order_ids=order_ids or [],
            started_at=started_at,
            answered_at=answered_at,
            wait_seconds=wait_seconds,
            duration_seconds=duration_seconds,
            recording_file=recording_file,
            recording_url=recording_url,
        )

        self._today_stats.call_records.append(record.to_dict())
        self._today_stats.total_calls += 1

        if result == CallResult.ANSWERED:
            self._today_stats.answered_calls += 1
            if attempts == 1:
                self._today_stats.calls_1_attempt += 1
            elif attempts == 2:
                self._today_stats.calls_2_attempts += 1
            else:
                self._today_stats.calls_3_attempts += 1
        else:
            self._today_stats.unanswered_calls += 1
            if attempts == 1:
                self._today_stats.unanswered_1_attempt += 1
            elif attempts == 2:
                self._today_stats.unanswered_2_attempts += 1
            else:
                self._today_stats.unanswered_3_attempts += 1

        self._save_stats()
        logger.info(f"Qo'ng'iroq qayd etildi: {phone} - {result.value} ({attempts} urinish)")

        if self._webhook:
            self._webhook.schedule_event("call.completed", record.to_dict())

    def record_order(
        self,
        order_id: int,
        order_number: str,
        seller_name: str,
        seller_phone: str,
        client_name: str,
        product_name: str,
        price: int,
        result: OrderResult,
        call_attempts: int,
        telegram_sent: bool,
        order_status: str = ""
    ):
        """Buyurtmani qayd etish"""
        self._ensure_today()

        record = OrderRecord(
            order_id=order_id,
            order_number=order_number,
            seller_name=seller_name,
            seller_phone=seller_phone,
            client_name=client_name,
            product_name=product_name,
            price=price,
            result=result.value,
            call_attempts=call_attempts,
            telegram_sent=telegram_sent,
            timestamp=datetime.now().isoformat(),
            order_status=order_status
        )

        self._today_stats.order_records.append(record.to_dict())
        self._today_stats.total_orders += 1

        if result == OrderResult.ACCEPTED:
            self._today_stats.accepted_orders += 1
            if not telegram_sent:
                self._today_stats.accepted_without_telegram += 1
        elif result == OrderResult.REJECTED:
            self._today_stats.rejected_orders += 1

        self._save_stats()
        logger.info(f"Buyurtma qayd etildi: #{order_number} - {result.value}")

        if self._webhook:
            self._webhook.schedule_event("order.updated", record.to_dict())

    def get_today_stats(self) -> DailyStats:
        """Bugungi statistikani olish"""
        self._ensure_today()
        return self._today_stats

    def get_stats_by_date(self, date_str: str) -> Optional[DailyStats]:
        """Berilgan sana statistikasini olish"""
        return self._all_stats.get(date_str)

    def get_calls_by_attempts(self, attempts: int) -> List[CallRecord]:
        """Berilgan urinishlar soniga ko'ra qo'ng'iroqlar"""
        self._ensure_today()
        return [
            CallRecord.from_dict(r) for r in self._today_stats.call_records
            if r["attempts"] == attempts and r["result"] == CallResult.ANSWERED.value
        ]

    def get_orders_by_result(self, result: OrderResult) -> List[OrderRecord]:
        """Berilgan natijaga ko'ra buyurtmalar"""
        self._ensure_today()
        return [
            OrderRecord.from_dict(r) for r in self._today_stats.order_records
            if r["result"] == result.value
        ]

    def get_orders_accepted_without_telegram(self) -> List[OrderRecord]:
        """Telegram yuborilmasdan qabul qilingan buyurtmalar"""
        self._ensure_today()
        return [
            OrderRecord.from_dict(r) for r in self._today_stats.order_records
            if r["result"] == OrderResult.ACCEPTED.value and not r["telegram_sent"]
        ]

    def get_summary_message(self) -> str:
        """Statistika xulosa xabari"""
        self._ensure_today()
        stats = self._today_stats

        return f"""📊 *BUGUNGI STATISTIKA*
━━━━━━━━━━━━━━━━━━━━

📞 *QO'NG'IROQLAR:* {stats.total_calls} ta
├ ✅ Javob berildi: {stats.answered_calls}
├ ❌ Javob berilmadi: {stats.unanswered_calls}
├ 1️⃣ 1-urinishda: {stats.calls_1_attempt}
├ 2️⃣ 2-urinishda: {stats.calls_2_attempts}
└ 3️⃣ 3+ urinishda: {stats.calls_3_attempts}

📦 *BUYURTMALAR:* {stats.total_orders} ta
├ ✅ Qabul qilindi: {stats.accepted_orders}
├ ❌ Bekor qilindi: {stats.rejected_orders}
└ 🚀 Telegram'siz qabul: {stats.accepted_without_telegram}

📅 Sana: {stats.date}"""

    def reset_today(self):
        """Bugungi statistikani tozalash"""
        today = date.today().isoformat()
        self._all_stats[today] = DailyStats(date=today)
        self._today_stats = self._all_stats[today]
        self._save_stats()
        logger.info("Bugungi statistika tozalandi")

    def get_period_stats(self, period: str) -> DailyStats:
        """
        Davr bo'yicha statistikani olish

        Args:
            period: "daily", "weekly", "monthly", "yearly"

        Returns:
            DailyStats: Jami statistika
        """
        today = date.today()

        if period == "daily":
            start_date = today
        elif period == "weekly":
            start_date = today - timedelta(days=7)
        elif period == "monthly":
            start_date = today - timedelta(days=30)
        elif period == "yearly":
            start_date = today - timedelta(days=365)
        else:
            start_date = today

        # Jami statistikani hisoblash
        combined = DailyStats(date=f"{start_date.isoformat()} - {today.isoformat()}")

        for date_str, stats in self._all_stats.items():
            try:
                stat_date = date.fromisoformat(date_str)
                if start_date <= stat_date <= today:
                    combined.total_calls += stats.total_calls
                    combined.answered_calls += stats.answered_calls
                    combined.unanswered_calls += stats.unanswered_calls
                    combined.calls_1_attempt += stats.calls_1_attempt
                    combined.calls_2_attempts += stats.calls_2_attempts
                    combined.calls_3_attempts += stats.calls_3_attempts
                    combined.total_orders += stats.total_orders
                    combined.accepted_orders += stats.accepted_orders
                    combined.rejected_orders += stats.rejected_orders
                    combined.accepted_without_telegram += stats.accepted_without_telegram
                    combined.call_records.extend(stats.call_records)
                    combined.order_records.extend(stats.order_records)
            except ValueError:
                continue

        return combined

    def get_period_calls_by_attempts(self, period: str, attempts: int) -> List[CallRecord]:
        """Davr bo'yicha urinishlar soniga ko'ra qo'ng'iroqlar"""
        stats = self.get_period_stats(period)
        return [
            CallRecord.from_dict(r) for r in stats.call_records
            if r["attempts"] == attempts and r["result"] == CallResult.ANSWERED.value
        ]

    def get_period_orders_by_result(self, period: str, result: OrderResult) -> List[OrderRecord]:
        """Davr bo'yicha natijaga ko'ra buyurtmalar"""
        stats = self.get_period_stats(period)
        return [
            OrderRecord.from_dict(r) for r in stats.order_records
            if r["result"] == result.value
        ]

    def get_period_orders_without_telegram(self, period: str) -> List[OrderRecord]:
        """Davr bo'yicha Telegram'siz qabul qilingan buyurtmalar"""
        stats = self.get_period_stats(period)
        return [
            OrderRecord.from_dict(r) for r in stats.order_records
            if r["result"] == OrderResult.ACCEPTED.value and not r["telegram_sent"]
        ]
