"""
Telegram Bot Servisi
Xabar yuborish va boshqarish
"""

import logging
import json
import os
import random
import uuid
import aiohttp
import asyncio
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class TelegramChatError(Exception):
    """Doimiy Telegram chat xatosi (chat topilmadi, bot chiqarib yuborilgan, bloklangan)"""
    pass


# Callback data prefixes
CALLBACK_STATS = "stats"
CALLBACK_CALLS_1 = "calls_1"
CALLBACK_CALLS_2 = "calls_2"
CALLBACK_CALLS_3 = "calls_3"
CALLBACK_ANSWERED = "answered"
CALLBACK_UNANSWERED = "unanswered"
CALLBACK_ACCEPTED = "accepted"
CALLBACK_REJECTED = "rejected"
CALLBACK_NO_TELEGRAM = "no_telegram"
CALLBACK_BACK = "back"
CALLBACK_CALLS_BACK = "calls_back"  # Qo'ng'iroqlar sub-sahifalaridan orqaga

# Menu tugmalari
CALLBACK_MENU_BUSINESSES = "menu_businesses"
CALLBACK_MENU_CALLS = "menu_calls"
CALLBACK_MENU_ORDERS = "menu_orders"
CALLBACK_MENU_BACK = "menu_back"
CALLBACK_BIZ_REFRESH = "biz_refresh"

# Bizneslar pagination va region tugmalari
CALLBACK_BIZ_PAGE = "biz_page_"       # biz_page_0, biz_page_1, ...
CALLBACK_BIZ_REGION = "biz_region_"   # biz_region_0, biz_region_1, ...
CALLBACK_BIZ_BACK = "biz_back"        # Regiondan orqaga
CALLBACK_BIZ_REG_PAGE = "biz_rp_"     # biz_rp_0_1 (region_idx_page)
CALLBACK_BIZ_DISTRICT = "biz_dist_"   # biz_dist_0_1 (region_idx_district_idx)
CALLBACK_BIZ_DIST_BACK = "biz_dback_" # biz_dback_0 (region_idx ga qaytish)
CALLBACK_BIZ_ITEM = "biz_item_"       # biz_item_5 (business id)
CALLBACK_BIZ_ADD_GROUP = "biz_grp_"   # biz_grp_5 (business id - guruh qo'shish)
CALLBACK_BIZ_TOGGLE_CALL = "biz_call_"  # biz_call_5 (avtoqo'ng'iroqni yoqish/o'chirish)

# Davr tugmalari
CALLBACK_DAILY = "period_daily"
CALLBACK_WEEKLY = "period_weekly"
CALLBACK_MONTHLY = "period_monthly"
CALLBACK_YEARLY = "period_yearly"

# Owner orders (biznes egasi buyurtmalari)
CALLBACK_OWNER_ORDERS = "owner_orders"
CALLBACK_OWNER_BACK = "owner_back"
CALLBACK_OWNER_PERIOD = "oo_period_"    # oo_period_daily, oo_period_weekly, etc.
CALLBACK_OWNER_PAGE = "oo_page_"        # oo_page_0, oo_page_1, ...
CALLBACK_OWNER_STATUS = "oo_status_"    # oo_status_all, oo_status_accepted, oo_status_rejected
CALLBACK_OWNER_GROUP = "owner_group"    # Guruh boshqaruvi
CALLBACK_OWNER_GROUP_DEL = "owner_grp_del"  # Guruhni o'chirish (tasdiqlash)
CALLBACK_OWNER_GROUP_DEL_CONFIRM = "owner_grp_del_yes"  # Guruhni o'chirish tasdiqlandi
CALLBACK_OWNER_MAIN_PERIOD = "om_period_"  # om_period_daily, om_period_weekly, etc.
CALLBACK_OWNER_PLANNED = "owner_planned"       # Biznes egasi reja buyurtmalari
CALLBACK_OWNER_PLANNED_PAGE = "opl_page_"      # opl_page_0 (sahifalash)

# Admin orders (admin buyurtmalar bo'limi)
CALLBACK_ADMIN_ORDERS_PAGE = "ao_page_"      # ao_page_0, ao_page_1, ...
CALLBACK_ADMIN_ORDERS_STATUS = "ao_status_"  # ao_status_all, ao_status_accepted, ao_status_rejected, ao_status_notg
CALLBACK_ADMIN_ORDERS_PERIOD = "ao_period_"  # ao_period_daily, ao_period_weekly, etc.

# Rejalashtirilgan buyurtmalar
CALLBACK_MENU_PLANNED = "menu_planned"        # Reja buyurtmalar bo'limi
CALLBACK_PLANNED_PAGE = "pl_page_"            # pl_page_0, pl_page_1, ...

# Xabarnomalar (scheduled notifications)
CALLBACK_MENU_NOTIF = "menu_notif"           # Asosiy menyudan xabarnomalar
CALLBACK_NOTIF_NEW = "notif_new"             # Yangi xabarnoma yaratish
CALLBACK_NOTIF_LIST = "notif_list"           # Ro'yxatni ko'rish
CALLBACK_NOTIF_TARGET_REGION = "nt_region"   # Hudud bo'yicha target
CALLBACK_NOTIF_TARGET_DISTRICT = "nt_dist"   # Tuman bo'yicha target
CALLBACK_NOTIF_TARGET_BIZ = "nt_biz"         # Bizneslar bo'yicha target
CALLBACK_NOTIF_SEL_REGION = "nsr_"           # nsr_0 (region tanlash)
CALLBACK_NOTIF_SEL_DISTRICT = "nsd_"         # nsd_0_1 (region_idx_district_idx)
CALLBACK_NOTIF_SEL_BIZ = "nsb_"              # nsb_5 (business id tanlash)
CALLBACK_NOTIF_SEL_BIZ_PAGE = "nsbp_"        # nsbp_0 (biznes sahifa)
CALLBACK_NOTIF_DONE_BIZ = "nsb_done"         # Bizneslar tanlash tugadi
CALLBACK_NOTIF_CONFIRM = "notif_confirm"     # Tasdiqlash
CALLBACK_NOTIF_CANCEL = "notif_cancel"       # Bekor qilish
CALLBACK_NOTIF_BACK = "notif_back"           # Orqaga
CALLBACK_NOTIF_DELETE = "notif_del_"         # notif_del_uuid (o'chirish - tasdiqlash)
CALLBACK_NOTIF_DELETE_CONFIRM = "ndc_"       # ndc_uuid (o'chirish tasdiqlandi)
CALLBACK_NOTIF_PAGE = "notif_pg_"            # notif_pg_0 (ro'yxat sahifa)
CALLBACK_NOTIF_CAL_MONTH = "ncm_"           # ncm_2026_2 (yil_oy - oy o'zgartirish)
CALLBACK_NOTIF_CAL_DAY = "ncd_"             # ncd_2026_2_15 (yil_oy_kun tanlash)
CALLBACK_NOTIF_CAL_HOUR = "nch_"            # nch_7 (soat tanlash)
CALLBACK_NOTIF_CAL_MIN = "ncmin_"           # ncmin_0 (daqiqa tanlash)

# Auth states
AUTH_IDLE = "idle"
AUTH_AWAITING_PHONE = "awaiting_phone"
AUTH_AWAITING_OTP = "awaiting_otp"
AUTH_VERIFIED = "verified"

# Admin telefon raqamlari - barcha funksiyalarga to'liq kirish
ADMIN_PHONES = {
    "+998773088888",
    "+998948679300",
}


class TelegramService:
    """
    Telegram Bot servisi

    Funksiyalar:
    - Xabar yuborish
    - Xabarni tahrirlash
    - Xabarni o'chirish
    - Inline buttonlar
    """

    def __init__(self, bot_token: str, default_chat_id: str = None):
        self.bot_token = bot_token
        self.default_chat_id = default_chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

        self._session: Optional[aiohttp.ClientSession] = None

        logger.info("Telegram servisi ishga tushdi")

    async def _get_session(self) -> aiohttp.ClientSession:
        """HTTP session olish"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Sessionni yopish"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_chat_info(self, chat_id: str) -> Optional[Dict]:
        """Chat ma'lumotlarini olish (nomi, turi)"""
        return await self._make_request("getChat", {"chat_id": chat_id})

    async def _make_request(
        self,
        method: str,
        data: dict = None
    ) -> Optional[Dict]:
        """Telegram API so'rov"""
        session = await self._get_session()
        url = f"{self.base_url}/{method}"

        try:
            async with session.post(url, json=data) as response:
                result = await response.json()

                if result.get("ok"):
                    return result.get("result")
                else:
                    desc = result.get("description", "")
                    error_code = result.get("error_code", 0)
                    logger.error(f"Telegram xatosi: {desc}")
                    # Doimiy xatolar - qayta urinish befoyda
                    permanent_keywords = (
                        "chat not found",
                        "bot was blocked by the user",
                        "bot is not a member",
                        "user is deactivated",
                        "group chat was upgraded",
                        "supergroup chat was upgraded",
                        "have no rights to send",
                        "need administrator rights",
                    )
                    if error_code == 403 or any(kw in desc.lower() for kw in permanent_keywords):
                        raise TelegramChatError(desc)
                    return None

        except aiohttp.ClientError as e:
            logger.error(f"Telegram ulanish xatosi: {e}")
            return None

    async def send_message(
        self,
        text: str,
        chat_id: str = None,
        parse_mode: str = "Markdown",
        reply_markup: dict = None,
        disable_notification: bool = False
    ) -> Optional[int]:
        """
        Xabar yuborish

        Args:
            text: Xabar matni
            chat_id: Chat ID (default ishlatiladi agar berilmasa)
            parse_mode: Markdown yoki HTML
            reply_markup: Inline keyboard
            disable_notification: Ovossiz yuborish

        Returns:
            Message ID yoki None
        """
        chat_id = chat_id or self.default_chat_id

        if not chat_id:
            logger.error("Chat ID ko'rsatilmagan")
            return None

        data = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": disable_notification
        }

        if parse_mode:
            data["parse_mode"] = parse_mode

        if reply_markup:
            data["reply_markup"] = reply_markup

        result = await self._make_request("sendMessage", data)

        if result:
            message_id = result.get("message_id")
            logger.info(f"Telegram xabar yuborildi: {message_id}")
            return message_id

        return None

    async def edit_message(
        self,
        message_id: int,
        text: str,
        chat_id: str = None,
        parse_mode: str = "Markdown",
        reply_markup: dict = None
    ) -> bool:
        """
        Xabarni tahrirlash

        Args:
            message_id: Xabar ID
            text: Yangi matn
            chat_id: Chat ID

        Returns:
            Muvaffaqiyat holati
        """
        chat_id = chat_id or self.default_chat_id

        data = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }

        if reply_markup:
            data["reply_markup"] = reply_markup

        result = await self._make_request("editMessageText", data)

        if result:
            logger.info(f"Telegram xabar tahrirlandi: {message_id}")
            return True

        return False

    async def delete_message(
        self,
        message_id: int,
        chat_id: str = None
    ) -> bool:
        """
        Xabarni o'chirish

        Args:
            message_id: Xabar ID
            chat_id: Chat ID

        Returns:
            Muvaffaqiyat holati
        """
        chat_id = chat_id or self.default_chat_id

        data = {
            "chat_id": chat_id,
            "message_id": message_id
        }

        # To'g'ridan-to'g'ri API chaqirish
        session = await self._get_session()
        url = f"{self.base_url}/deleteMessage"

        try:
            async with session.post(url, json=data) as response:
                result = await response.json()
                logger.debug(f"Delete response: {result}")

                if result.get("ok"):
                    logger.info(f"Telegram xabar o'chirildi: {message_id}")
                    return True
                else:
                    error_desc = result.get('description', '')
                    # Agar xabar topilmasa - bu xato emas, oddiy debug log
                    if "message to delete not found" in error_desc.lower():
                        logger.debug(f"Telegram xabar {message_id} allaqachon o'chirilgan")
                        return True  # Bu success deb hisoblaymiz
                    else:
                        logger.error(f"Telegram o'chirish xatosi: {error_desc}")
                        return False

        except Exception as e:
            logger.error(f"Telegram delete xatosi: {e}")
            return False

    async def send_seller_orders_alert(
        self,
        seller_orders: dict,
        call_attempts: int = 0,
        chat_id: str = None,
        call_note: str = ""
    ) -> Optional[int]:
        """
        Sotuvchi uchun buyurtmalar haqida ogohlantirish yuborish

        Args:
            seller_orders: {
                "seller_name": str,
                "seller_phone": str,
                "seller_address": str,
                "orders": list of order dicts
            }
            call_attempts: Qo'ng'iroq urinishlari
            chat_id: Chat ID
            call_note: Qo'ng'iroq holati izohi

        Returns:
            Message ID
        """
        text = self._format_seller_orders_alert(seller_orders, call_attempts, call_note)

        return await self.send_message(
            text=text,
            chat_id=chat_id,
            parse_mode="HTML"  # HTML link ishlashi uchun
        )

    async def update_seller_orders_alert(
        self,
        message_id: int,
        seller_orders: dict,
        call_attempts: int = 0,
        chat_id: str = None,
        call_note: str = ""
    ) -> bool:
        """Mavjud sotuvchi xabarini tahrirlash (yangilash)"""
        text = self._format_seller_orders_alert(seller_orders, call_attempts, call_note)
        chat_id = chat_id or self.default_chat_id

        return await self.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML"
        )

    async def send_all_sellers_alert(
        self,
        sellers_data: dict,
        call_attempts: int = 0,
        chat_id: str = None
    ) -> tuple[Optional[int], dict]:
        """
        HAR BIR sotuvchi uchun ALOHIDA xabar yuborish

        Args:
            sellers_data: Dictionary of sellers {seller_phone: {seller_name, seller_phone, seller_address, orders}}
            call_attempts: Qo'ng'iroq urinishlari
            chat_id: Chat ID

        Returns:
            Tuple of (first_message_id, seller_message_ids dict)
        """
        first_message_id = None
        seller_message_ids = {}  # {seller_phone: message_id}

        # HAR BIR SOTUVCHI UCHUN ALOHIDA XABAR
        for seller_phone, seller_data in sellers_data.items():
            message_id = await self.send_seller_orders_alert(
                seller_data,
                call_attempts,
                chat_id
            )
            if message_id:
                seller_message_ids[seller_phone] = message_id
                if first_message_id is None:
                    first_message_id = message_id

            logger.info(f"Sotuvchi {seller_phone} uchun alohida xabar yuborildi: {message_id}")

        return first_message_id, seller_message_ids

    def _format_seller_orders_alert(self, seller_orders: dict, call_attempts: int = 0, call_note: str = "") -> str:
        """Sotuvchi buyurtmalari xabarini formatlash - HAR BIR SOTUVCHI UCHUN ALOHIDA"""
        seller_name = seller_orders.get("seller_name", "Noma'lum")
        seller_phone = seller_orders.get("seller_phone", "Noma'lum")
        # Sotuvchi telefon raqamiga + qo'shish
        if seller_phone and seller_phone != "Noma'lum" and not str(seller_phone).startswith('+'):
            seller_phone = '+' + str(seller_phone)
        seller_address = seller_orders.get("seller_address", "Noma'lum")
        orders = seller_orders.get("orders", [])
        orders_count = len(orders)

        # Umumiy narx
        total_price = sum(o.get("price", 0) or 0 for o in orders)
        total_price_str = f"{total_price:,.0f}".replace(",", " ") + " so'm"

        # Header
        text = f"""🚨 DIQQAT! {orders_count} ta buyurtma qabul qilinmadi!

SOTUVCHI:
  Nomi: {seller_name}
  Tel: {seller_phone}
  Manzil: {seller_address}

━━━ BUYURTMALAR ━━━
"""
        # Har bir buyurtma (mijoz)
        for i, order in enumerate(orders, 1):
            order_number = order.get("order_number", order.get("lead_id", "N/A"))
            client_name = order.get("client_name", "Noma'lum")
            client_phone = order.get("client_phone", "Noma'lum")
            # Mijoz telefon raqamiga + qo'shish
            if client_phone and client_phone != "Noma'lum" and not str(client_phone).startswith('+'):
                client_phone = '+' + str(client_phone)
            product_name = order.get("product_name", "Noma'lum")
            quantity = order.get("quantity", 1)
            price = order.get("price", 0)

            # Narxni formatlash
            if isinstance(price, (int, float)) and price:
                price_str = f"{price:,.0f}".replace(",", " ") + " so'm"
            else:
                price_str = "Noma'lum"

            # Tayyorlab berish vaqti (planned buyurtmalar uchun)
            delivery_time = order.get("delivery_time", "")

            text += f"""
{i}. Buyurtma #{order_number}
   Mijoz: {client_name}
   Tel: {client_phone}
   Mahsulot: {product_name}
   Miqdor: {quantity} ta
   Narx: {price_str}"""
            if delivery_time:
                text += f"\n   🕐 Tayyor bo'lish vaqti: {delivery_time}"
            text += "\n"

        # Footer
        if not call_note:
            if call_attempts == 0:
                call_note = "📵 Telefon raqami topilmadi"
            else:
                call_note = f"📞 {call_attempts} marta qo'ng'iroq qilindi, javob yo'q"

        text += f"""
━━━━━━━━━━━━━━━━━━━━━
📦 Jami: {orders_count} ta buyurtma
💰 Umumiy: {total_price_str}

❌ Buyurtmalarni qabul qilmayapti!
{call_note}
🔴 Zudlik bilan bog'laning!

📱 <a href="https://welltech.amocrm.ru">Buyurtmalarni ko'rish</a>"""

        return text

    STATUS_LABELS = {
        "CHECKING": "🟡 Tekshirilmoqda",
        "ACCEPTED": "🟢 Qabul qilindi",
        "READY": "✅ Tayyor",
        "PAYMENT_EXPIRED": "💳 To'lov muddati o'tdi",
        "ACCEPT_EXPIRED": "⏰ Qabul muddati o'tdi",
        "CANCELLED_CLIENT": "❌ Mijoz bekor qildi",
        "CANCELLED_SELLER": "🚫 Sotuvchi bekor qildi",
        "DELIVERING": "🚗 Yetkazilmoqda",
        "DELIVERED": "📦 Yetkazildi",
        "COMPLETED": "🏁 Yakunlandi",
        "CANCELLED": "❌ Bekor qilindi",
    }

    def _format_business_order_message(self, order_data: dict) -> str:
        """Biznes guruhi uchun bitta buyurtma xabari"""
        order_number = order_data.get("order_number", "")
        status = order_data.get("status", "CHECKING").upper()
        client_name = order_data.get("client_name", "Noma'lum")
        client_phone = order_data.get("client_phone", "")
        if client_phone and not str(client_phone).startswith('+'):
            client_phone = '+' + str(client_phone)
        product_name = order_data.get("product_name", "")
        quantity = order_data.get("quantity", 1)
        price = order_data.get("price", 0)
        price_str = f"{price:,.0f}".replace(",", " ") + " so'm" if price else "—"
        is_planned = order_data.get("is_planned", False)

        # Yetkazib berish ma'lumotlari
        delivery_address = order_data.get("delivery_address", "")
        delivery_lat = order_data.get("delivery_lat", "")
        delivery_lon = order_data.get("delivery_lon", "")
        delivery_time = order_data.get("delivery_time", "")

        # Yetkazish va to'lov usuli
        delivery_method = order_data.get("delivery_method", "")
        payment_method = order_data.get("payment_method", "")

        DELIVERY_LABELS = {"DELIVERY": "Yetkazib berish", "PICKUP": "Olib ketish"}
        PAYMENT_LABELS = {"CASH": "Naqd", "CARD": "Karta", "ONLINE": "Online"}

        # Status label
        status_label = self.STATUS_LABELS.get(status, f"📋 {status}")

        # Reja buyurtma header
        if is_planned:
            text = f"📅 Reja buyurtma #{order_number}\n"
        else:
            text = f"📦 Buyurtma #{order_number}\n"
        text += f"━━━━━━━━━━━━━━━━━━━\n"
        text += f"📊 Status: {status_label}\n"

        # Yetkazish va to'lov usuli
        if delivery_method:
            dm_label = DELIVERY_LABELS.get(delivery_method.upper(), delivery_method)
            text += f"🚚 Yetkazish: {dm_label}\n"
        if payment_method:
            pm_label = PAYMENT_LABELS.get(payment_method.upper(), payment_method)
            text += f"💳 To'lov: {pm_label}\n"

        # Reja vaqti
        if delivery_time:
            if is_planned:
                text += f"📅 Reja vaqti: {delivery_time} ❗❗❗\n"
            else:
                text += f"🕐 Tayyorlab berish vaqti: {delivery_time}\n"

        # Mijoz ma'lumotlari - faqat READY, DELIVERING, DELIVERED statuslarida ko'rsatiladi
        # COMPLETED (yakunlandi) da avtomatik yashiriladi
        show_client_statuses = {"READY", "DELIVERING", "DELIVERED"}
        if status in show_client_statuses:
            if client_name and client_name != "Noma'lum":
                text += f"👤 Mijoz: {client_name}\n"
            if client_phone:
                text += f"📞 Tel: {client_phone}\n"
            if delivery_address:
                text += f"📍 Manzil: {delivery_address}\n"
            if delivery_lat and delivery_lon:
                text += f"🗺 Lokatsiya: https://maps.google.com/?q={delivery_lat},{delivery_lon}\n"

        if product_name:
            text += f"🏷 Mahsulot: {product_name}\n"
            text += f"📦 Miqdor: {quantity} ta\n"
        text += f"💰 Narx: {price_str}"

        return text

    async def send_business_order_message(self, order_data: dict, chat_id: str) -> Optional[int]:
        """Biznes guruhiga bitta buyurtma xabari yuborish"""
        text = self._format_business_order_message(order_data)
        return await self.send_message(text=text, chat_id=chat_id)

    async def update_business_order_message(self, message_id: int, order_data: dict, chat_id: str) -> bool:
        """Biznes guruhidagi buyurtma xabarini yangilash (status o'zgarganda)"""
        text = self._format_business_order_message(order_data)
        return await self.edit_message(message_id=message_id, text=text, chat_id=chat_id)

    def _format_all_sellers_alert(self, sellers_data: dict, call_attempts: int = 0) -> str:
        """
        BARCHA sotuvchilar buyurtmalarini BITTA xabarda formatlash

        Args:
            sellers_data: Dictionary of sellers {seller_phone: {seller_name, seller_phone, seller_address, orders}}
            call_attempts: Qo'ng'iroq urinishlari soni

        Returns:
            Formatted text message
        """
        # Jami buyurtmalar va umumiy narxni hisoblash
        total_orders_count = 0
        total_price_all = 0

        for seller_data in sellers_data.values():
            orders = seller_data.get("orders", [])
            total_orders_count += len(orders)
            total_price_all += sum(o.get("price", 0) or 0 for o in orders)

        total_price_str = f"{total_price_all:,.0f}".replace(",", " ") + " so'm"

        # Header
        text = f"""🚨 DIQQAT! {total_orders_count} ta buyurtma qabul qilinmadi!
{len(sellers_data)} ta sotuvchida buyurtmalar kutmoqda.

"""

        # Har bir sotuvchi uchun alohida bo'lim
        for seller_idx, (seller_phone, seller_data) in enumerate(sellers_data.items(), 1):
            seller_name = seller_data.get("seller_name", "Noma'lum")
            seller_phone_display = seller_data.get("seller_phone", "Noma'lum")
            seller_address = seller_data.get("seller_address", "Noma'lum")
            orders = seller_data.get("orders", [])
            orders_count = len(orders)

            # Sotuvchi narxi
            seller_total_price = sum(o.get("price", 0) or 0 for o in orders)
            seller_price_str = f"{seller_total_price:,.0f}".replace(",", " ") + " so'm"

            # Sotuvchi header
            text += f"""━━━━━━━━━━━━━━━━━━━━
📍 SOTUVCHI #{seller_idx}
  Nomi: {seller_name}
  Tel: {seller_phone_display}
  Manzil: {seller_address}
  Buyurtmalar: {orders_count} ta

"""

            # Har bir buyurtma
            for i, order in enumerate(orders, 1):
                order_number = order.get("order_number", order.get("lead_id", "N/A"))
                client_name = order.get("client_name", "Noma'lum")
                client_phone = order.get("client_phone", "Noma'lum")
                product_name = order.get("product_name", "Noma'lum")
                quantity = order.get("quantity", 1)
                price = order.get("price", 0)

                # Narxni formatlash
                if isinstance(price, (int, float)) and price:
                    price_str = f"{price:,.0f}".replace(",", " ") + " so'm"
                else:
                    price_str = "Noma'lum"

                text += f"""  {i}. Buyurtma #{order_number}
     Mijoz: {client_name}
     Tel: {client_phone}
     Mahsulot: {product_name}
     Miqdor: {quantity} ta
     Narx: {price_str}

"""

            # Sotuvchi footer
            text += f"""  💰 Sotuvchi jami: {seller_price_str}

"""

        # Umumiy footer
        text += f"""━━━━━━━━━━━━━━━━━━━━
📦 JAMI: {total_orders_count} ta buyurtma
💰 UMUMIY: {total_price_str}

❌ Buyurtmalarni qabul qilmayapti!
📞 {call_attempts} marta qo'ng'iroq qilindi.
🔴 Zudlik bilan bog'laning!

📱 Buyurtmalarni ko'rish (https://welltech.amocrm.ru/leads/pipeline/10154618)"""

        return text

    async def send_resolved_message(
        self,
        resolved_count: int,
        remaining_count: int,
        chat_id: str = None
    ) -> Optional[int]:
        """
        Buyurtmalar tekshirildi xabari

        Args:
            resolved_count: Tekshirilgan soni
            remaining_count: Qolgan soni
            chat_id: Chat ID

        Returns:
            Message ID
        """
        now = datetime.now().strftime("%H:%M:%S")

        if remaining_count == 0:
            text = f"""
✅ *Barcha buyurtmalar tekshirildi!*

📦 Tekshirilgan: *{resolved_count}* ta
⏰ Vaqt: {now}
            """
        else:
            text = f"""
✅ *Buyurtmalar tekshirildi*

📦 Tekshirilgan: *{resolved_count}* ta
📦 Qolgan: *{remaining_count}* ta
⏰ Vaqt: {now}
            """

        return await self.send_message(text=text, chat_id=chat_id)

    async def update_order_alert(
        self,
        message_id: int,
        orders_count: int,
        call_attempts: int = 0,
        chat_id: str = None
    ) -> bool:
        """
        Buyurtma ogohlantirishini yangilash

        Args:
            message_id: Xabar ID
            orders_count: Yangi buyurtmalar soni
            call_attempts: Qo'ng'iroq urinishlari
            chat_id: Chat ID

        Returns:
            Muvaffaqiyat holati
        """
        now = datetime.now().strftime("%H:%M:%S")

        text = f"""
🔔 *Yangi buyurtmalar!*

📦 Buyurtmalar soni: *{orders_count}* ta
📞 Qo'ng'iroq urinishlari: {call_attempts}/3
⏰ Yangilangan: {now}
📋 Status: TEKSHIRILMOQDA

⚠️ Iltimos, tezroq tekshiring!
        """

        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "📱 amoCRM ochish",
                        "url": "https://welltech.amocrm.ru"
                    }
                ]
            ]
        }

        return await self.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            reply_markup=reply_markup
        )


