"""
Buyurtma modeli
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class OrderStatus(Enum):
    """Buyurtma statuslari"""
    TEKSHIRILMOQDA = "TEKSHIRILMOQDA"
    QABUL_QILINDI = "QABUL_QILINDI"
    RAD_ETILDI = "RAD_ETILDI"
    JARAYONDA = "JARAYONDA"
    YETKAZILDI = "YETKAZILDI"


@dataclass
class Order:
    """Buyurtma ma'lumotlari"""
    id: int
    lead_id: int
    status: OrderStatus
    customer_name: str
    customer_phone: str
    created_at: datetime
    updated_at: datetime
    responsible_user_id: Optional[int] = None
    price: float = 0.0
    notes: str = ""



@dataclass
class CallAttempt:
    """Qo'ng'iroq urinishi"""
    id: int = field(default_factory=lambda: int(datetime.now().timestamp()))
    phone: str = ""
    attempt_number: int = 1
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: str = "pending"  # pending, calling, answered, no_answer, failed
    duration: int = 0
    orders_count: int = 0

    def start(self):
        """Qo'ng'iroqni boshlash"""
        self.started_at = datetime.now()
        self.status = "calling"

    def end(self, status: str, duration: int = 0):
        """Qo'ng'iroqni tugatish"""
        self.ended_at = datetime.now()
        self.status = status
        self.duration = duration


@dataclass
class NotificationState:
    """Bildirishnoma holati"""
    telegram_message_id: Optional[int] = None
    last_orders_count: int = 0
    last_notified_at: Optional[datetime] = None
    call_attempts: int = 0
    is_resolved: bool = False

    def reset(self):
        """Holatni tozalash"""
        self.telegram_message_id = None
        self.last_orders_count = 0
        self.last_notified_at = None
        self.call_attempts = 0
        self.is_resolved = False
