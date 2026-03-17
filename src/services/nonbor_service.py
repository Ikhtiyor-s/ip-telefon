"""
Nonbor API Integratsiya Servisi
Buyurtmalarni kuzatish va boshqarish (amoCRM o'rniga)
"""

import logging
import os
import aiohttp
import asyncio
from typing import List, Dict, Optional, Set
from datetime import datetime

logger = logging.getLogger(__name__)

NONBOR_BASE_URL = os.getenv("NONBOR_BASE_URL", "https://prod.nonbor.uz/api/v2")
NONBOR_SECRET = os.getenv("NONBOR_SECRET", "nonbor-secret-key")
# Domain ni base_url dan olish (orders endpoint uchun)
NONBOR_DOMAIN = NONBOR_BASE_URL.split("/api/")[0]  # https://prod.nonbor.uz


class NonborService:
    """
    Nonbor API bilan ishlash servisi

    Funksiyalar:
    - Yangi buyurtmalarni kuzatish (CHECKING status)
    - Buyurtmalar sonini olish
    - Bizneslar (sotuvchilar) ma'lumotlarini olish
    - Status o'zgarishlarini kuzatish
    """

    def __init__(self, status_name: str = "CHECKING"):
        self.status_name = status_name
        self.base_url = NONBOR_BASE_URL
        self.headers = {
            "accept": "application/json",
            "X-Telegram-Bot-Secret": NONBOR_SECRET,
        }

        # Cache
        self._known_leads: Set[int] = set()
        self._businesses_cache: Dict[int, Dict] = {}
        self._seller_id_cache: Dict[str, int] = {}  # phone -> seller_id
        self._session: Optional[aiohttp.ClientSession] = None
        self._consecutive_errors: int = 0

        # Orders cache - bir polling intervalda faqat 1 ta API so'rov
        self._orders_cache: Optional[List[Dict]] = None
        self._orders_cache_time: float = 0
        self._orders_cache_ttl: float = 4.0  # 4 soniya cache (5s polling dan kam)

        logger.info(f"Nonbor servisi ishga tushdi")

    async def _get_session(self) -> aiohttp.ClientSession:
        """HTTP session olish"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def close(self):
        """Sessionni yopish"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _recreate_session(self):
        """Session ni yopib qayta yaratish (stale connection fix)"""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None
        logger.info("HTTP session qayta yaratildi (stale connection fix)")

    async def _make_request(self, method: str, endpoint: str) -> Optional[dict]:
        """API so'rov yuborish"""
        # 10 ta ketma-ket xatodan keyin session ni qayta yaratish
        if self._consecutive_errors > 0 and self._consecutive_errors % 10 == 0:
            await self._recreate_session()

        session = await self._get_session()
        url = f"{self.base_url}/{endpoint}"
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with session.request(method, url, timeout=timeout) as response:
                if response.status == 200:
                    self._consecutive_errors = 0  # Muvaffaqiyatli - reset
                    return await response.json()
                else:
                    self._consecutive_errors += 1
                    logger.error(f"Nonbor API xatosi: {response.status}")
                    return None
        except asyncio.TimeoutError:
            self._consecutive_errors += 1
            if self._consecutive_errors <= 3 or self._consecutive_errors % 10 == 0:
                logger.error(f"Nonbor API timeout: {endpoint} (ketma-ket: {self._consecutive_errors})")
            return None
        except aiohttp.ClientError as e:
            self._consecutive_errors += 1
            if self._consecutive_errors <= 3 or self._consecutive_errors % 10 == 0:
                logger.error(f"Nonbor API ulanish xatosi: {e} (ketma-ket: {self._consecutive_errors})")
            return None

    async def get_businesses(self) -> List[Dict]:
        """Barcha tasdiqlangan bizneslarni olish"""
        data = await self._make_request("GET", "telegram_bot/businesses/accepted/")
        if not data or not data.get("success"):
            return []

        businesses = data.get("result", [])
        # Cache yangilash
        for biz in businesses:
            self._businesses_cache[biz["id"]] = biz

        return businesses

    async def get_order_status(self, order_id: int) -> Optional[str]:
        """
        Bitta buyurtmaning haqiqiy statusini olish
        Admin panel endpointidan foydalanish: /orders/{id}/

        Returns:
            Status string (masalan: "CHECKING", "ACCEPTED", "CANCELLED") yoki None
        """
        session = await self._get_session()
        # Admin panel /orders/{id}/ endpointini ishlatish (base_url siz)
        url = f"{NONBOR_DOMAIN}/orders/{order_id}/"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        state = data.get("state") or data.get("result", {}).get("state")
                        if state:
                            return state.upper()
                else:
                    logger.debug(f"Order status endpoint: {response.status}")
        except Exception as e:
            logger.debug(f"Order status endpoint xato: {e}")
        return None

    async def get_order_details(self, order_id: int) -> Optional[Dict]:
        """
        Bitta buyurtmaning to'liq ma'lumotlarini olish
        /orders/{id}/ endpoint - telefon raqami va boshqa qo'shimcha ma'lumotlar uchun

        Returns:
            To'liq buyurtma ma'lumotlari dict yoki None
        """
        session = await self._get_session()
        url = f"{NONBOR_DOMAIN}/orders/{order_id}/"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        logger.debug(f"Order #{order_id} details: {list(data.keys())}")
                        return data
                else:
                    logger.debug(f"Order details endpoint: {response.status}")
        except Exception as e:
            logger.debug(f"Order details endpoint xato: {e}")
        return None

    async def get_orders(self, force: bool = False) -> Optional[List[Dict]]:
        """
        Barcha buyurtmalarni olish (cache bilan - 4s TTL)

        Args:
            force: True bo'lsa cache ni e'tiborsiz qoldiradi

        Returns:
            Buyurtmalar ro'yxati yoki None agar xato yuz bergan bo'lsa
        """
        import time
        now = time.monotonic()

        # Cache dan qaytarish (4s ichida takroriy so'rov bo'lsa)
        if not force and self._orders_cache is not None and (now - self._orders_cache_time) < self._orders_cache_ttl:
            return self._orders_cache

        data = await self._make_request("GET", "telegram_bot/get-order-for-courier/")
        if data is None:
            return None

        if not data.get("success"):
            return None

        results = data.get("result", {}).get("results", [])
        if results:
            states = {}
            for o in results:
                s = o.get("state", "unknown")
                states[s] = states.get(s, 0) + 1
            logger.info(f"API buyurtmalar: {len(results)} ta, statuslar: {states}")

        # Cache ni yangilash
        self._orders_cache = results
        self._orders_cache_time = now
        return results

    async def get_orders_by_business(self, business_id: int) -> List[Dict]:
        """
        Biznes ID bo'yicha buyurtmalarni olish (get-order-for-courier dan)

        Returns:
            Bitta biznesga tegishli buyurtmalar ro'yxati
        """
        orders = await self.get_orders()
        if not orders:
            return []

        # business_id bo'yicha filtrlash
        biz_orders = [
            o for o in orders
            if (o.get("business") or {}).get("id") == business_id
        ]
        logger.info(f"Biznes #{business_id} buyurtmalari: {len(biz_orders)} ta (jami: {len(orders)})")
        return biz_orders

    async def get_leads_by_status(self) -> Optional[List[Dict]]:
        """
        CHECKING statusidagi barcha buyurtmalarni olish
        (AmoCRM get_leads_by_status() bilan mos)

        Returns:
            Buyurtmalar ro'yxati yoki None agar xato yuz bergan bo'lsa
        """
        orders = await self.get_orders()
        if orders is None:
            return None

        # get-order-for-courier/ faqat qabul kutayotgan buyurtmalarni qaytaradi
        # CHECKING yoki PENDING — ikkalasi ham yangi buyurtma (API format o'zgarishi)
        target_statuses = {self.status_name.upper(), "CHECKING", "PENDING"}
        checking_orders = [
            order for order in orders
            if order.get("state", "").upper() in target_statuses
        ]

        logger.debug(f"Yangi buyurtmalar: {len(checking_orders)} ta (jami: {len(orders)})")
        return checking_orders

    async def check_for_new_leads(self) -> tuple:
        """
        Yangi buyurtmalarni tekshirish
        (AmoCRM check_for_new_leads() bilan mos)

        Returns:
            (jami_soni, barcha_hozirgi_order_idlar) or (None, None) agar xato
        """
        try:
            leads = await self.get_leads_by_status()

            if leads is None:
                return None, None

            current_ids = {order["id"] for order in leads}

            # Yangi buyurtmalar (faqat log uchun)
            new_ids = current_ids - self._known_leads

            # Cache yangilash
            self._known_leads = current_ids

            if new_ids:
                logger.info(f"Yangi buyurtmalar: {len(new_ids)} ta, Jami: {len(leads)} ta")

            return len(leads), list(current_ids)
        except Exception as e:
            logger.error(f"check_for_new_leads xatosi: {e}")
            return None, None

    async def get_order_full_data(self, order_id: int) -> Dict:
        """
        Buyurtma uchun to'liq ma'lumotlarni olish
        (AmoCRM get_order_full_data() bilan mos)

        Returns:
            dict: {
                lead_id, lead_name, seller_name, seller_phone, seller_address,
                client_name, client_phone, product_name, quantity, price, order_number
            }
        """
        result = {
            "lead_id": order_id,
            "lead_name": "Noma'lum",
            "seller_name": "Noma'lum",
            "seller_phone": "Noma'lum",
            "seller_address": "Noma'lum",
            "seller_language": "uz",  # Default: o'zbek tili
            "client_name": "Noma'lum",
            "client_phone": "Noma'lum",
            "product_name": "Noma'lum",
            "quantity": 1,
            "price": 0,
            "order_number": str(order_id),
        }

        # Bizneslar cacheni yangilash (agar bo'sh bo'lsa)
        if not self._businesses_cache:
            await self.get_businesses()

        # Buyurtmalarni olish va kerakli buyurtmani topish
        orders = await self.get_orders()
        if not orders:
            return result

        order = None
        for o in orders:
            if o.get("id") == order_id:
                order = o
                break

        if not order:
            # Ro'yxatda topilmadi - individual endpoint dan olish
            order_details = await self.get_order_details(order_id)
            if order_details:
                order = order_details.get("result", order_details)
                logger.info(f"Buyurtma #{order_id} individual endpoint dan olindi")
            else:
                return result

        # Buyurtma ma'lumotlari
        result["lead_id"] = order.get("id", order_id)
        result["order_number"] = str(order.get("id", order_id))
        result["price"] = (order.get("total_price", 0) or 0) / 100

        # Biznes (sotuvchi) ma'lumotlari
        business = order.get("business") or {}
        if business:
            result["business_id"] = business.get("id")
            result["seller_name"] = business.get("title", "Noma'lum")
            result["seller_address"] = business.get("address", "Noma'lum")

            # Biznes telefon raqami va tilini olish (businesses API dan, title bo'yicha)
            biz_title = business.get("title", "")
            if biz_title:
                # Avval cacheda izlash, topilmasa cacheni yangilab qayta izlash
                matched_biz = None
                for attempt in range(2):
                    for cached_biz in self._businesses_cache.values():
                        if cached_biz.get("title", "").strip().lower() == biz_title.strip().lower():
                            matched_biz = cached_biz
                            break
                    if matched_biz:
                        break
                    if attempt == 0:
                        logger.info(f"Biznes '{biz_title}' cacheda topilmadi, cacheni yangilash...")
                        await self.get_businesses()

                if matched_biz:
                    phone = matched_biz.get("phone_number", "")
                    if phone:
                        result["seller_phone"] = f"+{phone}" if not phone.startswith("+") else phone
                    # Biznes egasi tili (ilovada tanlangan)
                    lang = (
                        matched_biz.get("language") or
                        matched_biz.get("owner_language") or
                        matched_biz.get("tg_language") or
                        matched_biz.get("language_code") or
                        "uz"
                    )
                    result["seller_language"] = str(lang).lower()[:2]
                else:
                    logger.warning(f"Biznes '{biz_title}' businesses API da topilmadi (cache: {len(self._businesses_cache)} ta)")

        # Mijoz ma'lumotlari
        user = order.get("user") or {}
        if user:
            first_name = user.get("first_name", "")
            last_name = user.get("last_name", "")
            result["client_name"] = f"{first_name} {last_name}".strip() or "Noma'lum"
            result["client_phone"] = user.get("phone", "Noma'lum")

        # Mahsulotlar
        items = order.get("order_item") or order.get("items") or []
        if items:
            first_item = items[0]
            product = first_item.get("product") or {}
            result["product_name"] = product.get("name") or product.get("title", "Noma'lum")
            result["quantity"] = first_item.get("count", 1)

        # Lead name format (amoCRM bilan mos)
        result["lead_name"] = f"#{order['id']} | {result['client_name']} | {order.get('payment_method', 'CASH')} | {result['price']}"

        return result

    async def get_seller_id(self, phone: str) -> Optional[int]:
        """
        Telefon raqam bo'yicha seller_id olish
        POST /telegram_bot/get_seller_info/ → {username: phone} → result[0].id

        Args:
            phone: Telefon raqami (masalan: +998948679300)

        Returns:
            Seller ID yoki None
        """
        # Cache tekshirish
        if phone in self._seller_id_cache:
            return self._seller_id_cache[phone]

        # + belgisini olib tashlash
        username = phone.lstrip("+")

        session = await self._get_session()
        url = f"{self.base_url}/telegram_bot/get_seller_info/"
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            async with session.post(url, json={"username": username}, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    result_list = data.get("result", [])
                    if data.get("success") and isinstance(result_list, list) and len(result_list) > 0:
                        seller_id = result_list[0].get("id")
                        if seller_id:
                            self._seller_id_cache[phone] = seller_id
                            logger.info(f"Seller ID topildi: {phone} -> {seller_id}")
                            return seller_id
                else:
                    logger.error(f"get_seller_info xato: {response.status}")
        except Exception as e:
            logger.error(f"get_seller_info xato: {e}")
        return None

    async def get_seller_orders(self, seller_id: int) -> List[Dict]:
        """
        Seller buyurtmalarini olish
        GET /telegram_bot/sellers/{seller_id}/orders/

        Returns:
            Buyurtmalar ro'yxati
        """
        data = await self._make_request("GET", f"telegram_bot/sellers/{seller_id}/orders/")
        if not data or not data.get("success"):
            return []

        orders = data.get("result", [])
        logger.info(f"Seller #{seller_id} buyurtmalari: {len(orders)} ta")
        return orders

    async def get_seller_order_detail(self, seller_id: int, order_id: int) -> Optional[Dict]:
        """
        Bitta buyurtmaning batafsil ma'lumotlarini olish
        GET /telegram_bot/sellers/{seller_id}/orders/{order_id}/

        Returns:
            Buyurtma tafsilotlari (items, payment_method, delivery_method) yoki None
        """
        data = await self._make_request("GET", f"telegram_bot/sellers/{seller_id}/orders/{order_id}/")
        if not data or not data.get("success"):
            return None

        results = data.get("result", [])
        if results:
            return results[0]
        return None

    async def get_seller_orders_details_batch(self, seller_id: int, order_ids: List[int]) -> Dict[int, Dict]:
        """
        Bir nechta buyurtmaning batafsil ma'lumotlarini parallel olish

        Returns:
            {order_id: detail_dict} mapping
        """
        async def fetch_one(oid):
            detail = await self.get_seller_order_detail(seller_id, oid)
            return oid, detail

        results = await asyncio.gather(*[fetch_one(oid) for oid in order_ids], return_exceptions=True)

        details = {}
        for r in results:
            if isinstance(r, tuple) and r[1] is not None:
                details[r[0]] = r[1]
        return details

    async def get_seller_order_counts(self, seller_id: int) -> Optional[Dict]:
        """
        Seller buyurtmalari status bo'yicha sonini olish
        GET /telegram_bot/sellers/{seller_id}/orders/status_count/

        Returns:
            Status counts dict yoki None
        """
        data = await self._make_request("GET", f"telegram_bot/sellers/{seller_id}/orders/status_count/")
        if not data or not data.get("success"):
            return None

        return data.get("result", {})

    async def get_orders_by_business(self, business_id: int) -> List[Dict]:
        """
        Biznes ID bo'yicha buyurtmalarni olish (API dan)

        Args:
            business_id: Biznes ID

        Returns:
            Buyurtmalar ro'yxati (bo'sh list agar xato yuz bersa)
        """
        orders = await self.get_orders()
        if not orders:
            return []

        # Biznes ID bo'yicha filtrlash
        business_orders = [
            order for order in orders
            if (order.get("business") or {}).get("id") == business_id
        ]

        logger.info(f"Biznes #{business_id} buyurtmalari: {len(business_orders)} ta (jami: {len(orders)})")
        return business_orders

    def reset_known_leads(self):
        """Ko'rilgan buyurtmalar ro'yxatini tozalash"""
        self._known_leads.clear()
        logger.debug("Known leads tozalandi")


class NonborPoller:
    """
    Nonbor API Polling - Real-time kuzatish
    (AmoCRMPoller bilan mos)

    Yangi buyurtmalar va status o'zgarishlarini kuzatadi
    """

    def __init__(
        self,
        nonbor_service: NonborService,
        polling_interval: int = 5,
        on_new_orders: callable = None,
        on_orders_resolved: callable = None
    ):
        self.nonbor = nonbor_service
        self.polling_interval = polling_interval
        self.on_new_orders = on_new_orders
        self.on_orders_resolved = on_orders_resolved

        self._running = False
        self._last_count = 0
        self._last_ids: Set[int] = set()  # Oldingi polling dagi ID lar
        self._task: Optional[asyncio.Task] = None

        logger.info(f"Nonbor Poller yaratildi: {polling_interval}s interval")

    async def start(self):
        """Polling boshlash"""
        if self._running:
            return

        self._running = True

        # Birinchi ishga tushganda bizneslarni cache qilish
        await self.nonbor.get_businesses()

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Nonbor Polling boshlandi")

    async def stop(self):
        """Polling to'xtatish"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Nonbor Polling to'xtatildi")

    async def _poll_loop(self):
        """Asosiy polling sikli - exponential backoff bilan"""
        self._poll_count = 0
        while self._running:
            try:
                self._poll_count += 1
                count, current_ids = await self.nonbor.check_for_new_leads()
                # Har 12 poll da (60s) holat log
                if self._poll_count % 12 == 1:
                    logger.info(f"Polling #{self._poll_count}: buyurtmalar={count}, ids={current_ids}")

                if count is None:
                    # Xatolik — backoff hisoblash (5s, 10s, 20s, 40s, 60s max)
                    errors = self.nonbor._consecutive_errors
                    backoff = min(self.polling_interval * (2 ** min(errors - 1, 4)), 60)

                    # Bo'lakli kutish: har 5s da API ni tekshirib turadi
                    waited = 0
                    while waited < backoff and self._running:
                        await asyncio.sleep(self.polling_interval)
                        waited += self.polling_interval

                        # Har bo'lakda API ni tekshirish
                        test_count, test_ids = await self.nonbor.check_for_new_leads()
                        if test_count is not None:
                            # API tiklandi — darhol normal rejimga qaytish
                            count, current_ids = test_count, test_ids
                            logger.info(f"API tiklandi! Normal rejimga qaytildi ({waited}s da)")
                            break
                    else:
                        # Backoff tugadi, API hali ham ishlamayapti
                        continue

                current_id_set = set(current_ids) if current_ids else set()

                # Faqat ID lar o'zgarganda callback chaqirish
                if current_id_set != self._last_ids:
                    new_ids = current_id_set - self._last_ids
                    removed_ids = self._last_ids - current_id_set

                    # Yangi buyurtmalar keldi
                    if new_ids and self.on_new_orders:
                        await self.on_new_orders(count, list(current_id_set))

                    # Buyurtmalar hal qilindi (status o'zgardi)
                    if removed_ids and self.on_orders_resolved:
                        await self.on_orders_resolved(list(removed_ids), count)

                    self._last_ids = current_id_set

                self._last_count = count

                await asyncio.sleep(self.polling_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling xatosi: {e}")
                await asyncio.sleep(self.polling_interval)

    @property
    def current_count(self) -> int:
        """Hozirgi buyurtmalar soni"""
        return self._last_count