class TelegramNotificationManager:
    """
    Telegram bildirishnoma boshqaruvchisi

    - Xabarlarni kuzatish
    - Auto-delete qilish
    - Dublikatlarni oldini olish
    """

    def __init__(self, telegram_service: TelegramService, data_dir: str = "data"):
        self.telegram = telegram_service
        self._active_message_ids: list = []  # Barcha yuborilgan xabarlar
        self._seller_message_ids: dict = {}  # Sotuvchi telefon -> xabar ID mapping
        self._pending_deletions: list = []  # O'chirilmagan xabarlar (retry uchun)
        self._last_count = 0
        self._message_sent_at: Optional[datetime] = None

        # Xabar ID larini saqlash uchun fayl
        from pathlib import Path
        import json
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.messages_file = self.data_dir / "telegram_messages.json"
        self._load_messages()

    def _load_messages(self):
        """Saqlangan xabar ID larini yuklash"""
        if self.messages_file.exists():
            try:
                import json
                with open(self.messages_file, "r") as f:
                    data = json.load(f)
                    self._active_message_ids = data.get("message_ids", [])
                    self._seller_message_ids = data.get("seller_message_ids", {})
                    self._pending_deletions = data.get("pending_deletions", [])
                    # MUHIM: combined_message_id ni ham yuklash
                    self._combined_message_id = data.get("combined_message_id", None)

                    # Eski xabar ID larni tozalash - faqat seller_message_ids yoki combined_message_id da bor bo'lganlarni saqlash
                    valid_message_ids = set(self._seller_message_ids.values())
                    if self._combined_message_id:
                        valid_message_ids.add(self._combined_message_id)
                    old_count = len(self._active_message_ids)
                    self._active_message_ids = [msg_id for msg_id in self._active_message_ids if msg_id in valid_message_ids]

                    if old_count != len(self._active_message_ids):
                        logger.info(f"Eski xabar ID lar tozalandi: {old_count} -> {len(self._active_message_ids)}")
                        self._save_messages()  # Tozalangan listni saqlash

                    logger.info(f"Telegram xabar ID lar yuklandi: {len(self._active_message_ids)} ta, pending: {len(self._pending_deletions)} ta")
            except Exception as e:
                logger.error(f"Telegram xabar ID lar yuklashda xato: {e}")
                self._active_message_ids = []
                self._seller_message_ids = {}
                self._pending_deletions = []
                self._combined_message_id = None

    def _save_messages(self):
        """Xabar ID larni faylga saqlash"""
        try:
            import json
            # MUHIM: combined_message_id ni ham saqlash
            combined_id = getattr(self, '_combined_message_id', None)
            with open(self.messages_file, "w") as f:
                json.dump({
                    "message_ids": self._active_message_ids,
                    "seller_message_ids": self._seller_message_ids,
                    "combined_message_id": combined_id,
                    "pending_deletions": self._pending_deletions
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Telegram xabar ID lar saqlashda xato: {e}")

    async def notify_seller_orders(
        self,
        seller_orders: dict,
        call_attempts: int = 0,
        chat_id: str = None
    ):
        """
        Sotuvchi buyurtmalari haqida xabar

        Args:
            seller_orders: Sotuvchi va uning buyurtmalari
            call_attempts: Qo'ng'iroq urinishlari
            chat_id: Biznes guruh chat ID (None bo'lsa default)
        """
        # Yangi xabar yuborish - biznes guruhiga
        message_id = await self.telegram.send_seller_orders_alert(seller_orders, call_attempts, chat_id=chat_id)
        if message_id:
            self._active_message_ids.append(message_id)
            self._message_sent_at = datetime.now()
            self._save_messages()  # Faylga saqlash

    async def notify_resolved(self, resolved_count: int, remaining_count: int):
        """
        Buyurtmalar tekshirildi xabari

        Agar hammasi tekshirilgan bo'lsa - barcha aktiv xabarlarni o'chiradi
        Agar o'chirish muvaffaqiyatsiz bo'lsa - pending_deletions ga qo'shadi
        """
        if remaining_count == 0 and self._active_message_ids:
            # Barcha aktiv xabarlarni o'chirish
            failed_ids = []
            for msg_id in self._active_message_ids:
                success = await self.telegram.delete_message(msg_id)
                if not success:
                    failed_ids.append(msg_id)
                    logger.warning(f"Xabar o'chirilmadi, pending ga qo'shildi: {msg_id}")

            # Muvaffaqiyatsiz o'chirishlarni pending ga qo'shish
            if failed_ids:
                for msg_id in failed_ids:
                    if msg_id not in self._pending_deletions:
                        self._pending_deletions.append(msg_id)
                logger.info(f"Pending deletions: {len(self._pending_deletions)} ta xabar")

            self._active_message_ids = []
            self._seller_message_ids = {}
            self._combined_message_id = None
            self._message_sent_at = None
            self._save_messages()

        self._last_count = remaining_count

    async def delete_all_notifications(self):
        """Barcha aktiv xabarlarni o'chirish"""
        failed_ids = []
        for msg_id in self._active_message_ids:
            success = await self.telegram.delete_message(msg_id)
            if not success:
                failed_ids.append(msg_id)

        if failed_ids:
            for msg_id in failed_ids:
                if msg_id not in self._pending_deletions:
                    self._pending_deletions.append(msg_id)
            logger.warning(f"O'chirilmagan xabarlar pending ga qo'shildi: {len(failed_ids)} ta")

        self._active_message_ids = []
        self._seller_message_ids = {}
        self._combined_message_id = None
        self._message_sent_at = None
        self._save_messages()

    async def retry_pending_deletions(self):
        """Pending deletions ro'yxatidagi xabarlarni qayta o'chirishga urinish"""
        if not self._pending_deletions:
            return

        logger.info(f"Pending deletions retry: {len(self._pending_deletions)} ta xabar")
        still_pending = []
        for msg_id in self._pending_deletions:
            success = await self.telegram.delete_message(msg_id)
            if not success:
                still_pending.append(msg_id)
            else:
                logger.info(f"Pending xabar muvaffaqiyatli o'chirildi: {msg_id}")

        self._pending_deletions = still_pending
        self._save_messages()

        if still_pending:
            logger.warning(f"Hali o'chirilmagan xabarlar: {len(still_pending)} ta")
        else:
            logger.info("Barcha pending xabarlar muvaffaqiyatli o'chirildi")

    def clear_notification(self):
        """Bildirishnoma holatini tozalash"""
        self._active_message_ids = []
        self._seller_message_ids = {}
        self._combined_message_id = None  # MUHIM: combined_message_id ni ham tozalash
        self._last_count = 0
        self._message_sent_at = None
        self._save_messages()  # Faylga saqlash

    @property
    def has_active_notification(self) -> bool:
        """Aktiv bildirishnoma bormi"""
        return len(self._active_message_ids) > 0


class TelegramStatsHandler:
    """
    Telegram statistika handleri

    Inline keyboard orqali statistika ko'rsatish va boshqarish
    """

    def __init__(self, telegram_service: TelegramService, stats_service=None, nonbor_service=None):
        self.telegram = telegram_service
        self.stats_service = stats_service
        self.nonbor_service = nonbor_service
        self._polling_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_update_id = 0
        self._current_period = "daily"  # Joriy davr

        # Biznes guruh qo'shish uchun state
        self._awaiting_group_input: Dict[str, int] = {}  # chat_id -> business_id
        self._awaiting_message_id: Dict[str, int] = {}   # chat_id -> message_id

        # Guruhlar faylini yuklash
        self._groups_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "business_groups.json"
        )
        self._business_groups: Dict[str, str] = self._load_groups()

        # Avtoqo'ng'iroq sozlamalari
        self._call_settings_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "call_settings.json"
        )
        self._disabled_businesses: set = self._load_call_settings()

        # Auth tizimi
        self._auth_states: Dict[str, str] = {}          # chat_id -> state
        self._auth_phones: Dict[str, str] = {}          # chat_id -> phone (vaqtinchalik)
        self._auth_otps: Dict[str, dict] = {}           # chat_id -> {code, expires, phone, business_id}
        self._verified_users: Dict[str, dict] = {}      # chat_id -> {phone, business_id, business_title}
        self._phone_to_chat: Dict[str, str] = {}        # phone -> chat_id
        self._auth_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "auth_users.json"
        )
        self._load_auth_users()

        # Owner orders view state (har bir chat uchun alohida)
        self._owner_orders_period: Dict[str, str] = {}    # chat_id -> period (daily/weekly/monthly/yearly)
        self._owner_orders_page: Dict[str, int] = {}      # chat_id -> page (0-indexed)
        self._owner_orders_status: Dict[str, str] = {}    # chat_id -> status filter (all/accepted/rejected)

        # Admin orders view state
        self._admin_orders_page: Dict[str, int] = {}      # chat_id -> page (0-indexed)
        self._admin_orders_status: Dict[str, str] = {}    # chat_id -> status filter (all/accepted/rejected/notg)

        # Xabarnomalar tizimi
        self._notif_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "scheduled_notifications.json"
        )
        self._notifications: List[dict] = self._load_notifications()
        # Xabarnoma yaratish state (har bir admin chat uchun)
        self._notif_draft_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "notif_drafts.json"
        )
        self._notif_draft: Dict[str, dict] = self._load_notif_drafts()
        self._awaiting_notif_text: Dict[str, int] = {}    # chat_id -> message_id
        self._awaiting_notif_datetime: Dict[str, int] = {}  # chat_id -> message_id

    def set_stats_service(self, stats_service):
        """Stats servisini sozlash"""
        self.stats_service = stats_service

    def set_nonbor_service(self, nonbor_service):
        """Nonbor servisini sozlash"""
        self.nonbor_service = nonbor_service

    def _load_groups(self) -> Dict[str, str]:
        """Guruhlar faylidan yuklash"""
        try:
            if os.path.exists(self._groups_file):
                with open(self._groups_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Guruhlar faylini yuklash xatosi: {e}")
        return {}

    def _save_groups(self):
        """Guruhlarni faylga saqlash"""
        try:
            os.makedirs(os.path.dirname(self._groups_file), exist_ok=True)
            with open(self._groups_file, "w") as f:
                json.dump(self._business_groups, f, indent=2)
        except Exception as e:
            logger.error(f"Guruhlar faylini saqlash xatosi: {e}")

    # ===== AVTOQO'NG'IROQ SOZLAMALARI =====

    def _load_call_settings_raw(self) -> dict:
        """call_settings.json ni to'liq o'qish"""
        try:
            if os.path.exists(self._call_settings_file):
                with open(self._call_settings_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Call settings yuklash xatosi: {e}")
        return {}

    def _load_call_settings(self) -> set:
        """O'chirilgan bizneslar ro'yxatini yuklash"""
        data = self._load_call_settings_raw()
        return set(data.get("disabled_businesses", []))

    def _save_call_settings(self):
        """O'chirilgan bizneslar ro'yxatini saqlash"""
        try:
            data = self._load_call_settings_raw()
            data["disabled_businesses"] = list(self._disabled_businesses)
            os.makedirs(os.path.dirname(self._call_settings_file), exist_ok=True)
            with open(self._call_settings_file, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Call settings saqlash xatosi: {e}")

    def is_call_enabled(self, business_id: int) -> bool:
        """Biznes uchun avtoqo'ng'iroq yoqilganmi?"""
        return business_id not in self._disabled_businesses

    def get_business_config(self, business_id: int) -> dict:
        """Biznes uchun individual sozlamalar (har safar fayldan o'qiydi)"""
        data = self._load_call_settings_raw()
        configs = data.get("business_configs", {})
        return configs.get(str(business_id), {})

    # ===== XABARNOMALAR =====

    def _load_notifications(self) -> list:
        """Rejalashtirilgan xabarnomalarni yuklash"""
        try:
            if os.path.exists(self._notif_file):
                with open(self._notif_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("notifications", [])
        except Exception as e:
            logger.error(f"Xabarnomalar yuklash xatosi: {e}")
        return []

    def _save_notifications(self):
        """Xabarnomalarni saqlash"""
        try:
            os.makedirs(os.path.dirname(self._notif_file), exist_ok=True)
            with open(self._notif_file, "w", encoding="utf-8") as f:
                json.dump({"notifications": self._notifications}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Xabarnomalar saqlash xatosi: {e}")

    def _load_notif_drafts(self) -> dict:
        """Xabarnoma draftlarini fayldan yuklash (restart uchun)"""
        try:
            if os.path.exists(self._notif_draft_file):
                with open(self._notif_draft_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"Xabarnoma draftlari yuklandi: {len(data)} ta")
                    return data
        except Exception as e:
            logger.error(f"Xabarnoma draftlari yuklash xatosi: {e}")
        return {}

    def _save_notif_drafts(self):
        """Xabarnoma draftlarini faylga saqlash"""
        try:
            os.makedirs(os.path.dirname(self._notif_draft_file), exist_ok=True)
            with open(self._notif_draft_file, "w", encoding="utf-8") as f:
                json.dump(self._notif_draft, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Xabarnoma draftlari saqlash xatosi: {e}")

    def get_pending_notifications(self) -> list:
        """Yuborilishi kerak bo'lgan xabarnomalarni olish (vaqti kelgan)"""
        now = datetime.now(timezone(timedelta(hours=5)))
        pending = []
        for notif in self._notifications:
            if notif.get("status") != "pending":
                continue
            try:
                send_at = datetime.fromisoformat(notif["send_at"])
                if send_at <= now:
                    pending.append(notif)
            except (KeyError, ValueError):
                continue
        return pending

    def mark_notification_sent(self, notif_id: str, sent_count: int, total_count: int):
        """Xabarnomani yuborilgan deb belgilash"""
        for notif in self._notifications:
            if notif.get("id") == notif_id:
                notif["status"] = "sent"
                notif["sent_count"] = sent_count
                notif["total_count"] = total_count
                notif["sent_at"] = datetime.now(timezone(timedelta(hours=5))).isoformat()
                break
        self._save_notifications()

    # ===== AUTH METODLAR =====

    def _load_auth_users(self):
        """Auth faylidan yuklash"""
        try:
            if os.path.exists(self._auth_file):
                with open(self._auth_file, "r") as f:
                    data = json.load(f)
                    self._verified_users = data.get("verified_users", {})
                    self._phone_to_chat = data.get("phone_to_chat", {})
                    for chat_id in self._verified_users:
                        self._auth_states[chat_id] = AUTH_VERIFIED
                    logger.info(f"Auth yuklandi: {len(self._verified_users)} ta tasdiqlangan foydalanuvchi")
        except Exception as e:
            logger.error(f"Auth faylini yuklash xatosi: {e}")

    def _save_auth_users(self):
        """Auth faylga saqlash"""
        try:
            os.makedirs(os.path.dirname(self._auth_file), exist_ok=True)
            with open(self._auth_file, "w") as f:
                json.dump({
                    "verified_users": self._verified_users,
                    "phone_to_chat": self._phone_to_chat
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Auth faylini saqlash xatosi: {e}")

    def _is_authenticated(self, chat_id: str) -> bool:
        """Foydalanuvchi tasdiqlangan mi? Biznes guruhlari ham ruxsat oladi."""
        if self._auth_states.get(chat_id) == AUTH_VERIFIED:
            return True
        # Biznesga biriktirilgan guruhlar avtomatik ruxsat oladi
        if chat_id in self._business_groups.values():
            return True
        return False

    def _is_admin(self, chat_id: str) -> bool:
        """Foydalanuvchi admin mi? (ADMIN_PHONES ro'yxatida)"""
        user_data = self._verified_users.get(chat_id)
        if not user_data:
            return False
        return user_data.get("phone") in ADMIN_PHONES

    def _get_user_business_id(self, chat_id: str) -> Optional[int]:
        """Foydalanuvchining business_id sini olish"""
        user_data = self._verified_users.get(chat_id)
        if not user_data:
            return None
        return user_data.get("business_id")

    def _generate_otp(self, chat_id: str, phone: str, business_id=None, business_title="") -> str:
        """4-raqamli OTP yaratish (5 daqiqa amal qiladi)"""
        code = str(random.randint(1000, 9999))
        self._auth_otps[chat_id] = {
            "code": code,
            "phone": phone,
            "expires": datetime.now().timestamp() + 300,
            "business_id": business_id,
            "business_title": business_title
        }
        return code

    def _verify_otp(self, chat_id: str, entered_code: str) -> str:
        """OTP tekshirish. Returns: 'valid', 'invalid', 'expired'"""
        otp_data = self._auth_otps.get(chat_id)
        if not otp_data:
            return "expired"
        if datetime.now().timestamp() > otp_data["expires"]:
            self._auth_otps.pop(chat_id, None)
            return "expired"
        if otp_data["code"] == entered_code.strip():
            return "valid"
        return "invalid"

    async def _find_business_by_phone(self, phone: str) -> Optional[Dict]:
        """Telefon raqam bo'yicha biznesni topish"""
        if not self.nonbor_service:
            return None
        businesses = await self.nonbor_service.get_businesses()
        if not businesses:
            return None

        # Raqamni normalizatsiya qilish
        normalized = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if normalized.startswith("+"):
            without_plus = normalized[1:]
        else:
            without_plus = normalized
            normalized = "+" + normalized

        for biz in businesses:
            biz_phone = (biz.get("phone_number") or "").replace(" ", "").replace("-", "")
            if not biz_phone:
                continue
            biz_without_plus = biz_phone.lstrip("+")
            if without_plus == biz_without_plus:
                return biz
        return None

    async def _start_auth_flow(self, chat_id: str):
        """Auth jarayonini boshlash"""
        self._auth_states[chat_id] = AUTH_AWAITING_PHONE
        await self.telegram.send_message(
            text=(
                "📋 <b>Assalomu alaykum Nonbor buyurtmalar botiga xush kelibsiz!</b>\n\n"
                "Bot orqali siz buyurtmalar haqida xabar va statistikalarni ko'rishingiz mumkin.\n\n"
                "<b>Kirish:</b>\n"
                "1. business.nonbor.uz da ro'yxatdan o'ting.\n"
                "2. Ro'yxatdagi raqamni quyida yozing.\n"
                "3. Tasdiqlash kodini kiriting.\n\n"
                "<b>Guruhga ulash (ixtiyoriy):</b>\n"
                "1. Guruh yarating va @Nonborbuyurtmalar_bot ni guruhingizga admin qiling. "
                "(o'zingizning guruhingiz bo'sa keyingi qadamlarni bajaring).\n"
                "2. @userinfobot ga kirib start bosing.\n"
                "3. Menyudan Group bo'limini tanlab o'zingizning guruhingizni yuboring. "
                "Bot sizga guruhingiz IDsini yuboradi. ID raqamni nusxalang.\n"
                "4. Botda \"Bizneslar\" → biznesingiz → \"Guruh ID\" → ID kiriting "
                "va yuborish tugmasini bosing.\n"
                "Tabriklaymiz sizning guruhingiz muvaffaqiyatli qo'shildi. "
                "Endi buyurtmalaringizni onlayn kuzatib borishingiz mumkin.\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "📱 <b>Telefon raqamingizni kiriting:</b>\n"
                "<i>Masalan: +998901234567</i>"
            ),
            chat_id=chat_id,
            parse_mode="HTML"
        )

    async def _handle_phone_input(self, chat_id: str, phone: str):
        """Telefon raqamni tekshirish va OTP yuborish"""
        # Raqamni normalizatsiya (avval qilish kerak - admin tekshirish uchun)
        normalized = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if not normalized.startswith("+"):
            normalized = "+" + normalized

        # Admin raqammi tekshirish - biznes tekshiruvisiz
        is_admin_phone = normalized in ADMIN_PHONES

        if is_admin_phone:
            # Admin uchun biznes tekshiruv shart emas
            business = {"id": None, "title": "🔐 Admin"}
        else:
            # Oddiy foydalanuvchi - biznesni topish
            business = await self._find_business_by_phone(phone)
            if not business:
                await self.telegram.send_message(
                    text=(
                        "❌ <b>Raqam topilmadi</b>\n\n"
                        "Bu raqam tizimda ro'yxatdan o'tmagan.\n"
                        "📱 Boshqa raqam kiriting:"
                    ),
                    chat_id=chat_id,
                    parse_mode="HTML"
                )
                return

        # Boshqa foydalanuvchi allaqachon shu raqamni tasdiqlaganmi?
        existing_chat = self._phone_to_chat.get(normalized)
        if existing_chat and existing_chat != chat_id:
            # Asl egasi uchun OTP yaratish
            self._auth_phones[existing_chat] = normalized
            self._auth_states[existing_chat] = AUTH_AWAITING_OTP
            otp_code = self._generate_otp(
                existing_chat, normalized,
                business_id=business.get("id"),
                business_title=business.get("title", "")
            )
            # Asl egasiga ogohlantirish + OTP yuborish
            await self.telegram.send_message(
                text=(
                    "⚠️ <b>OGOHLANTIRISH!</b>\n\n"
                    "Kimdir sizning biznes raqamingiz bilan "
                    "botga kirishga harakat qilmoqda!\n\n"
                    f"📱 Raqam: <code>{normalized}</code>\n"
                    f"🕐 Vaqt: {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}\n\n"
                    f"🔑 <b>Tasdiqlash kodi:</b> <code>{otp_code}</code>\n"
                    f"<i>Siz ekanligingizni tasdiqlash uchun kodni kiriting:</i>"
                ),
                chat_id=existing_chat,
                parse_mode="HTML"
            )
            # Hozirgi foydalanuvchiga rad javob
            await self.telegram.send_message(
                text=(
                    "🚫 <b>Kirish rad etildi</b>\n\n"
                    "Bu raqam boshqa akkauntda tasdiqlangan.\n"
                    "📱 Boshqa raqam kiriting:"
                ),
                chat_id=chat_id,
                parse_mode="HTML"
            )
            return

        # OTP yaratish va yuborish
        self._auth_phones[chat_id] = normalized
        self._auth_states[chat_id] = AUTH_AWAITING_OTP
        otp_code = self._generate_otp(
            chat_id, normalized,
            business_id=business.get("id"),
            business_title=business.get("title", "")
        )

        if is_admin_phone:
            await self.telegram.send_message(
                text=(
                    f"🔐 <b>Admin raqami aniqlandi</b>\n\n"
                    f"🔑 Tasdiqlash kodi: <code>{otp_code}</code>\n\n"
                    f"<i>Kodni kiriting (5 daqiqa amal qiladi):</i>"
                ),
                chat_id=chat_id,
                parse_mode="HTML"
            )
        else:
            await self.telegram.send_message(
                text=(
                    f"✅ <b>Biznes topildi:</b> {business.get('title', '')}\n\n"
                    f"🔑 Tasdiqlash kodi: <code>{otp_code}</code>\n\n"
                    f"<i>Kodni kiriting (5 daqiqa amal qiladi):</i>"
                ),
                chat_id=chat_id,
                parse_mode="HTML"
            )

    async def _handle_otp_input(self, chat_id: str, code: str):
        """OTP tasdiqlash"""
        result = self._verify_otp(chat_id, code)

        if result == "expired":
            self._auth_states[chat_id] = AUTH_IDLE
            self._auth_phones.pop(chat_id, None)
            await self.telegram.send_message(
                text=(
                    "⏰ <b>Kod muddati tugadi</b>\n\n"
                    "Qaytadan /start bosing."
                ),
                chat_id=chat_id,
                parse_mode="HTML"
            )
            return

        if result == "invalid":
            await self.telegram.send_message(
                text="❌ Kod noto'g'ri. Qayta kiriting:",
                chat_id=chat_id,
                parse_mode="HTML"
            )
            return

        # Tasdiqlandi
        otp_data = self._auth_otps.pop(chat_id, {})
        phone = otp_data.get("phone", self._auth_phones.get(chat_id, ""))
        business_id = otp_data.get("business_id")
        business_title = otp_data.get("business_title", "")

        self._auth_states[chat_id] = AUTH_VERIFIED
        self._verified_users[chat_id] = {
            "phone": phone,
            "business_id": business_id,
            "business_title": business_title,
            "verified_at": datetime.now().isoformat()
        }
        self._phone_to_chat[phone] = chat_id
        self._save_auth_users()
        self._auth_phones.pop(chat_id, None)

        await self.telegram.send_message(
            text=f"✅ <b>Muvaffaqiyatli!</b>\n\nXush kelibsiz, {business_title}!",
            chat_id=chat_id,
            parse_mode="HTML"
        )
        await self.send_stats_message(chat_id)

    # ===== AUTH METODLAR TUGADI =====

    async def start_polling(self):
        """Callback query polling boshlash"""
        if self._running:
            return

        self._running = True
        self._polling_task = asyncio.create_task(self._poll_updates())
        logger.info("Telegram stats polling boshlandi")

    async def stop_polling(self):
        """Polling to'xtatish"""
        self._running = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        logger.info("Telegram stats polling to'xtatildi")

    async def _poll_updates(self):
        """Updates polling"""
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling xatosi: {e}")
                await asyncio.sleep(5)

    async def _get_updates(self) -> List[dict]:
        """Telegram updates olish"""
        session = await self.telegram._get_session()
        url = f"{self.telegram.base_url}/getUpdates"

        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": ["callback_query", "message"]
        }

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as response:
                data = await response.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    if updates:
                        self._last_update_id = updates[-1]["update_id"]
                    return updates
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"getUpdates xatosi: {e}")

        return []

    async def _handle_update(self, update: dict):
        """Update ni qayta ishlash"""
        # Text xabarlar (auth bilan)
        message = update.get("message")
        if message:
            text = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))
            chat_type = message.get("chat", {}).get("type", "private")

            # /id - guruh yoki private da chat ID ni ko'rsatish
            if text == "/id" or text.startswith("/id@"):
                await self.telegram.send_message(
                    text=f"<code>{chat_id}</code>",
                    chat_id=chat_id,
                    parse_mode="HTML"
                )
                return

            # Guruh chatlarida hech qanday komandaga javob bermaslik
            if chat_type in ("group", "supergroup"):
                return

            # /start - har doim ishlaydi
            if text == "/start" or text.startswith("/start@"):
                logger.info(f"/start komandasi: {chat_id}")
                if self._is_authenticated(chat_id):
                    await self.send_stats_message(chat_id)
                else:
                    await self._start_auth_flow(chat_id)
                return

            # Auth flow - telefon kiritish
            auth_state = self._auth_states.get(chat_id, AUTH_IDLE)
            if auth_state == AUTH_AWAITING_PHONE and text:
                await self._handle_phone_input(chat_id, text)
                return

            # Auth flow - OTP kiritish
            if auth_state == AUTH_AWAITING_OTP and text:
                await self._handle_otp_input(chat_id, text)
                return

            # === Quyidagi barcha funksiyalar faqat tasdiqlangan foydalanuvchilar uchun ===
            if not self._is_authenticated(chat_id):
                await self.telegram.send_message(
                    text="🔒 Avval /start bosib autentifikatsiyadan o'ting.",
                    chat_id=chat_id,
                    parse_mode="HTML"
                )
                return

            if text == "/stats" or text.startswith("/stats@"):
                logger.info(f"/stats komandasi: {chat_id}")
                await self.send_stats_message(chat_id)
                return

            # Xabarnoma matn kiritish kutilmoqda
            if chat_id in self._awaiting_notif_text and text:
                prompt_msg_id = self._awaiting_notif_text.pop(chat_id)
                user_msg_id = message.get("message_id")
                draft = self._notif_draft.get(chat_id, {})
                draft["text"] = text
                self._notif_draft[chat_id] = draft
                self._save_notif_drafts()
                # Eski xabarlarni o'chirish
                await self.telegram.delete_message(prompt_msg_id, chat_id=chat_id)
                await self.telegram.delete_message(user_msg_id, chat_id=chat_id)
                # Kalendar yuborish (yangi xabar)
                await self._ask_notif_datetime(chat_id)
                return

            # Guruh ID kiritish kutilmoqda
            if chat_id in self._awaiting_group_input and text:
                biz_id = self._awaiting_group_input.pop(chat_id)
                msg_id = self._awaiting_message_id.pop(chat_id, None)
                group_id = text.strip()
                # Guruh ID avtomatik tuzatish - minus qo'shish
                if group_id.isdigit():
                    group_id = "-" + group_id
                self._business_groups[str(biz_id)] = group_id
                self._save_groups()
                logger.info(f"Biznes #{biz_id} uchun guruh ID saqlandi: {group_id}")
                # Nonbor admin panelga ham yuborish
                if self.nonbor:
                    asyncio.create_task(self.nonbor.update_business_group(biz_id, group_id))
                if msg_id:
                    if self._is_admin(chat_id):
                        await self._show_business_detail(msg_id, chat_id, biz_id)
                    else:
                        await self._show_owner_group(msg_id, chat_id)
                return
            return

        # Callback query
        callback_query = update.get("callback_query")
        if not callback_query:
            return

        callback_id = callback_query.get("id")
        data = callback_query.get("data", "")
        callback_message = callback_query.get("message", {})
        message_id = callback_message.get("message_id")
        chat_id = str(callback_message.get("chat", {}).get("id", ""))

        logger.info(f"Callback query: {data}")

        # Answer callback
        await self._answer_callback(callback_id)

        # Auth tekshirish - tasdiqlanmagan foydalanuvchi tugma bosa olmaydi
        if not self._is_authenticated(chat_id):
            await self.telegram.send_message(
                text="🔒 Avval /start bosib autentifikatsiyadan o'ting.",
                chat_id=chat_id,
                parse_mode="HTML"
            )
            return

        # Handle callback
        if data == "noop":
            return

        # Admin bo'lmagan foydalanuvchilar faqat ruxsat berilgan tugmalarni bosa oladi
        if not self._is_admin(chat_id):
            if data.startswith(CALLBACK_BIZ_ADD_GROUP):
                # Guruh qo'shish
                pass
            elif data.startswith(CALLBACK_BIZ_ITEM):
                # Biznes ko'rish
                pass
            elif data == CALLBACK_OWNER_ORDERS or data == "owner_orders":
                # Buyurtmalar ro'yxati - state ni reset qilish
                self._owner_orders_period[chat_id] = "daily"
                self._owner_orders_page[chat_id] = 0
                self._owner_orders_status[chat_id] = "all"
                await self._show_owner_orders(message_id, chat_id)
                return
            elif data == CALLBACK_OWNER_BACK or data == "owner_back":
                # Asosiy menyuga qaytish
                await self._update_business_owner_message(message_id, chat_id)
                return
            elif data == CALLBACK_OWNER_PLANNED:
                await self._show_owner_planned_orders(message_id, chat_id)
                return
            elif data.startswith(CALLBACK_OWNER_PLANNED_PAGE):
                try:
                    page = int(data.replace(CALLBACK_OWNER_PLANNED_PAGE, ""))
                    await self._show_owner_planned_orders(message_id, chat_id, page=page)
                except ValueError:
                    pass
                return
            elif data.startswith(CALLBACK_OWNER_MAIN_PERIOD):
                # Asosiy sahifada davr o'zgartirish
                period = data.replace(CALLBACK_OWNER_MAIN_PERIOD, "")
                await self._update_business_owner_message(message_id, chat_id, period=period)
                return
            elif data == CALLBACK_OWNER_GROUP:
                # Guruh boshqaruvi
                await self._show_owner_group(message_id, chat_id)
                return
            elif data == CALLBACK_OWNER_GROUP_DEL:
                # Guruhni o'chirish - tasdiqlash so'rash
                await self._confirm_delete_owner_group(message_id, chat_id)
                return
            elif data == CALLBACK_OWNER_GROUP_DEL_CONFIRM:
                # Guruhni o'chirish tasdiqlandi
                await self._delete_owner_group(message_id, chat_id)
                return
            elif data.startswith(CALLBACK_OWNER_PERIOD):
                # Davr o'zgartirish
                period = data.replace(CALLBACK_OWNER_PERIOD, "")
                self._owner_orders_page[chat_id] = 0  # Sahifani reset
                await self._show_owner_orders(message_id, chat_id, period=period)
                return
            elif data.startswith(CALLBACK_OWNER_PAGE):
                # Sahifa o'zgartirish
                try:
                    page = int(data.replace(CALLBACK_OWNER_PAGE, ""))
                    await self._show_owner_orders(message_id, chat_id, page=page)
                except ValueError:
                    pass
                return
            elif data.startswith(CALLBACK_OWNER_STATUS):
                # Status filter o'zgartirish
                status = data.replace(CALLBACK_OWNER_STATUS, "")
                self._owner_orders_page[chat_id] = 0  # Sahifani reset
                await self._show_owner_orders(message_id, chat_id, status_filter=status)
                return
            elif data == CALLBACK_BIZ_BACK or data == CALLBACK_MENU_BACK:
                # Orqaga - oddiy ko'rinishga qaytarish
                user_data = self._verified_users.get(chat_id, {})
                biz_id = user_data.get("business_id")
                if biz_id:
                    await self._show_business_detail(message_id, chat_id, biz_id)
                return
            else:
                # Boshqa barcha tugmalar bloklangan
                return

        if data == CALLBACK_BACK:
            await self._show_main_stats(message_id, chat_id)
        elif data == CALLBACK_CALLS_BACK:
            await self._show_all_calls(message_id, chat_id)
        elif data == CALLBACK_CALLS_1:
            await self._show_calls_list(message_id, chat_id, 1)
        elif data == CALLBACK_CALLS_2:
            await self._show_calls_list(message_id, chat_id, 2)
        elif data == CALLBACK_CALLS_3:
            await self._show_calls_list(message_id, chat_id, 3)
        elif data == CALLBACK_ANSWERED:
            await self._show_answered_calls(message_id, chat_id)
        elif data == CALLBACK_UNANSWERED:
            await self._show_unanswered_calls(message_id, chat_id)
        elif data == CALLBACK_ACCEPTED:
            self._admin_orders_page[chat_id] = 0
            await self._show_orders_menu(message_id, chat_id, status_filter="accepted")
        elif data == CALLBACK_REJECTED:
            self._admin_orders_page[chat_id] = 0
            await self._show_orders_menu(message_id, chat_id, status_filter="rejected")
        elif data == CALLBACK_NO_TELEGRAM:
            self._admin_orders_page[chat_id] = 0
            await self._show_orders_menu(message_id, chat_id, status_filter="notg")
        # Davr tugmalari
        elif data == CALLBACK_DAILY:
            self._current_period = "daily"
            await self._show_main_stats(message_id, chat_id)
        elif data == CALLBACK_WEEKLY:
            self._current_period = "weekly"
            await self._show_main_stats(message_id, chat_id)
        elif data == CALLBACK_MONTHLY:
            self._current_period = "monthly"
            await self._show_main_stats(message_id, chat_id)
        elif data == CALLBACK_YEARLY:
            self._current_period = "yearly"
            await self._show_main_stats(message_id, chat_id)
        # Menu tugmalari
        elif data == CALLBACK_MENU_BUSINESSES or data == CALLBACK_BIZ_REFRESH:
            await self._show_businesses(message_id, chat_id)
        elif data == CALLBACK_MENU_CALLS:
            await self._show_all_calls(message_id, chat_id)
        elif data == CALLBACK_MENU_ORDERS:
            self._admin_orders_page[chat_id] = 0
            self._admin_orders_status[chat_id] = "all"
            await self._show_orders_menu(message_id, chat_id)
        elif data == CALLBACK_MENU_BACK:
            await self._show_main_stats(message_id, chat_id)
        # Reja buyurtmalar
        elif data == CALLBACK_MENU_PLANNED:
            await self._show_planned_orders(message_id, chat_id)
        elif data.startswith(CALLBACK_PLANNED_PAGE):
            page = int(data.replace(CALLBACK_PLANNED_PAGE, ""))
            await self._show_planned_orders(message_id, chat_id, page=page)
        # Xabarnomalar
        elif data == CALLBACK_MENU_NOTIF:
            await self._show_notif_menu(message_id, chat_id)
        elif data == CALLBACK_NOTIF_NEW:
            await self._show_notif_target_type(message_id, chat_id)
        elif data == CALLBACK_NOTIF_LIST:
            await self._show_notif_list(message_id, chat_id)
        elif data == CALLBACK_NOTIF_BACK:
            await self._show_notif_menu(message_id, chat_id)
        elif data == CALLBACK_NOTIF_TARGET_REGION:
            await self._show_notif_regions(message_id, chat_id)
        elif data == CALLBACK_NOTIF_TARGET_DISTRICT:
            await self._show_notif_regions(message_id, chat_id, for_district=True)
        elif data == CALLBACK_NOTIF_TARGET_BIZ:
            self._notif_draft[chat_id] = {"target_type": "businesses", "target_ids": [], "target_names": []}
            self._save_notif_drafts()
            await self._show_notif_biz_select(message_id, chat_id)
        elif data.startswith(CALLBACK_NOTIF_SEL_REGION):
            region_val = data.replace(CALLBACK_NOTIF_SEL_REGION, "")
            await self._handle_notif_region_select(message_id, chat_id, region_val)
        elif data.startswith(CALLBACK_NOTIF_SEL_DISTRICT):
            parts = data.replace(CALLBACK_NOTIF_SEL_DISTRICT, "").split("_")
            region_idx, district_idx = int(parts[0]), int(parts[1])
            await self._handle_notif_district_select(message_id, chat_id, region_idx, district_idx)
        elif data.startswith(CALLBACK_NOTIF_SEL_BIZ_PAGE):
            page = int(data.replace(CALLBACK_NOTIF_SEL_BIZ_PAGE, ""))
            await self._show_notif_biz_select(message_id, chat_id, page=page)
        elif data == CALLBACK_NOTIF_DONE_BIZ:
            draft = self._notif_draft.get(chat_id, {})
            if not draft.get("target_ids"):
                # Kamida bitta biznes tanlash kerak - xabar ko'rsatish
                await self._show_notif_biz_select(message_id, chat_id, warning="❗ Kamida bitta biznes tanlang!")
                return
            await self._ask_notif_text(message_id, chat_id)
        elif data.startswith(CALLBACK_NOTIF_SEL_BIZ):
            biz_id = int(data.replace(CALLBACK_NOTIF_SEL_BIZ, ""))
            await self._handle_notif_biz_toggle(message_id, chat_id, biz_id)
        elif data == CALLBACK_NOTIF_CONFIRM:
            await self._save_notif_draft(message_id, chat_id)
        elif data == CALLBACK_NOTIF_CANCEL:
            self._notif_draft.pop(chat_id, None)
            self._save_notif_drafts()
            await self._show_notif_menu(message_id, chat_id)
        elif data.startswith(CALLBACK_NOTIF_DELETE_CONFIRM):
            notif_id = data.replace(CALLBACK_NOTIF_DELETE_CONFIRM, "")
            await self._delete_notification(message_id, chat_id, notif_id)
        elif data.startswith(CALLBACK_NOTIF_DELETE):
            notif_id = data.replace(CALLBACK_NOTIF_DELETE, "")
            await self._confirm_delete_notification(message_id, chat_id, notif_id)
        elif data.startswith(CALLBACK_NOTIF_PAGE):
            page = int(data.replace(CALLBACK_NOTIF_PAGE, ""))
            await self._show_notif_list(message_id, chat_id, page=page)
        elif data.startswith(CALLBACK_NOTIF_CAL_MIN):
            # MUHIM: ncmin_ tekshiruvi ncm_ dan OLDIN bo'lishi kerak (prefiks to'qnashuvi)
            minute = int(data.replace(CALLBACK_NOTIF_CAL_MIN, ""))
            await self._handle_notif_min_select(message_id, chat_id, minute)
        elif data.startswith(CALLBACK_NOTIF_CAL_MONTH):
            parts = data.replace(CALLBACK_NOTIF_CAL_MONTH, "").split("_")
            year, month = int(parts[0]), int(parts[1])
            await self._show_notif_calendar(message_id, chat_id, year, month)
        elif data.startswith(CALLBACK_NOTIF_CAL_DAY):
            parts = data.replace(CALLBACK_NOTIF_CAL_DAY, "").split("_")
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            await self._handle_notif_day_select(message_id, chat_id, year, month, day)
        elif data.startswith(CALLBACK_NOTIF_CAL_HOUR):
            hour = int(data.replace(CALLBACK_NOTIF_CAL_HOUR, ""))
            await self._handle_notif_hour_select(message_id, chat_id, hour)
        # Admin orders pagination va status filter
        elif data.startswith(CALLBACK_ADMIN_ORDERS_PAGE):
            try:
                page = int(data.replace(CALLBACK_ADMIN_ORDERS_PAGE, ""))
                await self._show_orders_menu(message_id, chat_id, page=page)
            except ValueError:
                pass
        elif data.startswith(CALLBACK_ADMIN_ORDERS_STATUS):
            status = data.replace(CALLBACK_ADMIN_ORDERS_STATUS, "")
            self._admin_orders_page[chat_id] = 0  # Sahifani reset
            await self._show_orders_menu(message_id, chat_id, status_filter=status)
        elif data.startswith(CALLBACK_ADMIN_ORDERS_PERIOD):
            period = data.replace(CALLBACK_ADMIN_ORDERS_PERIOD, "")
            self._current_period = period
            self._admin_orders_page[chat_id] = 0
            await self._show_orders_menu(message_id, chat_id)
        # Bizneslar pagination va region
        elif data.startswith(CALLBACK_BIZ_PAGE):
            page = int(data.replace(CALLBACK_BIZ_PAGE, ""))
            await self._show_businesses(message_id, chat_id, page=page)
        elif data.startswith(CALLBACK_BIZ_REGION):
            region_idx = int(data.replace(CALLBACK_BIZ_REGION, ""))
            await self._show_region_districts(message_id, chat_id, region_idx)
        elif data.startswith(CALLBACK_BIZ_REG_PAGE):
            parts = data.replace(CALLBACK_BIZ_REG_PAGE, "").split("_")
            region_idx, page = int(parts[0]), int(parts[1])
            await self._show_region_districts(message_id, chat_id, region_idx, page=page)
        elif data.startswith(CALLBACK_BIZ_DISTRICT):
            parts = data.replace(CALLBACK_BIZ_DISTRICT, "").split("_")
            region_idx, district_idx = int(parts[0]), int(parts[1])
            await self._show_district_businesses(message_id, chat_id, region_idx, district_idx)
        elif data.startswith(CALLBACK_BIZ_DIST_BACK):
            region_idx = int(data.replace(CALLBACK_BIZ_DIST_BACK, ""))
            await self._show_region_districts(message_id, chat_id, region_idx)
        elif data.startswith(CALLBACK_BIZ_ITEM):
            biz_id = int(data.replace(CALLBACK_BIZ_ITEM, ""))
            await self._show_business_detail(message_id, chat_id, biz_id)
        elif data.startswith(CALLBACK_BIZ_TOGGLE_CALL):
            biz_id = int(data.replace(CALLBACK_BIZ_TOGGLE_CALL, ""))
            if not self._is_admin(chat_id):
                return
            # Toggle
            if biz_id in self._disabled_businesses:
                self._disabled_businesses.discard(biz_id)
                logger.info(f"Avtoqo'ng'iroq YOQILDI: business_id={biz_id}")
            else:
                self._disabled_businesses.add(biz_id)
                logger.info(f"Avtoqo'ng'iroq O'CHIRILDI: business_id={biz_id}")
            self._save_call_settings()
            await self._show_business_detail(message_id, chat_id, biz_id)
        elif data.startswith(CALLBACK_BIZ_ADD_GROUP):
            biz_id = int(data.replace(CALLBACK_BIZ_ADD_GROUP, ""))
            # Admin bo'lmagan foydalanuvchilar faqat o'z biznesiga guruh qo'sha oladi
            if not self._is_admin(chat_id):
                user_biz_id = self._get_user_business_id(chat_id)
                if user_biz_id != biz_id:
                    await self.telegram.edit_message(
                        message_id=message_id,
                        text="🚫 Bu biznesga guruh qo'shishga ruxsat yo'q",
                        chat_id=chat_id,
                        parse_mode="HTML",
                        reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}]]}
                    )
                    return
            self._awaiting_group_input[chat_id] = biz_id
            self._awaiting_message_id[chat_id] = message_id
            # Bekor qilish - admin uchun biznes detailga, owner uchun guruh viewga
            cancel_callback = f"{CALLBACK_BIZ_ITEM}{biz_id}" if self._is_admin(chat_id) else CALLBACK_OWNER_GROUP
            await self.telegram.edit_message(
                message_id=message_id,
                text="📝 <b>Guruh ID sini yuboring:</b>\n\nMasalan: <code>-1001234567890</code>",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[
                    {"text": "❌ Bekor qilish", "callback_data": cancel_callback}
                ]]}
            )
        elif data == CALLBACK_BIZ_BACK:
            await self._show_businesses(message_id, chat_id)
        elif data == CALLBACK_OWNER_ORDERS or data == "owner_orders":
            # Buyurtmalar ro'yxati - state ni reset qilish
            self._owner_orders_period[chat_id] = "daily"
            self._owner_orders_page[chat_id] = 0
            self._owner_orders_status[chat_id] = "all"
            await self._show_owner_orders(message_id, chat_id)
        elif data == CALLBACK_OWNER_BACK or data == "owner_back":
            await self._update_business_owner_message(message_id, chat_id)
        elif data.startswith(CALLBACK_OWNER_MAIN_PERIOD):
            period = data.replace(CALLBACK_OWNER_MAIN_PERIOD, "")
            await self._update_business_owner_message(message_id, chat_id, period=period)
        elif data == CALLBACK_OWNER_GROUP:
            await self._show_owner_group(message_id, chat_id)
        elif data == CALLBACK_OWNER_GROUP_DEL:
            await self._delete_owner_group(message_id, chat_id)
        elif data.startswith(CALLBACK_OWNER_PERIOD):
            # Davr o'zgartirish
            period = data.replace(CALLBACK_OWNER_PERIOD, "")
            self._owner_orders_page[chat_id] = 0  # Sahifani reset
            await self._show_owner_orders(message_id, chat_id, period=period)
        elif data.startswith(CALLBACK_OWNER_PAGE):
            # Sahifa o'zgartirish
            try:
                page = int(data.replace(CALLBACK_OWNER_PAGE, ""))
                await self._show_owner_orders(message_id, chat_id, page=page)
            except ValueError:
                pass
        elif data.startswith(CALLBACK_OWNER_STATUS):
            # Status filter o'zgartirish
            status = data.replace(CALLBACK_OWNER_STATUS, "")
            self._owner_orders_page[chat_id] = 0  # Sahifani reset
            await self._show_owner_orders(message_id, chat_id, status_filter=status)

    async def _answer_callback(self, callback_id: str):
        """Callback query javob"""
        session = await self.telegram._get_session()
        url = f"{self.telegram.base_url}/answerCallbackQuery"

        try:
            await session.post(url, json={"callback_query_id": callback_id})
        except Exception as e:
            logger.error(f"answerCallbackQuery xatosi: {e}")

    def _get_period_title(self) -> str:
        """Davr sarlavhasini olish"""
        titles = {
            "daily": "BUGUNGI",
            "weekly": "HAFTALIK (7 kun)",
            "monthly": "OYLIK (30 kun)",
            "yearly": "YILLIK (365 kun)"
        }
        return titles.get(self._current_period, "BUGUNGI")

    async def send_stats_message(self, chat_id: str = None) -> Optional[int]:
        """Statistika xabarini yuborish"""
        if not self.stats_service:
            return None

        chat_id = chat_id or self.telegram.default_chat_id

        # Admin bo'lmagan foydalanuvchilar uchun oddiy ko'rinish
        if not self._is_admin(chat_id):
            return await self._send_business_owner_message(chat_id)

        self._current_period = "daily"  # Har doim kunlik bilan boshlash
        stats = self.stats_service.get_period_stats(self._current_period)

        title = self._get_period_title()

        # API dan jarayondagi va reja buyurtmalar sonini olish
        planned_count = 0
        active_count = 0
        if self.nonbor_service:
            try:
                orders = await self.nonbor_service.get_orders()
                logger.info(f"Stats(start): get_orders() natijasi: {type(orders)}, {len(orders) if orders else 0} ta")
                if orders:
                    skip = {"PENDING", "WAITING_PAYMENT", "PAYMENTPENDING", "PAYMENT_PENDING"}
                    final = {"COMPLETED", "CANCELLED", "CANCELLED_SELLER", "CANCELLED_CLIENT", "CANCELLED_USER", "CANCELLED_ADMIN", "PAYMENT_EXPIRED"}
                    for o in orders:
                        state = (o.get("state") or "").upper()
                        if state in skip:
                            continue
                        if o.get("is_planned") or o.get("is_planner"):
                            planned_count += 1
                        if state not in final:
                            active_count += 1
            except Exception as e:
                logger.error(f"Stats(start): buyurtmalar olishda xato: {e}")

        text = f"""📊 <b>{title} STATISTIKA</b>
━━━━━━━━━━━━━━━━━━━━

📦 <b>BUYURTMALAR:</b> {stats.total_orders} ta
├ ✅ Qabul qilindi: {stats.accepted_orders}
├ ❌ Bekor qilindi: {stats.rejected_orders}
├ 🚀 Telegram'siz qabul: {stats.accepted_without_telegram}
└ 🔄 Jarayondagi: {active_count}

📞 <b>QO'NG'IROQLAR:</b> {stats.total_calls} ta
├ ✅ Javob berildi: {stats.answered_calls}
├ ❌ Javob berilmadi: {stats.unanswered_calls}
├ 1️⃣ 1-urinishda: {stats.calls_1_attempt}
└ 2️⃣ 2-urinishda: {stats.calls_2_attempts}

📅 <b>REJA BUYURTMALAR:</b> {planned_count} ta

📅 Davr: {stats.date}

<i>Batafsil ko'rish uchun tugmalarni bosing:</i>"""

        keyboard = self._get_stats_keyboard(stats, planned_count=planned_count)

        return await self.telegram.send_message(
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _build_owner_main_content(self, chat_id: str, period: str = "daily"):
        """Biznes egasi asosiy sahifa matn va klaviaturasini yaratish"""
        user_data = self._verified_users.get(chat_id, {})
        biz_id = user_data.get("business_id")
        biz_title = user_data.get("business_title", "Noma'lum")
        user_phone = user_data.get("phone", "")

        group_id = self._business_groups.get(str(biz_id), "") if biz_id else ""

        # Davr nomlari
        period_names = {
            "daily": "Bugungi",
            "weekly": "Haftalik",
            "monthly": "Oylik",
            "yearly": "Yillik"
        }
        period_name = period_names.get(period, "Bugungi")

        # Nonbor API dan buyurtmalar statistikasi (get-order-for-courier)
        status_counts = {"checking": 0, "accepted": 0, "delivering": 0, "delivered": 0, "completed": 0, "rejected": 0, "expired": 0}
        orders_count = 0

        if self.nonbor_service and biz_id:
            raw_orders = await self.nonbor_service.get_orders_by_business(biz_id)

            from datetime import timedelta, timezone
            now = datetime.now(timezone(timedelta(hours=5)))
            if period == "daily":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif period == "weekly":
                start_date = now - timedelta(days=7)
            elif period == "monthly":
                start_date = now - timedelta(days=30)
            elif period == "yearly":
                start_date = now - timedelta(days=365)
            else:
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

            for order in raw_orders:
                created_at = order.get("created_at", "")
                if created_at:
                    try:
                        order_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if order_dt < start_date:
                            continue
                    except:
                        pass
                orders_count += 1
                state = (order.get("state") or "").upper()
                mapped = self._map_api_state(state)
                if mapped in status_counts:
                    status_counts[mapped] += 1

            logger.info(f"Asosiy sahifa: {len(raw_orders)} -> {orders_count} ({period}, start={start_date.strftime('%Y-%m-%d %H:%M')})")

        text = f"🏪 <b>{biz_title}</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📦 <b>{period_name} buyurtmalar:</b> {orders_count} ta\n"
        if orders_count > 0:
            text += f"├ 🕐 Kutilmoqda: {status_counts['checking']}\n"
            text += f"├ ✅ Qabul qilindi: {status_counts['accepted']}\n"
            text += f"├ 🚗 Yetkazilmoqda: {status_counts['delivering']}\n"
            text += f"├ 📬 Yetkazildi: {status_counts['delivered']}\n"
            text += f"├ ✔️ Yakunlandi: {status_counts['completed']}\n"
            text += f"├ ❌ Bekor qilindi: {status_counts['rejected']}\n"
            text += f"└ ⏰ Muddati o'tgan: {status_counts['expired']}\n"
        text += "\n"

        if group_id:
            text += f"👥 <b>Guruh:</b> Ulangan ✅\n"
        else:
            text += f"👥 <b>Guruh:</b> Ulanmagan ❌\n"
            text += f"<i>Guruh qo'shsangiz, buyurtmalar haqida xabar keladi</i>\n"

        # Keyboard
        keyboard_rows = []

        # 0-qator: Davr tugmalari
        period_row = []
        for p_key, p_label in [("daily", "Kunlik"), ("weekly", "Haftalik"), ("monthly", "Oylik"), ("yearly", "Yillik")]:
            btn_text = f"✓ {p_label}" if p_key == period else p_label
            period_row.append({"text": btn_text, "callback_data": f"{CALLBACK_OWNER_MAIN_PERIOD}{p_key}"})
        keyboard_rows.append(period_row)

        # 1-qator: Buyurtmalar | Reja buyurtmalar
        row1 = [
            {"text": "📦 Buyurtmalar", "callback_data": "owner_orders"},
            {"text": "📅 Reja", "callback_data": CALLBACK_OWNER_PLANNED}
        ]
        keyboard_rows.append(row1)

        # 2-qator: Biznes guruh
        if biz_id:
            if group_id:
                keyboard_rows.append([{"text": "👥 Biznes guruh", "callback_data": CALLBACK_OWNER_GROUP}])
            else:
                keyboard_rows.append([{"text": "➕ Biznes guruh", "callback_data": CALLBACK_OWNER_GROUP}])

        # 2-qator: Biznes sayt | Mijoz sayt
        keyboard_rows.append([
            {"text": "🏪 Biznes", "web_app": {"url": "https://business.nonbor.uz/"}},
            {"text": "🛒 Mijoz", "web_app": {"url": "https://nonbor.uz/"}}
        ])

        # 3-qator: Qo'llab quvvatlash
        keyboard_rows.append([
            {"text": "🆘 Qo'llab quvvatlash", "url": "https://t.me/NonborSupportBot"}
        ])

        return text, {"inline_keyboard": keyboard_rows}

    async def _send_business_owner_message(self, chat_id: str) -> Optional[int]:
        """Biznes egasi uchun oddiy ko'rinish - faqat buyurtmalar va guruh"""
        text, reply_markup = await self._build_owner_main_content(chat_id)

        return await self.telegram.send_message(
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    async def _update_business_owner_message(self, message_id: int, chat_id: str, period: str = "daily"):
        """Biznes egasi uchun xabarni tahrirlash (orqaga qaytish)"""
        text, reply_markup = await self._build_owner_main_content(chat_id, period=period)

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    async def _show_owner_group(self, message_id: int, chat_id: str):
        """Biznes egasi uchun guruh boshqaruvi ko'rinishi"""
        user_data = self._verified_users.get(chat_id, {})
        biz_id = user_data.get("business_id")
        biz_title = user_data.get("business_title", "Noma'lum")

        group_id = self._business_groups.get(str(biz_id), "") if biz_id else ""

        text = f"🏪 <b>{biz_title}</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        keyboard_rows = []
        if group_id:
            text += f"👥 <b>Guruh:</b> <code>{group_id}</code> ✅\n\n"
            text += f"<i>Guruh ulangan. Buyurtmalar haqida xabar shu guruhga yuboriladi.</i>\n"
            keyboard_rows.append([
                {"text": "✏️ O'zgartirish", "callback_data": f"{CALLBACK_BIZ_ADD_GROUP}{biz_id}"},
                {"text": "🗑 O'chirish", "callback_data": CALLBACK_OWNER_GROUP_DEL}
            ])
        else:
            text += f"👥 <b>Guruh:</b> Ulanmagan ❌\n\n"
            text += f"<i>Guruh qo'shsangiz, buyurtmalar haqida xabar keladi.</i>\n"
            keyboard_rows.append([
                {"text": "➕ Guruh qo'shish", "callback_data": f"{CALLBACK_BIZ_ADD_GROUP}{biz_id}"}
            ])

        keyboard_rows.append([{"text": "⬅️ Orqaga", "callback_data": CALLBACK_OWNER_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _confirm_delete_owner_group(self, message_id: int, chat_id: str):
        """Guruh o'chirish uchun tasdiqlash so'rash"""
        user_data = self._verified_users.get(chat_id, {})
        biz_title = user_data.get("business_title", "Noma'lum")
        biz_id = user_data.get("business_id")
        group_id = self._business_groups.get(str(biz_id), "") if biz_id else ""

        text = f"⚠️ <b>Guruhni o'chirishni tasdiqlaysizmi?</b>\n\n"
        text += f"🏪 <b>Biznes:</b> {biz_title}\n"
        text += f"👥 <b>Guruh:</b> <code>{group_id}</code>\n\n"
        text += f"<i>O'chirilgandan keyin buyurtmalar haqida guruhga xabar kelmaydi.</i>"

        keyboard = {"inline_keyboard": [
            [
                {"text": "✅ Ha, o'chirish", "callback_data": CALLBACK_OWNER_GROUP_DEL_CONFIRM},
                {"text": "❌ Bekor qilish", "callback_data": CALLBACK_OWNER_GROUP}
            ]
        ]}

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _delete_owner_group(self, message_id: int, chat_id: str):
        """Biznes egasi guruhini o'chirish"""
        user_data = self._verified_users.get(chat_id, {})
        biz_id = user_data.get("business_id")

        if biz_id and str(biz_id) in self._business_groups:
            del self._business_groups[str(biz_id)]
            self._save_groups()
            logger.info(f"Biznes #{biz_id} guruhi o'chirildi (chat: {chat_id})")

        # Guruh boshqaruviga qaytish
        await self._show_owner_group(message_id, chat_id)

    def _map_api_state(self, state: str) -> str:
        """API state ni filter status ga map qilish"""
        state = (state or "").upper()
        if state in ("CHECKING", "PENDING", "WAITING_PAYMENT"):
            return "checking"
        elif state in ("ACCEPTED", "READY"):
            return "accepted"
        elif state.startswith("CANCELLED"):
            return "rejected"
        elif state in ("ACCEPT_EXPIRED", "PAYMENT_EXPIRED"):
            return "expired"
        elif state == "DELIVERING":
            return "delivering"
        elif state == "DELIVERED":
            return "delivered"
        elif state == "COMPLETED":
            return "completed"
        return "checking"

    async def _show_owner_planned_orders(self, message_id: int, chat_id: str, page: int = 0):
        """Biznes egasi uchun reja buyurtmalar ro'yxati"""
        user_data = self._verified_users.get(chat_id, {})
        biz_id = user_data.get("business_id")
        biz_title = user_data.get("business_title", "Noma'lum")

        if not self.nonbor_service or not biz_id:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Ma'lumot olishda xatolik",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_OWNER_BACK}]]}
            )
            return

        # Biznesga tegishli barcha buyurtmalarni olish
        orders = await self.nonbor_service.get_orders_by_business(int(biz_id))

        # Faqat reja buyurtmalar
        planned_orders = []
        for order in orders:
            is_planned = order.get("is_planned") or order.get("is_planner")
            if not is_planned:
                continue
            status = (order.get("state") or "CHECKING").upper()
            skip = ["PENDING", "WAITING_PAYMENT", "PAYMENTPENDING", "PAYMENT_PENDING"]
            if status in skip:
                continue
            planned_orders.append(order)

        # Pagination
        ITEMS_PER_PAGE = 10
        total = len(planned_orders)
        total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        start_idx = page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, total)
        page_orders = planned_orders[start_idx:end_idx]

        STATUS_EMOJI = {
            "CHECKING": "🟡", "ACCEPTED": "🟢", "READY": "✅",
            "DELIVERING": "🚗", "DELIVERED": "📦", "COMPLETED": "🏁",
            "CANCELLED": "❌", "CANCELLED_SELLER": "🚫", "CANCELLED_CLIENT": "❌",
            "ACCEPT_EXPIRED": "⏰",
        }
        STATUS_NAMES = {
            "CHECKING": "Tekshirilmoqda", "ACCEPTED": "Qabul qilindi",
            "READY": "Tayyor", "DELIVERING": "Yetkazilmoqda",
            "DELIVERED": "Yetkazildi", "COMPLETED": "Yakunlandi",
            "CANCELLED": "Bekor qilindi", "CANCELLED_SELLER": "Sotuvchi bekor qildi",
            "CANCELLED_CLIENT": "Mijoz bekor qildi", "ACCEPT_EXPIRED": "Muddati o'tdi",
        }

        text = f"📅 <b>REJA BUYURTMALAR</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"🏪 {biz_title}\n"
        text += f"📊 Jami: <b>{total}</b> ta\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        if not page_orders:
            text += "<i>Reja buyurtmalar topilmadi</i>\n"
        else:
            for i, order in enumerate(page_orders, start_idx + 1):
                order_id = order.get("id", "?")
                status = (order.get("state") or "CHECKING").upper()
                emoji = STATUS_EMOJI.get(status, "📋")

                # Mahsulot
                items = order.get("order_item") or order.get("items") or []
                product_name = ""
                if items:
                    product = items[0].get("product") or {}
                    product_name = (product.get("title") or product.get("name") or "")[:20]

                # Narx
                price = (order.get("total_price", 0) or 0) / 100
                price_str = f"{price:,.0f}".replace(",", " ") if price else ""

                # Reja vaqti
                raw_planned = order.get("planned_datetime") or order.get("planned_time") or ""
                planned_time = ""
                if raw_planned:
                    try:
                        uz_tz = timezone(timedelta(hours=5))
                        dt = datetime.fromisoformat(str(raw_planned).replace('Z', '+00:00'))
                        dt_uz = dt.astimezone(uz_tz)
                        planned_time = dt_uz.strftime("%d.%m %H:%M")
                    except:
                        planned_time = str(raw_planned)

                status_name = STATUS_NAMES.get(status, status)

                text += f"<b>{i}.</b> {emoji} <b>#{order_id}</b>"
                if planned_time:
                    text += f" — 📅 {planned_time}"
                text += f"\n   📊 {status_name}"
                if product_name:
                    text += f"\n   🏷 {product_name}"
                if price_str:
                    text += f" | 💰 {price_str}"
                text += "\n\n"

        # Keyboard
        keyboard_rows = []
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append({"text": "⬅️", "callback_data": f"{CALLBACK_OWNER_PLANNED_PAGE}{page - 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})
            nav_row.append({"text": f"{page + 1}/{total_pages}", "callback_data": "noop"})
            if page < total_pages - 1:
                nav_row.append({"text": "➡️", "callback_data": f"{CALLBACK_OWNER_PLANNED_PAGE}{page + 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})
            keyboard_rows.append(nav_row)

        keyboard_rows.append([{"text": "◀️ Orqaga", "callback_data": CALLBACK_OWNER_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _show_owner_orders(self, message_id: int, chat_id: str, period: str = None, page: int = None, status_filter: str = None):
        """Biznes egasi uchun buyurtmalar ro'yxati - Nonbor API dan (get-order-for-courier)"""
        user_data = self._verified_users.get(chat_id, {})
        biz_title = user_data.get("business_title", "Noma'lum")
        biz_id = user_data.get("business_id")

        # State dan olish (agar parametr berilmagan bo'lsa)
        if period is None:
            period = self._owner_orders_period.get(chat_id, "daily")
        if page is None:
            page = self._owner_orders_page.get(chat_id, 0)
        if status_filter is None:
            status_filter = self._owner_orders_status.get(chat_id, "all")

        # State ni yangilash
        self._owner_orders_period[chat_id] = period
        self._owner_orders_page[chat_id] = page
        self._owner_orders_status[chat_id] = status_filter

        # Davr bo'yicha sana chegarasi
        from datetime import timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=5)))  # UZ timezone +5
        if period == "daily":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "weekly":
            start_date = now - timedelta(days=7)
        elif period == "monthly":
            start_date = now - timedelta(days=30)
        elif period == "yearly":
            start_date = now - timedelta(days=365)
        else:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # get-order-for-courier dan business_id bo'yicha olish
        api_orders = []

        if self.nonbor_service and biz_id:
            raw_orders = await self.nonbor_service.get_orders_by_business(biz_id)

            # Sana bo'yicha filtrlash
            for order in raw_orders:
                created_at = order.get("created_at", "")
                if created_at:
                    try:
                        order_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if order_dt >= start_date:
                            api_orders.append(order)
                    except:
                        api_orders.append(order)
                else:
                    api_orders.append(order)

            logger.info(f"Buyurtmalar filtrlandi: {len(raw_orders)} -> {len(api_orders)} ({period}, start={start_date.strftime('%Y-%m-%d %H:%M')})")

        # Status counts ni filtrlangan buyurtmalardan hisoblash
        status_counts = {"all": 0, "checking": 0, "accepted": 0, "rejected": 0, "expired": 0, "delivering": 0, "delivered": 0, "completed": 0}

        # API buyurtmalarni ichki formatga o'tkazish
        all_orders = []
        for order in api_orders:
            state = (order.get("state") or "").upper()
            mapped_status = self._map_api_state(state)

            # Mijoz ma'lumotlari
            user = order.get("user") or {}
            first_name = user.get("first_name", "")
            last_name = user.get("last_name", "")
            client_name = f"{first_name} {last_name}".strip() or "Noma'lum"

            # Vaqt
            timestamp = order.get("created_at") or ""

            all_orders.append({
                "order_number": str(order.get("id", "?")),
                "order_status": state,
                "mapped_status": mapped_status,
                "client_name": client_name,
                "timestamp": timestamp,
                "items": order.get("order_item") or order.get("items") or [],
            })

            # Status counts ni filtrlangan buyurtmalardan hisoblash
            ms = mapped_status
            if ms in status_counts:
                status_counts[ms] += 1
            status_counts["all"] += 1

        # Status filter qo'llash
        if status_filter != "all":
            orders_list = [o for o in all_orders if o["mapped_status"] == status_filter]
        else:
            orders_list = all_orders

        # Pagination (har bir buyurtma tafsilotli, 10 tadan ko'rsatamiz)
        ITEMS_PER_PAGE = 10
        total_orders = len(orders_list)
        total_pages = max(1, (total_orders + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, total_orders)
        page_orders = orders_list[start_idx:end_idx]

        # Status va davr nomlari
        status_names = {"all": "Barchasi", "checking": "Tekshirilmoqda", "accepted": "Qabul qilingan", "rejected": "Bekor qilingan", "expired": "Muddati o'tgan", "delivering": "Yetkazilmoqda", "delivered": "Yetkazildi", "completed": "Tayyor"}
        status_name = status_names.get(status_filter, "Barchasi")
        period_names = {"daily": "Bugungi", "weekly": "Haftalik", "monthly": "Oylik", "yearly": "Yillik"}
        period_name = period_names.get(period, "Bugungi")

        # Matn
        text = f"📦 <b>{biz_title}</b> ({period_name})\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"🔍 {status_name}\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        if not biz_id and self.nonbor_service:
            text += "<i>Biznes topilmadi</i>\n"
        elif not page_orders:
            text += "<i>Buyurtmalar topilmadi</i>\n"
        else:

            for i, order in enumerate(page_orders, start_idx + 1):
                order_num = order.get("order_number", "?")
                client_name = order.get("client_name", "Noma'lum")[:15]
                timestamp = order.get("timestamp", "")
                order_status = order.get("order_status", "")

                # Vaqtni format qilish
                time_str = ""
                if timestamp:
                    try:
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        time_str = dt.strftime("%d.%m %H:%M")
                    except:
                        pass

                # Status emoji
                if order_status == "COMPLETED":
                    emoji = "🏁"
                elif order_status == "DELIVERED":
                    emoji = "📬"
                elif order_status == "DELIVERING":
                    emoji = "🚚"
                elif order_status == "ACCEPTED":
                    emoji = "✅"
                elif order_status.startswith("CANCELLED"):
                    emoji = "❌"
                elif order_status == "ACCEPT_EXPIRED":
                    emoji = "⏰"
                else:
                    emoji = "⏳"

                # Tartib raqam + status + buyurtma raqami + vaqt
                text += f"<b>{i}.</b> {emoji} <b>#{order_num}</b>"
                if time_str:
                    text += f" ({time_str})"
                text += f" - {client_name}\n"

                # Mahsulotlar tafsiloti
                order_items = order.get("items", [])
                if order_items:
                    total_sum = 0
                    for item in order_items:
                        product = item.get("product") or {}
                        product_name = (product.get("name") or product.get("title", "?"))[:20]
                        qty = item.get("count", 1)
                        price = item.get("price", 0)
                        price_som = int(price) // 100 if price else 0
                        item_total = price_som * qty
                        total_sum += item_total
                        text += f"   📌 {product_name} x{qty} = {item_total:,} so'm\n"
                    if len(order_items) > 1:
                        text += f"   💰 <b>Jami: {total_sum:,} so'm</b>\n"
                text += "\n"

        # Footer
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📊 Jami: {total_orders} ta\n"
        if total_pages > 1:
            text += f"📄 Sahifa: {page + 1}/{total_pages}"

        # Keyboard
        keyboard_rows = []

        # 1. Davr tugmalari
        period_row = []
        for p_key, p_label in [("daily", "Kunlik"), ("weekly", "Hafta"), ("monthly", "Oylik"), ("yearly", "Yillik")]:
            btn_text = f"✓ {p_label}" if p_key == period else p_label
            period_row.append({"text": btn_text, "callback_data": f"{CALLBACK_OWNER_PERIOD}{p_key}"})
        keyboard_rows.append(period_row)

        # 2. Status filter tugmalari (sonlar bilan)
        all_statuses = [
            [("📋 Barchasi", "all"), ("🔍 Tekshiruv", "checking")],
            [("✅ Qabul", "accepted"), ("❌ Bekor", "rejected")],
            [("⏰ Mud o'tgan", "expired"), ("🚚 Yetkazuv", "delivering")],
            [("📬 Yetkazildi", "delivered"), ("🏁 Tayyor", "completed")],
        ]
        for row_statuses in all_statuses:
            row = []
            for label, s in row_statuses:
                cnt = status_counts.get(s, 0)
                btn_text = f"{label}({cnt})" if cnt else label
                if s == status_filter:
                    btn_text = f"✓ {btn_text}"
                row.append({"text": btn_text, "callback_data": f"{CALLBACK_OWNER_STATUS}{s}"})
            keyboard_rows.append(row)

        # 3. Pagination
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append({"text": "⬅️", "callback_data": f"{CALLBACK_OWNER_PAGE}{page - 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})

            nav_row.append({"text": f"{page + 1}/{total_pages}", "callback_data": "noop"})

            if page < total_pages - 1:
                nav_row.append({"text": "➡️", "callback_data": f"{CALLBACK_OWNER_PAGE}{page + 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})

            keyboard_rows.append(nav_row)

        # 4. Orqaga tugmasi
        keyboard_rows.append([{"text": "⬅️ Orqaga", "callback_data": CALLBACK_OWNER_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    def _get_stats_keyboard(self, stats, planned_count: int = 0) -> dict:
        """Statistika inline keyboard"""
        # Davr tugmalari - tanlangan davr belgilanadi
        period_buttons = []
        periods = [
            ("📅 Kunlik", CALLBACK_DAILY, "daily"),
            ("📆 Haftalik", CALLBACK_WEEKLY, "weekly"),
            ("🗓 Oylik", CALLBACK_MONTHLY, "monthly"),
            ("📊 Yillik", CALLBACK_YEARLY, "yearly")
        ]
        for label, callback, period in periods:
            if period == self._current_period:
                period_buttons.append({"text": f"✓ {label}", "callback_data": callback})
            else:
                period_buttons.append({"text": label, "callback_data": callback})

        return {
            "inline_keyboard": [
                period_buttons[:2],  # Kunlik, Haftalik
                period_buttons[2:],  # Oylik, Yillik
                [
                    {"text": f"📦 Buyurtmalar ({stats.total_orders})", "callback_data": CALLBACK_MENU_ORDERS},
                    {"text": f"📞 Qo'ng'iroqlar ({stats.total_calls})", "callback_data": CALLBACK_MENU_CALLS}
                ],
                [
                    {"text": "👥 Bizneslar", "callback_data": CALLBACK_MENU_BUSINESSES},
                    {"text": "📢 Xabarnomalar", "callback_data": CALLBACK_MENU_NOTIF}
                ],
                [
                    {"text": f"📅 Reja buyurtmalar ({planned_count})", "callback_data": CALLBACK_MENU_PLANNED}
                ]
            ]
        }

    async def _show_main_stats(self, message_id: int, chat_id: str):
        """Asosiy statistika sahifasiga qaytish"""
        if not self.stats_service:
            return

        stats = self.stats_service.get_period_stats(self._current_period)
        title = self._get_period_title()

        # API dan jarayondagi va reja buyurtmalar sonini olish
        planned_count = 0
        active_count = 0
        if self.nonbor_service:
            try:
                orders = await self.nonbor_service.get_orders()
                logger.info(f"Stats: get_orders() natijasi: {type(orders)}, {len(orders) if orders else 0} ta")
                if orders:
                    skip = {"PENDING", "WAITING_PAYMENT", "PAYMENTPENDING", "PAYMENT_PENDING"}
                    final = {"COMPLETED", "CANCELLED", "CANCELLED_SELLER", "CANCELLED_CLIENT", "CANCELLED_USER", "CANCELLED_ADMIN", "PAYMENT_EXPIRED"}
                    states_found = {}
                    for o in orders:
                        state = (o.get("state") or "").upper()
                        states_found[state] = states_found.get(state, 0) + 1
                        if state in skip:
                            continue
                        if o.get("is_planned") or o.get("is_planner"):
                            planned_count += 1
                        if state not in final:
                            active_count += 1
                    logger.info(f"Stats: statuslar={states_found}, active={active_count}, planned={planned_count}")
            except Exception as e:
                logger.error(f"Stats: buyurtmalar olishda xato: {e}")

        text = f"""📊 <b>{title} STATISTIKA</b>
━━━━━━━━━━━━━━━━━━━━

📦 <b>BUYURTMALAR:</b> {stats.total_orders} ta
├ ✅ Qabul qilindi: {stats.accepted_orders}
├ ❌ Bekor qilindi: {stats.rejected_orders}
├ 🚀 Telegram'siz qabul: {stats.accepted_without_telegram}
└ 🔄 Jarayondagi: {active_count}

📞 <b>QO'NG'IROQLAR:</b> {stats.total_calls} ta
├ ✅ Javob berildi: {stats.answered_calls}
├ ❌ Javob berilmadi: {stats.unanswered_calls}
├ 1️⃣ 1-urinishda: {stats.calls_1_attempt}
└ 2️⃣ 2-urinishda: {stats.calls_2_attempts}

📅 <b>REJA BUYURTMALAR:</b> {planned_count} ta

📅 Davr: {stats.date}

<i>Batafsil ko'rish uchun tugmalarni bosing:</i>"""

        keyboard = self._get_stats_keyboard(stats, planned_count=planned_count)

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_businesses(self, message_id: int, chat_id: str, page: int = 0):
        """Bizneslar ro'yxati - pagination bilan"""
        if not self.nonbor_service:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Nonbor servisi ulanmagan",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}]]}
            )
            return

        businesses = await self.nonbor_service.get_businesses()
        if not businesses:
            await self.telegram.edit_message(
                message_id=message_id,
                text="📭 Bizneslar topilmadi",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "🔄 Yangilash", "callback_data": CALLBACK_BIZ_REFRESH}],
                    [{"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}]
                ]}
            )
            return

        # Admin bo'lmagan foydalanuvchilar faqat o'z biznesini ko'radi
        if not self._is_admin(chat_id):
            user_biz_id = self._get_user_business_id(chat_id)
            if user_biz_id:
                await self._show_business_detail(message_id, chat_id, user_biz_id)
                return
            else:
                await self.telegram.edit_message(
                    message_id=message_id,
                    text="❌ Sizning biznesingiz topilmadi",
                    chat_id=chat_id,
                    parse_mode="HTML",
                    reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}]]}
                )
                return

        # Viloyat bo'yicha guruhlash (cache uchun)
        regions = {}
        for biz in businesses:
            region = biz.get("region_name_uz") or "Noma'lum"
            if region not in regions:
                regions[region] = []
            regions[region].append(biz)

        self._biz_regions = sorted(regions.keys())
        self._biz_regions_data = regions
        self._biz_all = businesses

        # Pagination
        per_page = 10
        total = len(businesses)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))

        start = page * per_page
        end = min(start + per_page, total)
        page_businesses = businesses[start:end]

        # Xabar tuzish - bizneslar ma'lumotlari bilan
        text = f"👥 <b>BIZNESLAR</b> ({total} ta)\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📄 Sahifa {page + 1}/{total_pages}\n\n"

        for i, biz in enumerate(page_businesses, start + 1):
            title = biz.get("title", "Noma'lum")
            phone = biz.get("phone_number", "")
            owner_first = biz.get("owner_first_name") or ""
            owner_last = biz.get("owner_last_name") or ""
            owner = f"{owner_first} {owner_last}".strip()
            region = biz.get("region_name_uz") or ""
            district = biz.get("district_name_uz") or ""
            address = biz.get("address") or ""
            group_id = self._business_groups.get(str(biz.get("id", 0)), "")
            mark = " ✅" if group_id else ""

            text += f"<b>{i}. {title}</b>{mark}\n"
            if owner:
                text += f"   👤 {owner}"
                if phone:
                    text += f" | 📱 {phone}"
                text += "\n"
            elif phone:
                text += f"   📱 {phone}\n"
            # Manzil (address) yoki viloyat/tuman
            if address:
                text += f"   📍 {address}\n"
            elif region or district:
                loc = f"{region}"
                if district:
                    loc += f", {district}"
                text += f"   📍 {loc}\n"
            text += "\n"

        # Keyboard
        keyboard_rows = []

        # Raqam tugmalari (5 tadan 2 qatorga)
        num_row = []
        for i, biz in enumerate(page_businesses, start + 1):
            biz_id = biz.get("id", 0)
            num_row.append({"text": str(i), "callback_data": f"{CALLBACK_BIZ_ITEM}{biz_id}"})
            if len(num_row) == 5:
                keyboard_rows.append(num_row)
                num_row = []
        if num_row:
            keyboard_rows.append(num_row)

        # Pagination tugmalari
        nav_row = []
        if page > 0:
            nav_row.append({"text": "◀️ Oldingi", "callback_data": f"{CALLBACK_BIZ_PAGE}{page - 1}"})
        if page < total_pages - 1:
            nav_row.append({"text": "Keyingi ▶️", "callback_data": f"{CALLBACK_BIZ_PAGE}{page + 1}"})
        if nav_row:
            keyboard_rows.append(nav_row)

        # Viloyat tugmalari (2 tadan)
        keyboard_rows.append([{"text": "━━ Viloyatlar ━━", "callback_data": "noop"}])
        region_row = []
        for idx, region_name in enumerate(self._biz_regions):
            count = len(regions[region_name])
            btn_text = f"🏙 {region_name} ({count})"
            region_row.append({"text": btn_text, "callback_data": f"{CALLBACK_BIZ_REGION}{idx}"})
            if len(region_row) == 2:
                keyboard_rows.append(region_row)
                region_row = []
        if region_row:
            keyboard_rows.append(region_row)

        # Pastki tugmalar
        keyboard_rows.append([
            {"text": "🔄 Yangilash", "callback_data": CALLBACK_BIZ_REFRESH},
            {"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}
        ])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _show_region_districts(self, message_id: int, chat_id: str, region_idx: int, page: int = 0):
        """Tanlangan viloyatdagi bizneslar (pagination) + tuman tugmalari"""
        if not hasattr(self, '_biz_regions') or region_idx >= len(self._biz_regions):
            await self._show_businesses(message_id, chat_id)
            return

        region_name = self._biz_regions[region_idx]
        region_businesses = self._biz_regions_data.get(region_name, [])

        # Tuman bo'yicha guruhlash
        districts = {}
        for biz in region_businesses:
            district = biz.get("district_name_uz") or "Noma'lum"
            if district not in districts:
                districts[district] = []
            districts[district].append(biz)

        # Cache tumanlar
        self._biz_current_districts = sorted(districts.keys())
        self._biz_current_districts_data = districts
        self._biz_current_region_idx = region_idx

        # Pagination
        per_page = 10
        total = len(region_businesses)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))

        start = page * per_page
        end = min(start + per_page, total)
        page_businesses = region_businesses[start:end]

        # Xabar tuzish
        text = f"🏙 <b>{region_name}</b> ({total} ta)\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📄 Sahifa {page + 1}/{total_pages}\n\n"

        # Biznes ro'yxati matnda - egasi, telefoni va manzili bilan
        for i, biz in enumerate(page_businesses, start + 1):
            title = biz.get("title", "Noma'lum")
            biz_id = biz.get("id", 0)
            phone = biz.get("phone_number", "")
            owner_first = biz.get("owner_first_name") or ""
            owner_last = biz.get("owner_last_name") or ""
            owner = f"{owner_first} {owner_last}".strip()
            address = biz.get("address", "")
            district = biz.get("district_name_uz") or ""
            group_id = self._business_groups.get(str(biz_id), "")
            mark = " ✅" if group_id else ""

            text += f"<b>{i}. {title}</b>{mark}\n"
            if owner:
                text += f"   👤 {owner}"
                if phone:
                    text += f" | 📱 {phone}"
                text += "\n"
            elif phone:
                text += f"   📱 {phone}\n"
            # Manzil yoki tuman
            if address:
                text += f"   📍 {address}\n"
            elif district:
                text += f"   📍 {district}\n"

        # Keyboard
        keyboard_rows = []

        # Biznes raqam tugmalari (5 tadan qatorga)
        num_row = []
        for i, biz in enumerate(page_businesses, start + 1):
            biz_id = biz.get("id", 0)
            num_row.append({"text": str(i), "callback_data": f"{CALLBACK_BIZ_ITEM}{biz_id}"})
            if len(num_row) == 5:
                keyboard_rows.append(num_row)
                num_row = []
        if num_row:
            keyboard_rows.append(num_row)

        # Pagination tugmalari
        nav_row = []
        if page > 0:
            nav_row.append({"text": "◀️ Oldingi", "callback_data": f"{CALLBACK_BIZ_REG_PAGE}{region_idx}_{page - 1}"})
        if page < total_pages - 1:
            nav_row.append({"text": "Keyingi ▶️", "callback_data": f"{CALLBACK_BIZ_REG_PAGE}{region_idx}_{page + 1}"})
        if nav_row:
            keyboard_rows.append(nav_row)

        # Tuman tugmalari (2 tadan qatorga)
        keyboard_rows.append([{"text": "━━ Tumanlar ━━", "callback_data": "noop"}])
        dist_row = []
        for idx, dist_name in enumerate(self._biz_current_districts):
            count = len(districts[dist_name])
            btn_text = f"📍 {dist_name} ({count})"
            dist_row.append({"text": btn_text, "callback_data": f"{CALLBACK_BIZ_DISTRICT}{region_idx}_{idx}"})
            if len(dist_row) == 2:
                keyboard_rows.append(dist_row)
                dist_row = []
        if dist_row:
            keyboard_rows.append(dist_row)

        # Pastki tugmalar
        keyboard_rows.append([
            {"text": "◀️ Viloyatlar", "callback_data": CALLBACK_BIZ_BACK}
        ])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _show_district_businesses(self, message_id: int, chat_id: str, region_idx: int, district_idx: int):
        """Tanlangan tumandagi bizneslarni ko'rsatish"""
        if not hasattr(self, '_biz_current_districts') or district_idx >= len(self._biz_current_districts):
            await self._show_region_districts(message_id, chat_id, region_idx)
            return

        district_name = self._biz_current_districts[district_idx]
        district_businesses = self._biz_current_districts_data.get(district_name, [])
        region_name = self._biz_regions[region_idx] if region_idx < len(self._biz_regions) else ""

        # Xabar tuzish
        text = f"📍 <b>{district_name}</b> ({len(district_businesses)} ta)\n"
        text += f"🏙 {region_name}\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        # Biznes ro'yxati matnda - egasi, telefoni va manzili bilan
        for i, biz in enumerate(district_businesses, 1):
            title = biz.get("title", "Noma'lum")
            biz_id = biz.get("id", 0)
            phone = biz.get("phone_number", "")
            owner_first = biz.get("owner_first_name") or ""
            owner_last = biz.get("owner_last_name") or ""
            owner = f"{owner_first} {owner_last}".strip()
            address = biz.get("address") or ""
            biz_region = biz.get("region_name_uz") or ""
            biz_district = biz.get("district_name_uz") or ""
            group_id = self._business_groups.get(str(biz_id), "")
            mark = " ✅" if group_id else ""

            text += f"<b>{i}. {title}</b>{mark}\n"
            if owner:
                text += f"   👤 {owner}"
                if phone:
                    text += f" | 📱 {phone}"
                text += "\n"
            elif phone:
                text += f"   📱 {phone}\n"
            # Manzil: address yoki viloyat+tuman
            if address:
                text += f"   📍 {address}\n"
            elif biz_region or biz_district:
                loc = biz_region
                if biz_district:
                    loc += f", {biz_district}" if loc else biz_district
                text += f"   📍 {loc}\n"
            text += "\n"

        # Keyboard
        keyboard_rows = []

        # Biznes raqam tugmalari (5 tadan qatorga)
        num_row = []
        for i, biz in enumerate(district_businesses, 1):
            biz_id = biz.get("id", 0)
            num_row.append({"text": str(i), "callback_data": f"{CALLBACK_BIZ_ITEM}{biz_id}"})
            if len(num_row) == 5:
                keyboard_rows.append(num_row)
                num_row = []
        if num_row:
            keyboard_rows.append(num_row)

        keyboard_rows.append([
            {"text": f"◀️ {region_name}", "callback_data": f"{CALLBACK_BIZ_DIST_BACK}{region_idx}"}
        ])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _show_business_detail(self, message_id: int, chat_id: str, biz_id: int):
        """Biznes tafsilotlarini ko'rsatish"""
        # Awaiting state ni tozalash (agar bekor qilish bosilgan bo'lsa)
        self._awaiting_group_input.pop(chat_id, None)
        self._awaiting_message_id.pop(chat_id, None)

        # Admin bo'lmagan foydalanuvchilar faqat o'z biznesini ko'ra oladi
        if not self._is_admin(chat_id):
            user_biz_id = self._get_user_business_id(chat_id)
            if user_biz_id != biz_id:
                await self.telegram.edit_message(
                    message_id=message_id,
                    text="🚫 Bu biznesga kirishga ruxsat yo'q",
                    chat_id=chat_id,
                    parse_mode="HTML",
                    reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}]]}
                )
                return

        # Biznesni topish
        biz = None
        if hasattr(self, '_biz_all') and self._biz_all:
            for b in self._biz_all:
                if b.get("id") == biz_id:
                    biz = b
                    break

        if not biz and self.nonbor_service:
            businesses = await self.nonbor_service.get_businesses()
            for b in businesses:
                if b.get("id") == biz_id:
                    biz = b
                    break

        if not biz:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Biznes topilmadi",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_BIZ_BACK}]]}
            )
            return

        title = biz.get("title", "Noma'lum")
        phone = biz.get("phone_number", "")
        region = biz.get("region_name_uz") or "Noma'lum"
        district = biz.get("district_name_uz") or "Noma'lum"
        address = biz.get("address") or "Ko'rsatilmagan"
        owner_first = biz.get("owner_first_name") or ""
        owner_last = biz.get("owner_last_name") or ""
        owner = f"{owner_first} {owner_last}".strip() or "Noma'lum"

        # Guruh ID
        group_id = self._business_groups.get(str(biz_id), "")
        # Avtoqo'ng'iroq holati
        call_enabled = self.is_call_enabled(biz_id)

        text = f"🏪 <b>{title}</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📱 <b>Telefon:</b> {phone}\n"
        text += f"👤 <b>Egasi:</b> {owner}\n"
        text += f"🏙 <b>Viloyat:</b> {region}\n"
        text += f"📍 <b>Tuman:</b> {district}\n"
        if group_id:
            text += f"👥 <b>Guruh:</b> <code>{group_id}</code>\n"
        else:
            if address and address != "Ko'rsatilmagan":
                text += f"🏠 <b>Manzil:</b> {address}\n"
        text += f"📞 <b>Avtoqo'ng'iroq:</b> {'ON' if call_enabled else 'OFF'}\n"

        keyboard_rows = []
        if group_id:
            keyboard_rows.append([{"text": "✏️ Guruhni o'zgartirish", "callback_data": f"{CALLBACK_BIZ_ADD_GROUP}{biz_id}"}])
        else:
            keyboard_rows.append([{"text": "➕ Guruh qo'shish", "callback_data": f"{CALLBACK_BIZ_ADD_GROUP}{biz_id}"}])
        # Avtoqo'ng'iroq toggle tugmasi (faqat admin)
        if self._is_admin(chat_id):
            call_btn_text = "🔕 Qo'ng'iroqni O'CHIRISH" if call_enabled else "🔔 Qo'ng'iroqni YOQISH"
            keyboard_rows.append([{"text": call_btn_text, "callback_data": f"{CALLBACK_BIZ_TOGGLE_CALL}{biz_id}"}])
        # Orqaga tugmasi faqat admin uchun
        if self._is_admin(chat_id):
            keyboard_rows.append([
                {"text": "◀️ Orqaga", "callback_data": CALLBACK_BIZ_BACK}
            ])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _show_calls_list(self, message_id: int, chat_id: str, attempts: int):
        """Berilgan urinishlar soni bo'yicha qo'ng'iroqlar ro'yxati"""
        if not self.stats_service:
            return

        calls = self.stats_service.get_period_calls_by_attempts(self._current_period, attempts)

        if not calls:
            text = f"""📞 <b>{attempts}-URINISHDA JAVOB BERILGAN QO'NG'IROQLAR</b>
━━━━━━━━━━━━━━━━━━━━

<i>Hozircha ma'lumot yo'q</i>"""
        else:
            text = f"""📞 <b>{attempts}-URINISHDA JAVOB BERILGAN QO'NG'IROQLAR</b>
━━━━━━━━━━━━━━━━━━━━

"""
            for i, call in enumerate(calls[-10:], 1):  # Oxirgi 10 ta
                time = call.timestamp.split("T")[1][:5]
                text += f"""{i}. <b>{call.seller_name}</b>
   📱 {call.phone}
   📦 {call.order_count} ta buyurtma
   ⏰ {time}

"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_CALLS_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_all_calls(self, message_id: int, chat_id: str):
        """Qo'ng'iroqlar bo'limi - filtrlar bilan"""
        if not self.stats_service:
            return

        stats = self.stats_service.get_period_stats(self._current_period)
        title = self._get_period_title()

        text = f"""📞 <b>{title} QO'NG'IROQLAR</b>
━━━━━━━━━━━━━━━━━━━━

📊 <b>Umumiy:</b> {stats.total_calls} ta qo'ng'iroq

<b>Urinishlar bo'yicha:</b>
├ 1️⃣ 1-urinishda: {stats.calls_1_attempt}
└ 2️⃣ 2-urinishda: {stats.calls_2_attempts}

<b>Natija bo'yicha:</b>
├ ✅ Javob berildi: {stats.answered_calls}
└ ❌ Javobsiz: {stats.unanswered_calls}

<i>Batafsil ko'rish uchun tugmalarni bosing:</i>"""

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": f"1️⃣ 1-urinish ({stats.calls_1_attempt})", "callback_data": CALLBACK_CALLS_1},
                    {"text": f"2️⃣ 2-urinish ({stats.calls_2_attempts})", "callback_data": CALLBACK_CALLS_2}
                ],
                [
                    {"text": f"✅ Javob ({stats.answered_calls})", "callback_data": CALLBACK_ANSWERED},
                    {"text": f"❌ Javobsiz ({stats.unanswered_calls})", "callback_data": CALLBACK_UNANSWERED}
                ],
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_answered_calls(self, message_id: int, chat_id: str):
        """Javob berilgan qo'ng'iroqlar"""
        if not self.stats_service:
            return

        stats = self.stats_service.get_period_stats(self._current_period)
        calls = [c for c in stats.call_records if c["result"] == "answered"]

        if not calls:
            text = """✅ <b>JAVOB BERILGAN QO'NG'IROQLAR</b>
━━━━━━━━━━━━━━━━━━━━

<i>Hozircha ma'lumot yo'q</i>"""
        else:
            text = f"""✅ <b>JAVOB BERILGAN QO'NG'IROQLAR</b> ({len(calls)} ta)
━━━━━━━━━━━━━━━━━━━━

"""
            for i, call in enumerate(calls[-10:], 1):
                time = call["timestamp"].split("T")[1][:5]
                text += f"""{i}. <b>{call["seller_name"]}</b>
   📱 {call["phone"]}
   📦 {call["order_count"]} ta | 🔄 {call["attempts"]} urinish
   ⏰ {time}

"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_CALLS_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_unanswered_calls(self, message_id: int, chat_id: str):
        """Javob berilmagan qo'ng'iroqlar"""
        if not self.stats_service:
            return

        stats = self.stats_service.get_period_stats(self._current_period)
        calls = [c for c in stats.call_records if c["result"] != "answered"]

        if not calls:
            text = """❌ <b>JAVOB BERILMAGAN QO'NG'IROQLAR</b>
