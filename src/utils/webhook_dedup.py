"""
Webhook event_id dedup — LRU + TTL.

Nonbor v2 webhook spec'ida har bir event noyob event_id ga ega bo'ladi
(X-Webhook-Id header yoki body.event_id). Sender retry qilganda bir xil
event_id qayta keladi — uni ikki marta ishlamaslik uchun bu modul ishlatiladi.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict


class EventDedup:
    """Thread-safe LRU dedup with per-entry TTL.

    Defolt: 10000 ta event_id, har biri 1 soat saqlanadi. Sender 5 marta retry
    qilsa ham (eng oxirgi urinish ~1 soatdan keyin), shu oraliqda dedup ishlaydi.
    """

    def __init__(self, maxsize: int = 10000, ttl_seconds: int = 3600) -> None:
        self._store: "OrderedDict[str, float]" = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def seen(self, event_id: str) -> bool:
        """True qaytarsa — bu event_id allaqachon ko'rilgan (dedup kerak)."""
        if not event_id:
            return False

        now = time.time()
        with self._lock:
            # Insertion tartibi = timestamp tartibi (move_to_end ishlatmaymiz),
            # shuning uchun oldindan TTL chiqgan yozuvlarni boshidan o'chirish kifoya
            while self._store:
                oldest_key, oldest_ts = next(iter(self._store.items()))
                if now - oldest_ts > self._ttl:
                    del self._store[oldest_key]
                else:
                    break

            if event_id in self._store:
                return True

            self._store[event_id] = now
            if len(self._store) > self._maxsize:
                self._store.popitem(last=False)
            return False

    def size(self) -> int:
        with self._lock:
            return len(self._store)