━━━━━━━━━━━━━━━━━━━━

<i>Hozircha ma'lumot yo'q</i>"""
        else:
            text = f"""❌ <b>JAVOB BERILMAGAN QO'NG'IROQLAR</b> ({len(calls)} ta)
━━━━━━━━━━━━━━━━━━━━

"""
            for i, call in enumerate(calls[-10:], 1):
                time = call["timestamp"].split("T")[1][:5]
                status = "❌ Javobsiz" if call["result"] == "no_answer" else "🔴 " + call["result"]
                text += f"""{i}. <b>{call["seller_name"]}</b>
   📱 {call["phone"]}
   📦 {call["order_count"]} ta | 🔄 {call["attempts"]} urinish
   {status}
   ⏰ {time}

"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_CALLS_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_orders_menu(self, message_id: int, chat_id: str, page: int = None, status_filter: str = None):
        """Admin uchun buyurtmalar bo'limi - pagination va filtrlar bilan"""
        if not self.stats_service:
            return

        # State dan olish
        if page is None:
            page = self._admin_orders_page.get(chat_id, 0)
        if status_filter is None:
            status_filter = self._admin_orders_status.get(chat_id, "all")

        # State ni yangilash
        self._admin_orders_page[chat_id] = page
        self._admin_orders_status[chat_id] = status_filter

        stats = self.stats_service.get_period_stats(self._current_period)
        title = self._get_period_title()

        # Buyurtmalarni olish
        orders_list = []
        for record in stats.order_records:
            if status_filter == "all":
                orders_list.append(record)
            elif status_filter == "accepted" and record.get("result") == "accepted":
                orders_list.append(record)
            elif status_filter == "rejected" and record.get("order_status", "").startswith("CANCELLED"):
                orders_list.append(record)
            elif status_filter == "expired" and record.get("order_status") == "ACCEPT_EXPIRED":
                orders_list.append(record)
            elif status_filter == "checking" and record.get("order_status") == "CHECKING":
                orders_list.append(record)
            elif status_filter == "delivering" and record.get("order_status") == "DELIVERING":
                orders_list.append(record)
            elif status_filter == "delivered" and record.get("order_status") == "DELIVERED":
                orders_list.append(record)
            elif status_filter == "notg" and record.get("telegram_sent") == False:
                orders_list.append(record)

        # Teskari tartibda (eng yangi birinchi)
        orders_list = list(reversed(orders_list))

        # Pagination
        ITEMS_PER_PAGE = 20
        total_orders = len(orders_list)
        total_pages = max(1, (total_orders + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, total_orders)
        page_orders = orders_list[start_idx:end_idx]

        # Status nomi
        status_names = {
            "all": "Barchasi",
            "accepted": "Qabul qilingan",
            "rejected": "Bekor qilingan",
            "expired": "Muddati o'tgan",
            "checking": "Tekshirilmoqda",
            "delivering": "Yetkazilmoqda",
            "delivered": "Yetkazildi",
            "notg": "Telegram'siz"
        }
        status_name = status_names.get(status_filter, "Barchasi")

        # Matn
        text = f"📦 <b>{title} BUYURTMALAR</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"🔍 {status_name}\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        if not page_orders:
            text += "<i>Buyurtmalar topilmadi</i>\n"
        else:
            for i, order in enumerate(page_orders, start_idx + 1):
                order_num = order.get("order_number", order.get("order_id", "?"))
                result = order.get("result", "?")
                client_name = order.get("client_name", "Noma'lum")[:15]
                seller_name = order.get("seller_name", "")[:15]
                product = order.get("product_name", "")[:20]
                price = order.get("price", 0)
                timestamp = order.get("timestamp", "")
                tg_sent = order.get("telegram_sent", True)

                # Vaqtni format qilish (sana + vaqt)
                time_str = ""
                if timestamp:
                    try:
                        dt = datetime.fromisoformat(timestamp)
                        time_str = dt.strftime("%d.%m %H:%M")
                    except:
                        pass

                # Status emoji
                if result == "accepted":
                    emoji = "✅"
                elif result == "rejected":
                    emoji = "❌"
                else:
                    emoji = "⏳"

                # Telegram'siz belgisi
                tg_mark = "" if tg_sent else " 🚀"

                # Tartib raqami + status emoji + buyurtma raqami
                text += f"<b>{i}.</b> {emoji} <b>#{order_num}</b>{tg_mark}"
                if time_str:
                    text += f" ({time_str})"
                text += f"\n   👤 {client_name}"
                if seller_name:
                    text += f" | 🏪 {seller_name}"
                if product:
                    text += f"\n   📦 {product}"
                if price:
                    text += f" | 💰 {price:,}"
                text += "\n\n"

        # Footer
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📊 Jami: {total_orders} ta"
        if status_filter == "all":
            accepted = stats.accepted_orders
            rejected = stats.rejected_orders
            expired = sum(1 for r in stats.order_records if r.get("order_status") == "ACCEPT_EXPIRED")
            notg = stats.accepted_without_telegram
            text += f" (✅{accepted} | ❌{rejected} | ⏰{expired} | 🚀{notg})"
        if total_pages > 1:
            text += f"\n📄 Sahifa: {page + 1}/{total_pages}"

        # Keyboard
        keyboard_rows = []

        # 0. Davr tugmalari
        period_row1 = []
        period_row2 = []
        for p_key, p_label in [("daily", "📅 Kunlik"), ("weekly", "📆 Haftalik")]:
            btn_text = f"✓ {p_label}" if p_key == self._current_period else p_label
            period_row1.append({"text": btn_text, "callback_data": f"{CALLBACK_ADMIN_ORDERS_PERIOD}{p_key}"})
        for p_key, p_label in [("monthly", "🗓 Oylik"), ("yearly", "📊 Yillik")]:
            btn_text = f"✓ {p_label}" if p_key == self._current_period else p_label
            period_row2.append({"text": btn_text, "callback_data": f"{CALLBACK_ADMIN_ORDERS_PERIOD}{p_key}"})
        keyboard_rows.append(period_row1)
        keyboard_rows.append(period_row2)

        # 1. Status filter tugmalari (4 qator)
        statuses1 = [("📋 Barchasi", "all"), ("🔍 Tekshirilmoqda", "checking")]
        statuses2 = [("✅ Qabul", "accepted"), ("❌ Bekor", "rejected")]
        statuses3 = [("⏰ Muddati o'tgan", "expired"), ("🚚 Yetkazilmoqda", "delivering")]
        statuses4 = [("📬 Yetkazildi", "delivered"), ("🚀 Tg'siz", "notg")]

        for row_statuses in [statuses1, statuses2, statuses3, statuses4]:
            row = []
            for label, s in row_statuses:
                if s == status_filter:
                    row.append({"text": f"✓ {label}", "callback_data": f"{CALLBACK_ADMIN_ORDERS_STATUS}{s}"})
                else:
                    row.append({"text": label, "callback_data": f"{CALLBACK_ADMIN_ORDERS_STATUS}{s}"})
            keyboard_rows.append(row)

        # 2. Pagination
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append({"text": "⬅️", "callback_data": f"{CALLBACK_ADMIN_ORDERS_PAGE}{page - 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})

            nav_row.append({"text": f"{page + 1}/{total_pages}", "callback_data": "noop"})

            if page < total_pages - 1:
                nav_row.append({"text": "➡️", "callback_data": f"{CALLBACK_ADMIN_ORDERS_PAGE}{page + 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})

            keyboard_rows.append(nav_row)

        # 3. Orqaga tugmasi
        keyboard_rows.append([{"text": "◀️ Orqaga", "callback_data": CALLBACK_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _show_orders_list(self, message_id: int, chat_id: str, result: str):
        """Buyurtmalar ro'yxati"""
        if not self.stats_service:
            return

        from .stats_service import OrderResult
        order_result = OrderResult.ACCEPTED if result == "accepted" else OrderResult.REJECTED
        orders = self.stats_service.get_period_orders_by_result(self._current_period, order_result)

        title = "✅ QABUL QILINGAN" if result == "accepted" else "❌ BEKOR QILINGAN"

        if not orders:
            text = f"""<b>{title} BUYURTMALAR</b>
━━━━━━━━━━━━━━━━━━━━

<i>Hozircha ma'lumot yo'q</i>"""
        else:
            text = f"""<b>{title} BUYURTMALAR</b> ({len(orders)} ta)
━━━━━━━━━━━━━━━━━━━━

"""
            for i, order in enumerate(orders[-10:], 1):
                time = order.timestamp.split("T")[1][:5]
                price_str = f"{order.price:,.0f}".replace(",", " ") if order.price else "N/A"
                text += f"""{i}. <b>#{order.order_number}</b>
   👤 {order.client_name}
   📦 {order.product_name}
   💰 {price_str} so'm
   🏪 {order.seller_name}
   ⏰ {time}

"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_no_telegram_orders(self, message_id: int, chat_id: str):
        """Telegram yuborilmasdan qabul qilingan buyurtmalar"""
        if not self.stats_service:
            return

        orders = self.stats_service.get_period_orders_without_telegram(self._current_period)

        if not orders:
            text = """🚀 <b>TELEGRAM'SIZ QABUL QILINGAN</b>
━━━━━━━━━━━━━━━━━━━━

<i>Hozircha ma'lumot yo'q</i>

Bu buyurtmalar 3 daqiqa ichida (Telegram yuborilmasdan) qabul qilingan."""
        else:
            text = f"""🚀 <b>TELEGRAM'SIZ QABUL QILINGAN</b> ({len(orders)} ta)
━━━━━━━━━━━━━━━━━━━━

<i>Bu buyurtmalar 3 daqiqa ichida qabul qilingan</i>

"""
            for i, order in enumerate(orders[-10:], 1):
                time = order.timestamp.split("T")[1][:5]
                price_str = f"{order.price:,.0f}".replace(",", " ") if order.price else "N/A"
                text += f"""{i}. <b>#{order.order_number}</b>
   👤 {order.client_name}
   📦 {order.product_name}
   💰 {price_str} so'm
   🏪 {order.seller_name}
   📞 {order.call_attempts} marta qo'ng'iroq
   ⏰ {time}

"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    # ===== REJA BUYURTMALAR UI =====

    async def _show_planned_orders(self, message_id: int, chat_id: str, page: int = 0):
        """Rejalashtirilgan buyurtmalar ro'yxati - API dan olish"""
        if not self.nonbor_service:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Nonbor servisi ulanmagan",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_BACK}]]}
            )
            return

        # API dan barcha buyurtmalarni olish
        orders = await self.nonbor_service.get_orders()
        if orders is None:
            orders = []

        # Faqat reja buyurtmalarni filtrlash
        planned_orders = []
        for order in orders:
            is_planned = order.get("is_planned") or order.get("is_planner")
            if not is_planned:
                continue

            status = (order.get("state") or "CHECKING").upper()
            # PENDING va to'lov kutilayotganlarni o'tkazib yuborish
            skip = ["PENDING", "WAITING_PAYMENT", "PAYMENTPENDING", "PAYMENT_PENDING"]
            if status in skip:
                continue

            planned_orders.append(order)

        # Pagination
        ITEMS_PER_PAGE = 10
        total = len(planned_orders)
        total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, total)
        page_orders = planned_orders[start_idx:end_idx]

        # Matn
        text = f"📅 <b>REJA BUYURTMALAR</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📊 Jami: <b>{total}</b> ta\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        STATUS_EMOJI = {
            "CHECKING": "🟡", "ACCEPTED": "🟢", "READY": "✅",
            "DELIVERING": "🚗", "DELIVERED": "📦", "COMPLETED": "🏁",
            "CANCELLED": "❌", "CANCELLED_SELLER": "🚫", "CANCELLED_CLIENT": "❌",
            "ACCEPT_EXPIRED": "⏰",
        }

        if not page_orders:
            text += "<i>Reja buyurtmalar topilmadi</i>\n"
        else:
            for i, order in enumerate(page_orders, start_idx + 1):
                order_id = order.get("id", "?")
                status = (order.get("state") or "CHECKING").upper()
                emoji = STATUS_EMOJI.get(status, "📋")

                # Biznes nomi
                business = order.get("business") or {}
                biz_name = (business.get("title") or business.get("name") or "")[:20]

                # Mahsulot
                items = order.get("order_item") or order.get("items") or []
                product_name = ""
                if items:
                    product = items[0].get("product") or {}
                    product_name = (product.get("title") or product.get("name") or "")[:20]

                # Narx
                price = (order.get("total_price", 0) or 0) / 100
                price_str = f"{price:,.0f}".replace(",", " ") if price else ""

                # Reja vaqti
                raw_planned = order.get("planned_datetime") or order.get("planned_time") or ""
                planned_time = ""
                if raw_planned:
                    try:
                        uz_tz = timezone(timedelta(hours=5))
                        dt = datetime.fromisoformat(str(raw_planned).replace('Z', '+00:00'))
                        dt_uz = dt.astimezone(uz_tz)
                        planned_time = dt_uz.strftime("%d.%m %H:%M")
                    except:
                        planned_time = str(raw_planned)

                # Status nomi
                STATUS_NAMES = {
                    "CHECKING": "Tekshirilmoqda", "ACCEPTED": "Qabul qilindi",
                    "READY": "Tayyor", "DELIVERING": "Yetkazilmoqda",
                    "DELIVERED": "Yetkazildi", "COMPLETED": "Yakunlandi",
                    "CANCELLED": "Bekor qilindi", "CANCELLED_SELLER": "Sotuvchi bekor qildi",
                    "CANCELLED_CLIENT": "Mijoz bekor qildi", "ACCEPT_EXPIRED": "Muddati o'tdi",
                }
                status_name = STATUS_NAMES.get(status, status)

                text += f"<b>{i}.</b> {emoji} <b>#{order_id}</b>"
                if planned_time:
                    text += f" — 📅 {planned_time}"
                text += f"\n   📊 {status_name}"
                if biz_name:
                    text += f" | 🏪 {biz_name}"
                if product_name:
                    text += f"\n   🏷 {product_name}"
                if price_str:
                    text += f" | 💰 {price_str}"
                text += "\n\n"

        # Keyboard
        keyboard_rows = []

        # Pagination
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append({"text": "⬅️", "callback_data": f"{CALLBACK_PLANNED_PAGE}{page - 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})
            nav_row.append({"text": f"{page + 1}/{total_pages}", "callback_data": "noop"})
            if page < total_pages - 1:
                nav_row.append({"text": "➡️", "callback_data": f"{CALLBACK_PLANNED_PAGE}{page + 1}"})
            else:
                nav_row.append({"text": "·", "callback_data": "noop"})
            keyboard_rows.append(nav_row)

        keyboard_rows.append([{"text": "◀️ Orqaga", "callback_data": CALLBACK_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    # ===== XABARNOMALAR UI =====

    async def _show_notif_menu(self, message_id: int, chat_id: str):
        """Xabarnomalar asosiy menyu"""
        # Har doim fayldan qayta yuklash
        self._notifications = self._load_notifications()
        pending_count = sum(1 for n in self._notifications if n.get("status") == "pending")
        sent_count = sum(1 for n in self._notifications if n.get("status") == "sent")

        text = f"📢 <b>XABARNOMALAR</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"🕐 Rejalashtirilgan: <b>{pending_count}</b> ta\n"
        text += f"✅ Yuborilgan: <b>{sent_count}</b> ta\n\n"
        text += f"<i>Bizneslar guruhlariga rejalashtirilgan xabarnomalar yuborish</i>"

        keyboard = {
            "inline_keyboard": [
                [{"text": "➕ Yangi xabarnoma", "callback_data": CALLBACK_NOTIF_NEW}],
                [{"text": f"📋 Ro'yxat ({pending_count + sent_count})", "callback_data": CALLBACK_NOTIF_LIST}],
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_MENU_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_notif_target_type(self, message_id: int, chat_id: str):
        """Target turini tanlash"""
        self._notif_draft[chat_id] = {}
        self._save_notif_drafts()

        text = "📢 <b>YANGI XABARNOMA</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        text += "Kimga yuborilsin?\n\n"
        text += "🏙 <b>Hudud</b> - bir viloyatdagi barcha bizneslar\n"
        text += "🏘 <b>Tuman</b> - bir tumandagi bizneslar\n"
        text += "🏪 <b>Bizneslar</b> - tanlangan bizneslar"

        keyboard = {
            "inline_keyboard": [
                [{"text": "🏙 Hudud bo'yicha", "callback_data": CALLBACK_NOTIF_TARGET_REGION}],
                [{"text": "🏘 Tuman bo'yicha", "callback_data": CALLBACK_NOTIF_TARGET_DISTRICT}],
                [{"text": "🏪 Bizneslar", "callback_data": CALLBACK_NOTIF_TARGET_BIZ}],
                [{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _show_notif_regions(self, message_id: int, chat_id: str, for_district: bool = False):
        """Viloyatlar ro'yxati (xabarnoma uchun)"""
        if not self.nonbor_service:
            return

        businesses = await self.nonbor_service.get_businesses()
        if not businesses:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Bizneslar topilmadi",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]]}
            )
            return

        regions = {}
        for biz in businesses:
            region = biz.get("region_name_uz") or "Noma'lum"
            if region not in regions:
                regions[region] = []
            regions[region].append(biz)

        self._notif_regions = sorted(regions.keys())
        self._notif_regions_data = regions

        action = "tuman tanlash" if for_district else "xabarnoma yuborish"
        text = f"🏙 <b>VILOYAT TANLANG</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"<i>{action.capitalize()} uchun viloyatni tanlang:</i>"

        keyboard_rows = []
        for idx, region_name in enumerate(self._notif_regions):
            count = len(regions[region_name])
            grp_count = sum(1 for b in regions[region_name] if str(b.get("id", 0)) in self._business_groups)
            if for_district:
                callback = f"{CALLBACK_NOTIF_SEL_REGION}d{idx}"
            else:
                callback = f"{CALLBACK_NOTIF_SEL_REGION}{idx}"
            keyboard_rows.append([{
                "text": f"🏙 {region_name} ({grp_count}/{count})",
                "callback_data": callback
            }])

        keyboard_rows.append([{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _handle_notif_region_select(self, message_id: int, chat_id: str, region_idx_or_str):
        """Viloyat tanlanganda"""
        idx_str = str(region_idx_or_str)

        if idx_str.startswith("d"):
            region_idx = int(idx_str[1:])
            await self._show_notif_districts(message_id, chat_id, region_idx)
            return

        region_idx = int(idx_str)
        if not hasattr(self, '_notif_regions') or region_idx >= len(self._notif_regions):
            await self._show_notif_menu(message_id, chat_id)
            return

        region_name = self._notif_regions[region_idx]
        region_businesses = self._notif_regions_data.get(region_name, [])
        target_ids = [b.get("id") for b in region_businesses if str(b.get("id", 0)) in self._business_groups]

        self._notif_draft[chat_id] = {
            "target_type": "region",
            "target_ids": target_ids,
            "target_name": region_name
        }
        self._save_notif_drafts()

        if not target_ids:
            await self.telegram.edit_message(
                message_id=message_id,
                text=f"❌ <b>{region_name}</b> da guruh ulangan biznes yo'q!",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]]}
            )
            return

        await self._ask_notif_text(message_id, chat_id)

    async def _show_notif_districts(self, message_id: int, chat_id: str, region_idx: int):
        """Viloyat ichidagi tumanlar ro'yxati"""
        if not hasattr(self, '_notif_regions') or region_idx >= len(self._notif_regions):
            await self._show_notif_menu(message_id, chat_id)
            return

        region_name = self._notif_regions[region_idx]
        region_businesses = self._notif_regions_data.get(region_name, [])

        districts = {}
        for biz in region_businesses:
            district = biz.get("district_name_uz") or "Noma'lum"
            if district not in districts:
                districts[district] = []
            districts[district].append(biz)

        self._notif_districts = sorted(districts.keys())
        self._notif_districts_data = districts
        self._notif_district_region_idx = region_idx

        text = f"🏘 <b>{region_name} - TUMANLAR</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += "<i>Tuman tanlang:</i>"

        keyboard_rows = []
        for d_idx, district_name in enumerate(self._notif_districts):
            count = len(districts[district_name])
            grp_count = sum(1 for b in districts[district_name] if str(b.get("id", 0)) in self._business_groups)
            keyboard_rows.append([{
                "text": f"🏘 {district_name} ({grp_count}/{count})",
                "callback_data": f"{CALLBACK_NOTIF_SEL_DISTRICT}{region_idx}_{d_idx}"
            }])

        keyboard_rows.append([{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _handle_notif_district_select(self, message_id: int, chat_id: str, region_idx: int, district_idx: int):
        """Tuman tanlanganda"""
        if not hasattr(self, '_notif_districts') or district_idx >= len(self._notif_districts):
            await self._show_notif_menu(message_id, chat_id)
            return

        region_name = self._notif_regions[region_idx]
        district_name = self._notif_districts[district_idx]
        district_businesses = self._notif_districts_data.get(district_name, [])
        target_ids = [b.get("id") for b in district_businesses if str(b.get("id", 0)) in self._business_groups]

        self._notif_draft[chat_id] = {
            "target_type": "district",
            "target_ids": target_ids,
            "target_name": f"{region_name}, {district_name}"
        }
        self._save_notif_drafts()

        if not target_ids:
            await self.telegram.edit_message(
                message_id=message_id,
                text=f"❌ <b>{district_name}</b> da guruh ulangan biznes yo'q!",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]]}
            )
            return

        await self._ask_notif_text(message_id, chat_id)

    async def _show_notif_biz_select(self, message_id: int, chat_id: str, page: int = 0, warning: str = ""):
        """Bizneslar tanlash (faqat guruh ulanganlar)"""
        if not self.nonbor_service:
            return

        businesses = await self.nonbor_service.get_businesses()
        biz_with_groups = [b for b in businesses if str(b.get("id", 0)) in self._business_groups]

        if not biz_with_groups:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Guruh ulangan biznes yo'q!",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]]}
            )
            return

        draft = self._notif_draft.get(chat_id, {})
        selected_ids = draft.get("target_ids", [])

        per_page = 10
        total = len(biz_with_groups)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        end = min(start + per_page, total)
        page_biz = biz_with_groups[start:end]

        text = f"🏪 <b>BIZNES TANLANG</b> ({total} ta)\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        if warning:
            text += f"\n⚠️ <b>{warning}</b>\n\n"
        text += f"Tanlangan: <b>{len(selected_ids)}</b> ta\n"
        text += f"📄 Sahifa {page + 1}/{total_pages}\n\n"

        for biz in page_biz:
            biz_id = biz.get("id", 0)
            title = biz.get("title", "Noma'lum")
            check = "✅" if biz_id in selected_ids else "⬜"
            text += f"{check} {title}\n"

        keyboard_rows = []
        row = []
        for biz in page_biz:
            biz_id = biz.get("id", 0)
            title = biz.get("title", "Noma'lum")
            check = "✅" if biz_id in selected_ids else "⬜"
            short_name = title[:12] if len(title) > 12 else title
            row.append({"text": f"{check} {short_name}", "callback_data": f"{CALLBACK_NOTIF_SEL_BIZ}{biz_id}"})
            if len(row) == 2:
                keyboard_rows.append(row)
                row = []
        if row:
            keyboard_rows.append(row)

        nav_row = []
        if page > 0:
            nav_row.append({"text": "◀️", "callback_data": f"{CALLBACK_NOTIF_SEL_BIZ_PAGE}{page - 1}"})
        if page < total_pages - 1:
            nav_row.append({"text": "▶️", "callback_data": f"{CALLBACK_NOTIF_SEL_BIZ_PAGE}{page + 1}"})
        if nav_row:
            keyboard_rows.append(nav_row)

        bottom = []
        if selected_ids:
            bottom.append({"text": f"✅ Tayyor ({len(selected_ids)})", "callback_data": CALLBACK_NOTIF_DONE_BIZ})
        bottom.append({"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK})
        keyboard_rows.append(bottom)

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _handle_notif_biz_toggle(self, message_id: int, chat_id: str, biz_id: int):
        """Biznes tanlash/bekor qilish"""
        draft = self._notif_draft.get(chat_id, {})
        selected_ids = draft.get("target_ids", [])
        selected_names = draft.get("target_names", [])

        if biz_id in selected_ids:
            idx = selected_ids.index(biz_id)
            selected_ids.pop(idx)
            if idx < len(selected_names):
                selected_names.pop(idx)
        else:
            selected_ids.append(biz_id)
            if self.nonbor_service:
                businesses = await self.nonbor_service.get_businesses()
                for b in businesses:
                    if b.get("id") == biz_id:
                        selected_names.append(b.get("title", f"#{biz_id}"))
                        break

        draft["target_ids"] = selected_ids
        draft["target_names"] = selected_names
        self._notif_draft[chat_id] = draft
        self._save_notif_drafts()
        await self._show_notif_biz_select(message_id, chat_id)

    async def _ask_notif_text(self, message_id: int, chat_id: str):
        """Xabarnoma matni so'rash"""
        draft = self._notif_draft.get(chat_id, {})
        target_name = draft.get("target_name", "")
        if draft.get("target_type") == "businesses":
            names = draft.get("target_names", [])
            target_name = ", ".join(names[:5])
            if len(names) > 5:
                target_name += f" (+{len(names) - 5})"

        target_type_label = {
            "region": "🏙 Hudud",
            "district": "🏘 Tuman",
            "businesses": "🏪 Bizneslar"
        }.get(draft.get("target_type", ""), "")

        text = f"📢 <b>XABARNOMA MATNI</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"{target_type_label}: <b>{target_name}</b>\n"
        text += f"Guruhlar: <b>{len(draft.get('target_ids', []))}</b> ta\n\n"
        text += f"Xabarnoma matnini yozing:"

        self._awaiting_notif_text[chat_id] = message_id

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [[{"text": "❌ Bekor qilish", "callback_data": CALLBACK_NOTIF_CANCEL}]]}
        )

    async def _ask_notif_datetime(self, chat_id: str):
        """Kalendar ko'rsatish - kun tanlash (yangi xabar)"""
        uz_tz = timezone(timedelta(hours=5))
        now = datetime.now(uz_tz)
        cal_msg_id = await self.telegram.send_message(
            text="📅 <b>Kunni tanlang:</b>",
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=self._build_calendar(now.year, now.month)
        )
        # Kalendar message_id ni saqlash - keyingi qadamlarda edit qilish uchun
        draft = self._notif_draft.get(chat_id, {})
        draft["_cal_msg_id"] = cal_msg_id
        self._notif_draft[chat_id] = draft
        self._save_notif_drafts()

    def _build_calendar(self, year: int, month: int) -> dict:
        """Inline kalendar yaratish"""
        import calendar
        uz_tz = timezone(timedelta(hours=5))
        now = datetime.now(uz_tz)
        today = now.date()

        month_names = {
            1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
            5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
            9: "Sentyabr", 10: "Oktyabr", 11: "Noyabr", 12: "Dekabr"
        }

        rows = []
        # Oy nomi va navigatsiya
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1

        nav_row = [
            {"text": "◀️", "callback_data": f"{CALLBACK_NOTIF_CAL_MONTH}{prev_year}_{prev_month}"},
            {"text": f"📅 {month_names[month]} {year}", "callback_data": "noop"},
            {"text": "▶️", "callback_data": f"{CALLBACK_NOTIF_CAL_MONTH}{next_year}_{next_month}"}
        ]
        rows.append(nav_row)

        # Hafta kunlari
        rows.append([
            {"text": d, "callback_data": "noop"}
            for d in ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
        ])

        # Kunlar
        cal = calendar.monthcalendar(year, month)
        for week in cal:
            week_row = []
            for day in week:
                if day == 0:
                    week_row.append({"text": " ", "callback_data": "noop"})
                else:
                    from datetime import date
                    day_date = date(year, month, day)
                    if day_date < today:
                        # O'tgan kunlar - bosib bo'lmaydi
                        week_row.append({"text": f"·{day}·", "callback_data": "noop"})
                    elif day_date == today:
                        week_row.append({"text": f"[{day}]", "callback_data": f"{CALLBACK_NOTIF_CAL_DAY}{year}_{month}_{day}"})
                    else:
                        week_row.append({"text": str(day), "callback_data": f"{CALLBACK_NOTIF_CAL_DAY}{year}_{month}_{day}"})
            rows.append(week_row)

        rows.append([{"text": "❌ Bekor qilish", "callback_data": CALLBACK_NOTIF_CANCEL}])

        return {"inline_keyboard": rows}

    async def _show_notif_calendar(self, message_id: int, chat_id: str, year: int, month: int):
        """Kalendar oyini o'zgartirish"""
        await self.telegram.edit_message(
            message_id=message_id,
            text="📅 <b>Kunni tanlang:</b>",
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=self._build_calendar(year, month)
        )

    async def _handle_notif_day_select(self, message_id: int, chat_id: str, year: int, month: int, day: int):
        """Kun tanlanganda - soat tanlash"""
        draft = self._notif_draft.get(chat_id, {})
        draft["_cal_year"] = year
        draft["_cal_month"] = month
        draft["_cal_day"] = day
        self._notif_draft[chat_id] = draft
        self._save_notif_drafts()

        text = f"🕐 <b>Soatni tanlang:</b>\n"
        text += f"📅 {day:02d}.{month:02d}.{year}"

        rows = []
        # Soatlar - 3 qatorga
        for start in range(6, 24, 6):
            row = []
            for h in range(start, min(start + 6, 24)):
                row.append({"text": f"{h:02d}:00", "callback_data": f"{CALLBACK_NOTIF_CAL_HOUR}{h}"})
            rows.append(row)

        rows.append([{"text": "◀️ Kunni o'zgartirish", "callback_data": f"{CALLBACK_NOTIF_CAL_MONTH}{year}_{month}"}])
        rows.append([{"text": "❌ Bekor qilish", "callback_data": CALLBACK_NOTIF_CANCEL}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": rows}
        )

    async def _handle_notif_hour_select(self, message_id: int, chat_id: str, hour: int):
        """Soat tanlanganda - daqiqa tanlash"""
        draft = self._notif_draft.get(chat_id, {})
        draft["_cal_hour"] = hour
        self._notif_draft[chat_id] = draft
        self._save_notif_drafts()

        day = draft.get("_cal_day", 1)
        month = draft.get("_cal_month", 1)
        year = draft.get("_cal_year", 2026)

        text = f"🕐 <b>Daqiqani tanlang:</b>\n"
        text += f"📅 {day:02d}.{month:02d}.{year} {hour:02d}:??"

        rows = []
        row = []
        for m in [0, 10, 15, 20, 30, 45]:
            row.append({"text": f"{hour:02d}:{m:02d}", "callback_data": f"{CALLBACK_NOTIF_CAL_MIN}{m}"})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        rows.append([{"text": "◀️ Soatni o'zgartirish", "callback_data": f"{CALLBACK_NOTIF_CAL_DAY}{year}_{month}_{day}"}])
        rows.append([{"text": "❌ Bekor qilish", "callback_data": CALLBACK_NOTIF_CANCEL}])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": rows}
        )

    async def _handle_notif_min_select(self, message_id: int, chat_id: str, minute: int):
        """Daqiqa tanlanganda - vaqtni saqlash va tasdiqlashga o'tish"""
        draft = self._notif_draft.get(chat_id, {})
        year = draft.get("_cal_year", 2026)
        month = draft.get("_cal_month", 1)
        day = draft.get("_cal_day", 1)
        hour = draft.get("_cal_hour", 0)

        uz_tz = timezone(timedelta(hours=5))
        dt = datetime(year, month, day, hour, minute, tzinfo=uz_tz)

        now = datetime.now(uz_tz)
        if dt <= now:
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Bu vaqt o'tib ketgan! Boshqa kun yoki soat tanlang.",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "📅 Qayta tanlash", "callback_data": f"{CALLBACK_NOTIF_CAL_MONTH}{year}_{month}"}],
                    [{"text": "❌ Bekor qilish", "callback_data": CALLBACK_NOTIF_CANCEL}]
                ]}
            )
            return

        # Vaqtinchalik kalendardan tozalash
        for key in ["_cal_year", "_cal_month", "_cal_day", "_cal_hour"]:
            draft.pop(key, None)

        draft["send_at"] = dt.isoformat()
        self._notif_draft[chat_id] = draft
        self._save_notif_drafts()
        await self._show_notif_confirm(chat_id)

    async def _show_notif_confirm(self, chat_id: str):
        """Xabarnomani tasdiqlash - kalendar xabarini tahrirlash"""
        draft = self._notif_draft.get(chat_id, {})
        cal_msg_id = draft.get("_cal_msg_id")

        target_type_label = {
            "region": "🏙 Hudud",
            "district": "🏘 Tuman",
            "businesses": "🏪 Bizneslar"
        }.get(draft.get("target_type", ""), "")

        target_name = draft.get("target_name", "")
        if draft.get("target_type") == "businesses":
            names = draft.get("target_names", [])
            target_name = ", ".join(names[:5])
            if len(names) > 5:
                target_name += f" (+{len(names) - 5})"

        send_at = draft.get("send_at", "")
        try:
            dt = datetime.fromisoformat(send_at)
            send_at_str = dt.strftime("%d.%m.%Y %H:%M")
        except:
            send_at_str = send_at

        notif_text = draft.get("text", "")
        preview = notif_text[:200] + "..." if len(notif_text) > 200 else notif_text

        text = f"📢 <b>TASDIQLASH</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"{target_type_label}: <b>{target_name}</b>\n"
        text += f"📬 Guruhlar: <b>{len(draft.get('target_ids', []))}</b> ta\n"
        text += f"📅 Yuborilish: <b>{send_at_str}</b>\n\n"
        text += f"📝 <b>Matn:</b>\n{preview}"

        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ Tasdiqlash", "callback_data": CALLBACK_NOTIF_CONFIRM}],
                [{"text": "❌ Bekor qilish", "callback_data": CALLBACK_NOTIF_CANCEL}]
            ]
        }

        if cal_msg_id:
            await self.telegram.edit_message(
                message_id=cal_msg_id,
                text=text,
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await self.telegram.send_message(
                text=text,
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup=keyboard
            )

    async def _save_notif_draft(self, message_id: int, chat_id: str):
        """Xabarnomani saqlash"""
        draft = self._notif_draft.pop(chat_id, {})
        self._save_notif_drafts()
        if not draft or not draft.get("text") or not draft.get("send_at"):
            await self.telegram.edit_message(
                message_id=message_id,
                text="❌ Xabarnoma yaratilmadi - ma'lumot yetarli emas",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]]}
            )
            return

        notif = {
            "id": str(uuid.uuid4())[:8],
            "text": draft["text"],
            "send_at": draft["send_at"],
            "target_type": draft.get("target_type", ""),
            "target_ids": draft.get("target_ids", []),
            "target_name": draft.get("target_name", ""),
            "status": "pending",
            "created_at": datetime.now(timezone(timedelta(hours=5))).isoformat(),
            "created_by": chat_id
        }

        if draft.get("target_type") == "businesses":
            names = draft.get("target_names", [])
            notif["target_name"] = ", ".join(names[:5])
            if len(names) > 5:
                notif["target_name"] += f" (+{len(names) - 5})"

        self._notifications.append(notif)
        self._save_notifications()

        try:
            dt = datetime.fromisoformat(notif["send_at"])
            send_at_str = dt.strftime("%d.%m.%Y %H:%M")
        except:
            send_at_str = notif["send_at"]

        text = f"✅ <b>XABARNOMA SAQLANDI!</b>\n\n"
        text += f"📅 Yuborilish: <b>{send_at_str}</b>\n"
        text += f"📬 Guruhlar: <b>{len(notif['target_ids'])}</b> ta\n"
        text += f"📝 Matn: {notif['text'][:100]}..."

        logger.info(f"Xabarnoma yaratildi: id={notif['id']}, send_at={send_at_str}, targets={len(notif['target_ids'])}")

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [[{"text": "📢 Xabarnomalar", "callback_data": CALLBACK_MENU_NOTIF}]]}
        )

    async def _show_notif_list(self, message_id: int, chat_id: str, page: int = 0):
        """Xabarnomalar ro'yxati - muddatlari bilan"""
        # Har doim fayldan qayta yuklash
        self._notifications = self._load_notifications()
        if not self._notifications:
            await self.telegram.edit_message(
                message_id=message_id,
                text="📋 Hozircha xabarnomalar yo'q",
                chat_id=chat_id,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "➕ Yangi xabarnoma", "callback_data": CALLBACK_NOTIF_NEW}],
                    [{"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}]
                ]}
            )
            return

        sorted_notifs = sorted(
            self._notifications,
            key=lambda n: (0 if n.get("status") == "pending" else 1, n.get("send_at", ""))
        )

        per_page = 5
        total = len(sorted_notifs)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        end = min(start + per_page, total)
        page_notifs = sorted_notifs[start:end]

        text = f"📋 <b>XABARNOMALAR</b> ({total} ta)\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📄 Sahifa {page + 1}/{total_pages}\n\n"

        for n in page_notifs:
            status_icon = "🕐" if n.get("status") == "pending" else "✅"
            try:
                dt = datetime.fromisoformat(n["send_at"])
                send_at_str = dt.strftime("%d.%m.%Y %H:%M")
            except:
                send_at_str = n.get("send_at", "?")

            target_type_icon = {"region": "🏙", "district": "🏘", "businesses": "🏪"}.get(n.get("target_type", ""), "📍")
            target_name = n.get("target_name", "")
            preview = n.get("text", "")[:60]
            if len(n.get("text", "")) > 60:
                preview += "..."

            text += f"{status_icon} <b>{send_at_str}</b>\n"
            text += f"   {target_type_icon} {target_name} ({len(n.get('target_ids', []))} guruh)\n"
            text += f"   📝 {preview}\n"

            if n.get("status") == "sent":
                sent_count = n.get("sent_count", 0)
                total_count = n.get("total_count", 0)
                text += f"   ✅ Yuborildi: {sent_count}/{total_count}\n"

            text += "\n"

        keyboard_rows = []

        for n in page_notifs:
            if n.get("status") == "pending":
                try:
                    dt = datetime.fromisoformat(n["send_at"])
                    send_at_str = dt.strftime("%d.%m %H:%M")
                except:
                    send_at_str = "?"
                keyboard_rows.append([{
                    "text": f"🗑 {send_at_str} - {n.get('target_name', '')[:15]}",
                    "callback_data": f"{CALLBACK_NOTIF_DELETE}{n.get('id', '')}"
                }])

        nav_row = []
        if page > 0:
            nav_row.append({"text": "◀️", "callback_data": f"{CALLBACK_NOTIF_PAGE}{page - 1}"})
        if page < total_pages - 1:
            nav_row.append({"text": "▶️", "callback_data": f"{CALLBACK_NOTIF_PAGE}{page + 1}"})
        if nav_row:
            keyboard_rows.append(nav_row)

        keyboard_rows.append([
            {"text": "➕ Yangi", "callback_data": CALLBACK_NOTIF_NEW},
            {"text": "◀️ Orqaga", "callback_data": CALLBACK_NOTIF_BACK}
        ])

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard_rows}
        )

    async def _confirm_delete_notification(self, message_id: int, chat_id: str, notif_id: str):
        """Xabarnomani o'chirishni tasdiqlash"""
        self._notifications = self._load_notifications()
        notif = next((n for n in self._notifications if n.get("id") == notif_id), None)
        if not notif:
            await self._show_notif_list(message_id, chat_id)
            return

        send_at_str = ""
        try:
            dt = datetime.fromisoformat(notif["send_at"])
            send_at_str = dt.strftime("%d.%m.%Y %H:%M")
        except:
            send_at_str = notif.get("send_at", "?")

        target_name = notif.get("target_name", "")
        preview = notif.get("text", "")[:100]

        text = f"🗑 <b>O'CHIRISHNI TASDIQLANG</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📅 Yuborilish: <b>{send_at_str}</b>\n"
        text += f"🏪 Target: <b>{target_name}</b>\n"
        text += f"📝 Matn: {preview}\n\n"
        text += f"⚠️ <b>Rostdan o'chirmoqchimisiz?</b>"

        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ Ha, o'chirish", "callback_data": f"{CALLBACK_NOTIF_DELETE_CONFIRM}{notif_id}"}],
                [{"text": "❌ Yo'q, orqaga", "callback_data": CALLBACK_NOTIF_LIST}]
            ]
        }

        await self.telegram.edit_message(
            message_id=message_id,
            text=text,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    async def _delete_notification(self, message_id: int, chat_id: str, notif_id: str):
        """Xabarnomani o'chirish"""
        self._notifications = self._load_notifications()
        self._notifications = [n for n in self._notifications if n.get("id") != notif_id]
        self._save_notifications()
        logger.info(f"Xabarnoma o'chirildi: id={notif_id}")
        await self._show_notif_list(message_id, chat_id)
