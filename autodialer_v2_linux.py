#!/usr/bin/env python3
"""
Avtomatik Qo'ng'iroq Tizimi v2 - LINUX VERSIYA
- Nonbor API dan CHECKING statusidagi buyurtmalarni polling qiladi
- Sotuvchiga qo'ng'iroq qilish (Click-to-Call)
- Kiruvchi qo'ng'iroqlarni qayta ishlash

LINUX SERVER UCHUN OPTIMIZATSIYA QILINGAN
"""

import asyncio
import subprocess
import logging
import re
import json
import hashlib
import os
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from collections import OrderedDict
from typing import Optional, Dict, List, Any
from aiohttp import web
import aiohttp

# ============ SOZLAMALAR ============
# Telegram credentials - ENVIRONMENT VARIABLES dan
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

# Server sozlamalari
SARKOR_ENDPOINT = os.environ.get("SARKOR_ENDPOINT", "sarkor-endpoint")
SIP_SERVER = os.environ.get("SIP_SERVER", "well-tech.sip.uz")

# Nonbor API
NONBOR_API_URL = os.environ.get(
    "NONBOR_API_URL",
    "https://test.nonbor.uz/api/v2/telegram_bot/get-order-for-courier/"
)

# Order statuslar
ORDER_STATUS_CHECKING = "CHECKING"
ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_ACCEPTED = "ACCEPTED"
ORDER_STATUS_READY = "READY"
ORDER_STATUS_DELIVERING = "DELIVERING"
ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_CANCELLED = "CANCELLED"
ORDER_STATUS_CANCELLED_SELLER = "CANCELLED_SELLER"
ORDER_STATUS_CANCELLED_CLIENT = "CANCELLED_CLIENT"

# Qo'ng'iroq sozlamalari
WAIT_BEFORE_CALL = int(os.environ.get("WAIT_BEFORE_CALL", "90"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
TELEGRAM_ALERT_TIME = int(os.environ.get("TELEGRAM_ALERT_TIME", "150"))
POLLING_INTERVAL = int(os.environ.get("POLLING_INTERVAL", "3"))
CALL_WAIT_TIME = int(os.environ.get("CALL_WAIT_TIME", "30"))

# TTS sozlamalari - LINUX yo'llari
TTS_AUDIO_CACHE = Path(os.environ.get("TTS_AUDIO_CACHE", "/var/lib/autodialer/audio/cache"))
TTS_ASTERISK_PATH = os.environ.get("TTS_ASTERISK_PATH", "/usr/share/asterisk/sounds/custom")

# Multi-language TTS sozlamalari
TTS_VOICES = {
    "uz": "uz-UZ-MadinaNeural",      # O'zbek
    "ru": "ru-RU-SvetlanaNeural",    # Rus
    "en": "en-US-JennyNeural",       # Ingliz
}

# Xabar shablonlari (har bir til uchun)
TTS_MESSAGES = {
    "uz": {
        "single": "Assalomu alaykum, men nonbor ovozli bot xizmatiman, sizda 1 ta buyurtma bor, iltimos, buyurtmangizni tekshiring.",
        "multiple": "Assalomu alaykum, men nonbor ovozli bot xizmatiman, sizda {count} ta buyurtma bor, iltimos, buyurtmalaringizni tekshiring."
    },
    "ru": {
        "single": "Здравствуйте, это голосовой бот Nonbor. У вас есть 1 заказ, пожалуйста, проверьте ваш заказ.",
        "multiple": "Здравствуйте, это голосовой бот Nonbor. У вас {count} заказов, пожалуйста, проверьте ваши заказы."
    },
    "en": {
        "single": "Hello, this is Nonbor voice bot. You have 1 order, please check your order.",
        "multiple": "Hello, this is Nonbor voice bot. You have {count} orders, please check your orders."
    }
}

DEFAULT_LANGUAGE = "uz"  # Standart til

# Xavfsizlik sozlamalari
MAX_PROCESSED_ORDERS = 10000
RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_WINDOW = 60

# ============ LOGGING ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============ XAVFSIZLIK FUNKSIYALARI ============

def sanitize_phone(phone: str) -> Optional[str]:
    """Telefon raqamini tozalash va tekshirish"""
    if not phone:
        return None

    cleaned = re.sub(r'[^\d+]', '', str(phone))

    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned

    if not re.match(r'^\+998\d{9}$', cleaned):
        if not re.match(r'^\+\d{10,15}$', cleaned):
            logger.warning(f"Noto'g'ri telefon formati: {phone}")
            return None

    return cleaned

def sanitize_text(text: str, max_length: int = 1000) -> str:
    """Matnni tozalash"""
    if not text:
        return ""

    cleaned = re.sub(r'<[^>]+>', '', str(text))
    cleaned = cleaned.replace('&', '&amp;')
    cleaned = cleaned.replace('<', '&lt;')
    cleaned = cleaned.replace('>', '&gt;')

    return cleaned[:max_length]

def validate_order_id(order_id: Any) -> Optional[int]:
    """Order ID ni tekshirish"""
    if order_id is None:
        return None

    try:
        oid = int(order_id)
        if oid <= 0 or oid > 2147483647:
            return None
        return oid
    except (ValueError, TypeError):
        return None

def secure_hash(text: str) -> str:
    """Xavfsiz hash yaratish"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

class RateLimiter:
    """Rate limiting"""

    def __init__(self, max_requests: int = RATE_LIMIT_REQUESTS, window: int = RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window = window
        self.requests: Dict[str, List[datetime]] = {}

    def is_allowed(self, client_ip: str) -> bool:
        now = datetime.now()
        window_start = now - timedelta(seconds=self.window)

        if client_ip not in self.requests:
            self.requests[client_ip] = []

        self.requests[client_ip] = [
            req_time for req_time in self.requests[client_ip]
            if req_time > window_start
        ]

        if len(self.requests[client_ip]) >= self.max_requests:
            return False

        self.requests[client_ip].append(now)
        return True

    def cleanup(self):
        now = datetime.now()
        window_start = now - timedelta(seconds=self.window * 2)

        for client_ip in list(self.requests.keys()):
            self.requests[client_ip] = [
                req_time for req_time in self.requests[client_ip]
                if req_time > window_start
            ]
            if not self.requests[client_ip]:
                del self.requests[client_ip]

rate_limiter = RateLimiter()

def rate_limit_middleware(handler):
    @wraps(handler)
    async def wrapper(request):
        client_ip = request.remote or "unknown"

        if not rate_limiter.is_allowed(client_ip):
            logger.warning(f"Rate limit exceeded: {client_ip}")
            return web.json_response(
                {"status": "error", "message": "Too many requests"},
                status=429
            )

        return await handler(request)
    return wrapper

class LimitedDict(OrderedDict):
    """Cheklangan o'lchamli dictionary"""

    def __init__(self, max_size: int = MAX_PROCESSED_ORDERS, *args, **kwargs):
        self.max_size = max_size
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value):
        if len(self) >= self.max_size:
            self.popitem(last=False)
        super().__setitem__(key, value)

class LimitedSet(set):
    """Cheklangan o'lchamli set"""

    def __init__(self, max_size: int = MAX_PROCESSED_ORDERS, *args, **kwargs):
        self.max_size = max_size
        self._order = []
        super().__init__(*args, **kwargs)

    def add(self, item):
        if item not in self:
            if len(self) >= self.max_size:
                oldest = self._order.pop(0)
                self.discard(oldest)
            self._order.append(item)
        super().add(item)

# ============ MA'LUMOTLAR SAQLOVCHILARI ============
pending_orders: Dict[int, Dict] = LimitedDict(max_size=MAX_PROCESSED_ORDERS)
order_messages: Dict[int, int] = LimitedDict(max_size=MAX_PROCESSED_ORDERS)
seller_messages: Dict[str, int] = LimitedDict(max_size=1000)
seller_orders_data: Dict[str, Dict] = LimitedDict(max_size=1000)
order_to_seller: Dict[int, str] = LimitedDict(max_size=MAX_PROCESSED_ORDERS)
order_statuses: Dict[int, str] = LimitedDict(max_size=MAX_PROCESSED_ORDERS)
processed_orders: LimitedSet = LimitedSet(max_size=MAX_PROCESSED_ORDERS)
call_results: Dict[int, str] = LimitedDict(max_size=1000)
call_history: List[Dict] = []
active_calls: Dict[str, Dict] = {}
seller_order_groups: Dict[str, List] = {}
seller_last_call: Dict[str, datetime] = {}

# ============ HTTP SESSION ============
_http_session: Optional[aiohttp.ClientSession] = None

async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=30)
        _http_session = aiohttp.ClientSession(timeout=timeout)
    return _http_session

async def close_http_session():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()

# ============ TTS FUNKSIYALARI ============
def get_tts_cache_path(text: str) -> Path:
    """Matn uchun cache fayl yo'lini olish"""
    text_hash = secure_hash(text)[:32]
    return TTS_AUDIO_CACHE / f"{text_hash}.wav"

def get_order_message_text(count: int, language: str = None) -> str:
    """Buyurtma soni uchun xabar matni (til bo'yicha)"""
    count = max(1, min(count, 1000))
    lang = language or DEFAULT_LANGUAGE
    if lang not in TTS_MESSAGES:
        lang = DEFAULT_LANGUAGE

    messages = TTS_MESSAGES[lang]
    if count == 1:
        return messages["single"]
    else:
        return messages["multiple"].format(count=count)

async def generate_tts_audio(text: str, language: str = None) -> Optional[Path]:
    """Edge TTS orqali audio yaratish (til bo'yicha)"""
    # Til aniqlash
    lang = language or DEFAULT_LANGUAGE
    if lang not in TTS_VOICES:
        lang = DEFAULT_LANGUAGE

    # Cache key = til + matn
    cache_key = f"{lang}_{text}"
    text_hash = hashlib.md5(cache_key.encode()).hexdigest()
    cache_path = TTS_AUDIO_CACHE / f"{text_hash}.wav"

    if cache_path.exists():
        logger.debug(f"TTS cache dan olindi: {cache_path} (til: {lang})")
        return cache_path

    try:
        import edge_tts

        # Cache papkasini yaratish
        TTS_AUDIO_CACHE.mkdir(parents=True, exist_ok=True)

        voice = TTS_VOICES[lang]
        logger.info(f"TTS yaratilmoqda: til={lang}, ovoz={voice}")

        communicate = edge_tts.Communicate(text, voice)
        mp3_path = cache_path.with_suffix(".mp3")
        await communicate.save(str(mp3_path))

        if not mp3_path.exists():
            logger.error(f"MP3 fayl yaratilmadi: {mp3_path}")
            return None

        # WAV ga convert (Asterisk uchun 8kHz mono)
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', str(mp3_path),
            '-ar', '8000', '-ac', '1', '-acodec', 'pcm_s16le',
            str(cache_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await process.wait()

        mp3_path.unlink(missing_ok=True)

        if cache_path.exists():
            logger.info(f"TTS yaratildi: {cache_path}")
            return cache_path

        return None

    except ImportError:
        logger.error("edge_tts kutubxonasi o'rnatilmagan! pip install edge-tts")
        return None
    except Exception as e:
        logger.error(f"TTS xatosi: {e}")
        return None

async def generate_order_audio(count: int, language: str = None) -> Optional[Path]:
    """Buyurtma soni uchun audio yaratish (til bo'yicha)"""
    text = get_order_message_text(count, language)
    return await generate_tts_audio(text, language)

def sync_audio_to_asterisk(audio_path: Path) -> Optional[str]:
    """Audio faylni Asterisk ga ko'chirish - LINUX VERSIYA"""
    try:
        if not audio_path or not audio_path.exists():
            return None

        audio_name = re.sub(r'[^a-zA-Z0-9_-]', '', audio_path.stem)
        if not audio_name:
            audio_name = secure_hash(str(audio_path))[:16]

        source_path = str(audio_path.absolute())

        if '..' in source_path or '\0' in source_path:
            logger.error("Xavfsizlik: Noto'g'ri fayl yo'li")
            return None

        try:
            # Copy command - LINUX
            copy_result = subprocess.run(
                ['cp', source_path, f'{TTS_ASTERISK_PATH}/{audio_name}.wav'],
                capture_output=True, text=True, timeout=30
            )

            if copy_result.returncode != 0:
                logger.error(f"Audio copy xatosi: {copy_result.stderr}")
                return None

            # Sox command - LINUX
            sox_result = subprocess.run(
                ['sox',
                 f'{TTS_ASTERISK_PATH}/{audio_name}.wav',
                 '-t', 'raw', '-r', '8000', '-c', '1', '-e', 'a-law',
                 f'{TTS_ASTERISK_PATH}/{audio_name}.alaw'],
                capture_output=True, text=True, timeout=30
            )

            # Chown command - LINUX
            subprocess.run(
                ['chown', 'asterisk:asterisk',
                 f'{TTS_ASTERISK_PATH}/{audio_name}.wav',
                 f'{TTS_ASTERISK_PATH}/{audio_name}.alaw'],
                capture_output=True, text=True, timeout=10
            )

            # Chmod command - LINUX
            subprocess.run(
                ['chmod', '644',
                 f'{TTS_ASTERISK_PATH}/{audio_name}.wav',
                 f'{TTS_ASTERISK_PATH}/{audio_name}.alaw'],
                capture_output=True, text=True, timeout=10
            )

            logger.debug(f"Audio Asterisk ga ko'chirildi: {TTS_ASTERISK_PATH}/{audio_name}.alaw")
            return audio_name

        except subprocess.TimeoutExpired:
            logger.error("Audio ko'chirish timeout")
            return None

    except Exception as e:
        logger.error(f"Audio sync xatosi: {e}")
        return None

def make_call_secure(phone_number: str, order_id: Optional[int] = None) -> bool:
    """Asterisk orqali qo'ng'iroq qilish - LINUX VERSIYA"""
    try:
        phone = sanitize_phone(phone_number)
        if not phone:
            logger.error(f"Noto'g'ri telefon raqami: {phone_number}")
            return False

        endpoint = re.sub(r'[^a-zA-Z0-9_-]', '', SARKOR_ENDPOINT)
        if not endpoint:
            logger.error("Noto'g'ri SARKOR_ENDPOINT")
            return False

        asterisk_cmd = f"channel originate PJSIP/{phone}@{endpoint} extension s@autodialer-ivr"

        logger.info(f"Qo'ng'iroq qilinmoqda: {phone}, Order ID: {order_id}")

        # LINUX - to'g'ridan-to'g'ri asterisk buyrug'i
        result = subprocess.run(
            ['asterisk', '-rx', asterisk_cmd],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            logger.info(f"Qo'ng'iroq muvaffaqiyatli: {phone}")
            return True
        else:
            logger.error(f"Qo'ng'iroq xatosi: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Qo'ng'iroq timeout: {phone_number}")
        return False
    except Exception as e:
        logger.error(f"Qo'ng'iroq xatosi: {e}")
        return False

def verify_audio_in_asterisk(audio_name: str) -> bool:
    """Asterisk da audio fayl mavjudligini tekshirish - LINUX"""
    try:
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', audio_name)
        if not safe_name:
            return False

        result = subprocess.run(
            ['test', '-f', f'{TTS_ASTERISK_PATH}/{safe_name}.wav'],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            logger.info(f"Audio Asterisk da mavjud: {safe_name}")
            return True
        else:
            logger.warning(f"Audio Asterisk da topilmadi: {TTS_ASTERISK_PATH}/{safe_name}.wav")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Audio tekshirish timeout")
        return False
    except Exception as e:
        logger.error(f"Audio tekshirish xatosi: {e}")
        return False

async def prepare_audio_for_call(order_count: int, language: str = None) -> Optional[str]:
    """Qo'ng'iroq uchun audio tayyorlash (til bo'yicha)"""
    try:
        audio_path = await generate_order_audio(order_count, language)

        if not audio_path or not audio_path.exists():
            logger.error(f"Audio yaratib bo'lmadi: {order_count} ta buyurtma")
            return None

        logger.info(f"Audio tayyor: {audio_path}")

        audio_name = sync_audio_to_asterisk(audio_path)

        if not audio_name:
            logger.error("Audio Asterisk ga ko'chirib bo'lmadi")
            return None

        if not verify_audio_in_asterisk(audio_name):
            logger.error(f"Audio Asterisk da topilmadi: {audio_name}")
            return None

        logger.info(f"Audio Asterisk da tasdiqlandi: {audio_name}")
        return audio_name

    except Exception as e:
        logger.error(f"Audio tayyorlash xatosi: {e}")
        return None

async def make_call_with_count(phone_number: str, order_count: int, order_ids: Optional[List] = None, language: str = None) -> bool:
    """Dinamik audio bilan qo'ng'iroq qilish - LINUX (til bo'yicha)"""
    try:
        phone = sanitize_phone(phone_number)
        if not phone:
            logger.error(f"Noto'g'ri telefon raqami: {phone_number}")
            return False

        audio_name = await prepare_audio_for_call(order_count, language)

        if not audio_name:
            logger.error(f"QO'NG'IROQ QILINMADI - Audio mavjud emas: {order_count} ta buyurtma, {phone}")
            return False

        endpoint = re.sub(r'[^a-zA-Z0-9_-]', '', SARKOR_ENDPOINT)
        safe_audio = re.sub(r'[^a-zA-Z0-9_-]', '', audio_name)

        asterisk_cmd = f"channel originate PJSIP/{phone}@{endpoint} extension {safe_audio}@autodialer-dynamic"

        logger.info(f"Qo'ng'iroq: {phone}, {order_count} ta buyurtma, Audio: {safe_audio}")

        # LINUX - to'g'ridan-to'g'ri asterisk buyrug'i
        result = subprocess.run(
            ['asterisk', '-rx', asterisk_cmd],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            logger.info(f"Qo'ng'iroq muvaffaqiyatli: {phone}, {order_count} ta buyurtma")
            return True
        else:
            logger.error(f"Qo'ng'iroq xatosi: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Qo'ng'iroq timeout: {phone_number}")
        return False
    except Exception as e:
        logger.error(f"Qo'ng'iroq xatosi: {e}")
        return False

# ============ TELEGRAM FUNKSIYALARI ============
async def send_telegram_message_async(message: str, order_id: Optional[int] = None) -> Optional[int]:
    """Telegram orqali xabar yuborish - ASYNC"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        logger.warning("Telegram credentials yo'q")
        return None

    try:
        session = await get_http_session()
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        safe_message = message[:4096]

        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": safe_message,
            "parse_mode": "HTML"
        }

        async with session.post(url, data=data) as response:
            if response.status == 200:
                result = await response.json()
                message_id = result.get('result', {}).get('message_id')
                logger.info(f"Telegram xabari yuborildi, message_id: {message_id}")

                if order_id and message_id:
                    order_messages[order_id] = message_id

                return message_id
            else:
                text = await response.text()
                logger.error(f"Telegram xatosi: {text}")
                return None

    except Exception as e:
        logger.error(f"Telegram xatosi: {e}")
        return None

def send_telegram_message(message: str, order_id: Optional[int] = None) -> Optional[int]:
    """Sync wrapper for telegram message"""
    try:
        import requests

        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
            logger.warning("Telegram credentials yo'q")
            return None

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": message[:4096],
            "parse_mode": "HTML"
        }

        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            message_id = result.get('result', {}).get('message_id')
            logger.info(f"Telegram xabari yuborildi, message_id: {message_id}")
            if order_id and message_id:
                order_messages[order_id] = message_id
            return message_id
        else:
            logger.error(f"Telegram xatosi: {response.status_code} - {response.text}")
        return None

    except Exception as e:
        logger.error(f"Telegram xatosi: {e}")
        return None

def format_seller_orders_message(seller_orders: Dict, call_attempts: int = 0) -> str:
    """Sotuvchi buyurtmalari uchun Telegram xabar formati"""
    seller_name = sanitize_text(seller_orders.get("seller_name", "Noma'lum"))
    seller_phone = sanitize_phone(seller_orders.get("seller_phone", "")) or "Noma'lum"
    seller_address = sanitize_text(seller_orders.get("seller_address", "Noma'lum"))
    delivery_time = sanitize_text(seller_orders.get("delivery_time", ""))
    orders = seller_orders.get("orders", [])
    orders_count = len(orders)

    total_price = 0
    for o in orders:
        price = o.get("price") or o.get("narx", 0)
        if isinstance(price, str):
            price = re.sub(r'[^\d.]', '', price)
            try:
                price = float(price)
            except:
                price = 0
        total_price += price or 0

    total_price_str = f"{total_price:,.0f}".replace(",", " ") + " so'm"

    text = f"""🚨 <b>DIQQAT! {orders_count} ta buyurtma qabul qilinmadi!</b>

<b>SOTUVCHI:</b>
  Nomi: {seller_name}
  Tel: {seller_phone}
  Manzil: {seller_address}"""

    if delivery_time:
        text += f"\n  Yetkazish vaqti: {delivery_time}"

    text += "\n\n<b>━━━ BUYURTMALAR ━━━</b>\n"

    for i, order in enumerate(orders[:10], 1):
        order_number = sanitize_text(str(order.get("order_number") or order.get("lead_id", "N/A")))
        client_name = sanitize_text(order.get("client_name") or order.get("mijoz_nomi", "Noma'lum"))
        client_phone = sanitize_phone(order.get("client_phone") or order.get("mijoz_tel", "")) or "Noma'lum"
        price = order.get("price") or order.get("narx", 0)

        if isinstance(price, (int, float)) and price:
            price_str = f"{price:,.0f}".replace(",", " ") + " so'm"
        elif isinstance(price, str) and price:
            price_str = price + " so'm"
        else:
            price_str = "Noma'lum"

        text += f"""
<b>{i}. Buyurtma #{order_number}</b>
   👤 Mijoz: {client_name}
   📞 Tel: {client_phone}
   💰 Narx: {price_str}
"""
        # Barcha mahsulotlarni ko'rsatish
        products = order.get("products", [])
        if products:
            text += "   📦 Mahsulotlar:\n"
            for idx, prod in enumerate(products, 1):
                prod_name = sanitize_text(prod.get('name', 'Noma\'lum'))
                prod_qty = prod.get('quantity', 1)
                prod_price = prod.get('price', 0)
                if isinstance(prod_price, (int, float)) and prod_price:
                    prod_price_str = f"{prod_price:,.0f}".replace(",", " ")
                else:
                    prod_price_str = "0"
                text += f"      {idx}. {prod_name} x{prod_qty} ({prod_price_str} so'm)\n"
        else:
            # Eski format uchun orqaga moslik
            product_name = sanitize_text(order.get("product_name") or order.get("mahsulot", "Noma'lum"))
            quantity = order.get("quantity") or order.get("miqdor", 1)
            text += f"   📦 Mahsulot: {product_name} x{quantity}\n"

    if orders_count > 10:
        text += f"\n... va yana {orders_count - 10} ta buyurtma\n"

    text += f"""
<b>━━━━━━━━━━━━━━━━━━━━━</b>
📦 Jami: <b>{orders_count}</b> ta buyurtma
💰 Umumiy: <b>{total_price_str}</b>

❌ Buyurtmalarni qabul qilmayapti!
📞 {call_attempts} marta qo'ng'iroq qilindi.
🔴 Zudlik bilan bog'laning!

📱 <a href="https://test.nonbor.uz">Buyurtmalarni ko'rish</a>"""

    return text

def send_seller_orders_alert(seller_orders: Dict, call_attempts: int = 0) -> Optional[int]:
    """Sotuvchi buyurtmalari haqida Telegram xabar yuborish"""
    seller_phone = seller_orders.get("seller_phone", "Noma'lum")
    orders_count = len(seller_orders.get("orders", []))

    logger.info(f"Telegram xabar yuborilmoqda: Sotuvchi {seller_phone}, {orders_count} ta buyurtma")

    message = format_seller_orders_message(seller_orders, call_attempts)

    orders = seller_orders.get("orders", [])
    first_order_id = orders[0].get("lead_id") if orders else None

    result = send_telegram_message(message, order_id=first_order_id)

    if result:
        seller_messages[seller_phone] = result
        seller_orders_data[seller_phone] = {
            "seller_orders": seller_orders,
            "call_attempts": call_attempts
        }
        logger.info(f"Telegram xabar yuborildi: Sotuvchi {seller_phone}, message_id: {result}")
    else:
        logger.error(f"Telegram xabar yuborilmadi: Sotuvchi {seller_phone}")

    return result

def delete_telegram_message(order_id: int) -> bool:
    """Telegram xabarni o'chirish"""
    try:
        import requests

        message_id = order_messages.get(order_id)
        if not message_id or not TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id
        }

        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            logger.info(f"Telegram xabari o'chirildi, message_id: {message_id}")
            if order_id in order_messages:
                del order_messages[order_id]
            return True
        return False

    except Exception as e:
        logger.error(f"Telegram o'chirish xatosi: {e}")
        return False

def edit_telegram_message(message_id: int, new_text: str) -> bool:
    """Telegram xabarni tahrirlash"""
    try:
        import requests

        if not TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": new_text[:4096],
            "parse_mode": "HTML"
        }

        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            logger.info(f"Telegram xabari yangilandi, message_id: {message_id}")
            return True
        return False

    except Exception as e:
        logger.error(f"Telegram tahrirlash xatosi: {e}")
        return False

def delete_seller_telegram_message(seller_phone: str) -> bool:
    """Sotuvchi uchun yuborilgan Telegram xabarni o'chirish"""
    try:
        import requests

        message_id = seller_messages.get(seller_phone)
        if not message_id or not TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id
        }

        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            logger.info(f"Sotuvchi {seller_phone} Telegram xabari o'chirildi")
            if seller_phone in seller_messages:
                del seller_messages[seller_phone]
            return True
        return False

    except Exception as e:
        logger.error(f"Telegram o'chirish xatosi: {e}")
        return False

def update_seller_telegram_on_status_change(seller_phone: str, order_id: int, new_status: str) -> bool:
    """Buyurtma statusi o'zgarganda Telegram xabarni yangilash"""
    try:
        message_id = seller_messages.get(seller_phone)
        saved_data = seller_orders_data.get(seller_phone)

        if not saved_data:
            logger.debug(f"Sotuvchi {seller_phone} uchun buyurtmalar ma'lumoti topilmadi")
            return False

        seller_orders = saved_data.get("seller_orders", {})
        call_attempts = saved_data.get("call_attempts", 0)
        orders = seller_orders.get("orders", [])

        changed_order = None
        for o in orders:
            if o.get("lead_id") == order_id or str(o.get("order_number")) == str(order_id):
                changed_order = o
                break

        order_number = changed_order.get("order_number") if changed_order else order_id

        updated_orders = [
            o for o in orders
            if o.get("lead_id") != order_id and str(o.get("order_number")) != str(order_id)
        ]

        if message_id:
            logger.info(f"Sotuvchi {seller_phone}: Eski xabar o'chirilmoqda (message_id: {message_id})")
            delete_seller_telegram_message(seller_phone)

        if new_status == ORDER_STATUS_ACCEPTED:
            send_telegram_message(
                f"✅ <b>Buyurtma #{order_number} QABUL QILINDI!</b>\n"
                f"🏪 Sotuvchi: {seller_phone}\n"
                f"⏰ Vaqt: {datetime.now().strftime('%H:%M:%S')}"
            )
        elif new_status in [ORDER_STATUS_CANCELLED_SELLER, ORDER_STATUS_CANCELLED_CLIENT]:
            cancel_type = "Sotuvchi" if new_status == ORDER_STATUS_CANCELLED_SELLER else "Mijoz"
            send_telegram_message(
                f"❌ <b>Buyurtma #{order_number} RAD ETILDI!</b>\n"
                f"👤 {cancel_type} tomonidan\n"
                f"🏪 Sotuvchi: {seller_phone}\n"
                f"⏰ Vaqt: {datetime.now().strftime('%H:%M:%S')}"
            )

        if len(updated_orders) == 0:
            logger.info(f"Sotuvchi {seller_phone}: Barcha buyurtmalar qabul qilindi")
            if seller_phone in seller_orders_data:
                del seller_orders_data[seller_phone]
            if seller_phone in seller_messages:
                del seller_messages[seller_phone]
            return True

        logger.info(f"Sotuvchi {seller_phone}: {len(orders)} → {len(updated_orders)} ta buyurtma qoldi")

        updated_seller_orders = seller_orders.copy()
        updated_seller_orders["orders"] = updated_orders

        new_message = format_seller_orders_message(updated_seller_orders, call_attempts)
        new_message_id = send_telegram_message(new_message)

        if new_message_id:
            seller_messages[seller_phone] = new_message_id
            seller_orders_data[seller_phone] = {
                "seller_orders": updated_seller_orders,
                "call_attempts": call_attempts
            }
            logger.info(f"YANGI Telegram xabar yuborildi: {len(updated_orders)} ta buyurtma qoldi")
            return True

        return False

    except Exception as e:
        logger.error(f"Telegram yangilash xatosi: {e}")
        return False

# ============ API FUNKSIYALARI ============
async def get_checking_orders_async() -> List[Dict]:
    """Nonbor API dan CHECKING statusidagi buyurtmalarni olish - ASYNC"""
    try:
        session = await get_http_session()

        async with session.get(NONBOR_API_URL) as response:
            if response.status == 200:
                data = await response.json()

                if isinstance(data, dict) and data.get('success'):
                    orders = data.get('result', {}).get('results', [])
                elif isinstance(data, list):
                    orders = data
                else:
                    orders = []

                checking_orders = [o for o in orders if o.get('state') == ORDER_STATUS_CHECKING]
                logger.info(f"CHECKING: {len(checking_orders)} ta buyurtma topildi")
                return checking_orders
            else:
                text = await response.text()
                logger.error(f"Nonbor API xatosi: {response.status} - {text}")
                return []

    except Exception as e:
        logger.error(f"Nonbor API xatosi: {e}")
        return []

def get_checking_orders() -> List[Dict]:
    """Nonbor API dan CHECKING statusidagi buyurtmalarni olish - SYNC"""
    try:
        import requests

        response = requests.get(NONBOR_API_URL, timeout=30)

        if response.status_code == 200:
            data = response.json()

            if isinstance(data, dict) and data.get('success'):
                orders = data.get('result', {}).get('results', [])
            elif isinstance(data, list):
                orders = data
            else:
                orders = []

            checking_orders = [o for o in orders if o.get('state') == ORDER_STATUS_CHECKING]
            logger.info(f"CHECKING: {len(checking_orders)} ta buyurtma topildi")
            return checking_orders
        else:
            logger.error(f"Nonbor API xatosi: {response.status_code} - {response.text}")
            return []

    except Exception as e:
        logger.error(f"Nonbor API xatosi: {e}")
        return []

get_all_orders = get_checking_orders

def get_order_status(order_id: int) -> Optional[str]:
    """Buyurtma statusini tekshirish"""
    try:
        import requests

        oid = validate_order_id(order_id)
        if not oid:
            return None

        response = requests.get(NONBOR_API_URL, timeout=30)
        if response.status_code == 200:
            data = response.json()

            if isinstance(data, dict) and data.get('success'):
                orders = data.get('result', {}).get('results', [])
            elif isinstance(data, list):
                orders = data
            else:
                return None

            for order in orders:
                if order.get('id') == oid:
                    return order.get('state')
        return None

    except Exception as e:
        logger.error(f"Order status tekshirish xatosi: {e}")
        return None

# ============ STATISTIKA ============
call_statistics = {
    "total_calls": 0,
    "answered_calls": 0,
    "unanswered_calls": 0,
    "first_attempt_answered": 0,
    "second_attempt_answered": 0,
    "by_date": {},
}

order_statistics = {
    "total_orders": 0,
    "accepted_orders": 0,
    "cancelled_orders": 0,
    "telegram_accepted": 0,
    "ready_orders": 0,
    "delivering_orders": 0,
    "completed_orders": 0,
    "by_seller": {},
    "by_date": {},
}

def record_call_statistic(answered: bool, attempt: int = 1):
    """Qo'ng'iroq statistikasini yozish"""
    today = datetime.now().strftime('%Y-%m-%d')

    if today not in call_statistics["by_date"]:
        call_statistics["by_date"][today] = {
            "total": 0, "answered": 0, "unanswered": 0,
            "first_attempt": 0, "second_attempt": 0
        }

    call_statistics["total_calls"] += 1
    call_statistics["by_date"][today]["total"] += 1

    if answered:
        call_statistics["answered_calls"] += 1
        call_statistics["by_date"][today]["answered"] += 1

        if attempt == 1:
            call_statistics["first_attempt_answered"] += 1
            call_statistics["by_date"][today]["first_attempt"] += 1
        elif attempt == 2:
            call_statistics["second_attempt_answered"] += 1
            call_statistics["by_date"][today]["second_attempt"] += 1
    else:
        call_statistics["unanswered_calls"] += 1
        call_statistics["by_date"][today]["unanswered"] += 1

# ============ SOTUVCHI BUYURTMALARINI GURUHLASH ============
async def process_seller_orders(seller_phone: str, order_ids: List[int], business_info: Dict, language: str = None):
    """Bir sotuvchining barcha buyurtmalarini qayta ishlash (til bo'yicha)"""
    # Til aniqlash
    seller_language = language or business_info.get('language') or DEFAULT_LANGUAGE
    logger.info(f"Sotuvchi {seller_phone}: Til = {seller_language}")

    order_count = len(order_ids)
    logger.info(f"Sotuvchi {seller_phone}: {order_count} ta buyurtma qayta ishlanmoqda...")

    logger.info(f"Sotuvchi {seller_phone}: {WAIT_BEFORE_CALL} sek kutilmoqda...")
    await asyncio.sleep(WAIT_BEFORE_CALL)
    logger.info(f"Sotuvchi {seller_phone}: Muddat yetdi, qo'ng'iroq qilinmoqda!")

    current_ids = seller_order_groups.get(seller_phone, [])
    if len(current_ids) > order_count:
        order_ids = current_ids
        order_count = len(order_ids)
        logger.info(f"Sotuvchi {seller_phone}: Yangi buyurtmalar qo'shildi, jami {order_count} ta")

    active_order_ids = []
    for oid in order_ids:
        status = get_order_status(oid)
        if status and status == ORDER_STATUS_CHECKING:
            active_order_ids.append(oid)

    if not active_order_ids:
        logger.info(f"Sotuvchi {seller_phone}: Barcha buyurtmalar qabul qilindi")
        if seller_phone in seller_order_groups:
            del seller_order_groups[seller_phone]
        return

    order_count = len(active_order_ids)
    logger.info(f"Sotuvchi {seller_phone}: {order_count} ta faol buyurtma")

    call_answered = False

    for attempt in range(1, MAX_RETRIES + 1):
        still_pending = []
        for oid in active_order_ids:
            status = get_order_status(oid)
            if status and status == ORDER_STATUS_CHECKING:
                still_pending.append(oid)

        if not still_pending:
            logger.info(f"Sotuvchi {seller_phone}: Barcha buyurtmalar qabul qilindi!")
            call_answered = True
            break

        order_count = len(still_pending)
        logger.info(f"Sotuvchi {seller_phone}: {attempt}-qo'ng'iroq, {order_count} ta buyurtma")

        call_success = await make_call_with_count(seller_phone, order_count, still_pending, seller_language)

        if call_success:
            await asyncio.sleep(CALL_WAIT_TIME)

            orders_accepted = 0
            for oid in still_pending:
                new_status = get_order_status(oid)
                if new_status and new_status != ORDER_STATUS_CHECKING:
                    orders_accepted += 1

            if orders_accepted > 0:
                record_call_statistic(answered=True, attempt=attempt)
                call_answered = True
        else:
            await asyncio.sleep(CALL_WAIT_TIME)

        seller_last_call[seller_phone] = datetime.now()

    if not call_answered:
        record_call_statistic(answered=False, attempt=MAX_RETRIES)

    await asyncio.sleep(60)

    final_pending = []
    for oid in active_order_ids:
        status = get_order_status(oid)
        if status and status == ORDER_STATUS_CHECKING:
            final_pending.append(oid)

    if final_pending:
        order_count = len(final_pending)
        logger.warning(f"Sotuvchi {seller_phone}: {order_count} ta buyurtma qabul qilinmadi!")

        # business_info dan ma'lumot olish (pending_orders o'chirilgan bo'lishi mumkin)
        biznes_nomi = business_info.get('biznes_nomi') or 'Noma\'lum'
        mijoz_nomi = business_info.get('mijoz_nomi') or 'Noma\'lum'
        mijoz_tel = business_info.get('mijoz_tel') or 'Noma\'lum'
        narx = business_info.get('narx', 0)

        orders_list = []
        for oid in final_pending:
            # Avval pending_orders dan, keyin business_info dan olish
            order_data = pending_orders.get(oid, {})
            bi = order_data.get('business_info', business_info)
            orders_list.append({
                "lead_id": oid,
                "order_number": oid,
                "mijoz_nomi": bi.get('mijoz_nomi', mijoz_nomi),
                "mijoz_tel": bi.get('mijoz_tel', mijoz_tel),
                "mahsulot": bi.get('mahsulot', 'Buyurtma'),
                "miqdor": bi.get('miqdor', 1),
                "narx": bi.get('narx', narx),
                "products": bi.get('products', []),  # Barcha mahsulotlar
            })

        seller_orders = {
            "seller_name": biznes_nomi,
            "seller_phone": seller_phone,
            "seller_address": business_info.get('biznes_manzil', 'Noma\'lum'),
            "orders": orders_list
        }

        logger.info(f"Telegram xabar yuborilmoqda: {seller_phone}, {order_count} ta buyurtma")
        send_seller_orders_alert(seller_orders, call_attempts=MAX_RETRIES)

    if seller_phone in seller_order_groups:
        del seller_order_groups[seller_phone]

    for oid in order_ids:
        if oid in pending_orders:
            del pending_orders[oid]

    logger.info(f"Sotuvchi {seller_phone}: Task tugadi")

async def polling_task():
    """Nonbor API dan buyurtmalarni polling qilish"""
    logger.info("Polling boshlandi...")

    cleanup_counter = 0

    while True:
        try:
            cleanup_counter += 1
            if cleanup_counter >= 100:
                rate_limiter.cleanup()
                cleanup_counter = 0

            all_orders = get_all_orders()

            current_api_order_ids = set(o.get('id') for o in all_orders if o.get('id'))

            for seller_key, data in list(seller_orders_data.items()):
                orders_in_telegram = data.get("seller_orders", {}).get("orders", [])

                for order in orders_in_telegram:
                    order_id = order.get("lead_id") or order.get("order_number")

                    if order_id and order_id not in current_api_order_ids:
                        logger.info(f"Buyurtma #{order_id} API dan yo'qoldi (qabul qilingan)")
                        update_seller_telegram_on_status_change(seller_key, order_id, ORDER_STATUS_ACCEPTED)

                        if order_id in order_statuses:
                            del order_statuses[order_id]
                        if order_id in order_to_seller:
                            del order_to_seller[order_id]

            for order in all_orders:
                order_id = order.get('id')
                if not order_id:
                    continue

                current_status = order.get('state')
                old_status = order_statuses.get(order_id)

                if order_id not in order_to_seller:
                    business = order.get('business', {})
                    biznes_nomi = business.get('title', 'Noma\'lum')
                    order_to_seller[order_id] = biznes_nomi

                if old_status and old_status != current_status:
                    logger.info(f"Status o'zgardi: #{order_id} {old_status} → {current_status}")

                    seller_key = order_to_seller.get(order_id)
                    if not seller_key:
                        order_data = pending_orders.get(order_id, {})
                        seller_key = order_data.get('seller_phone')

                    if seller_key:
                        update_seller_telegram_on_status_change(seller_key, order_id, current_status)

                        if order_id in pending_orders:
                            del pending_orders[order_id]

                order_statuses[order_id] = current_status

            new_orders_by_seller: Dict[str, List] = {}

            for order in all_orders:
                order_id = order.get('id')
                if not order_id:
                    continue

                if order_id in processed_orders or order_id in pending_orders:
                    continue

                created_at_str = order.get('created_at', '')
                if created_at_str:
                    try:
                        clean_date = created_at_str.split('+')[0].split('Z')[0]
                        if '.' in clean_date:
                            created_at = datetime.strptime(clean_date, '%Y-%m-%dT%H:%M:%S.%f')
                        else:
                            created_at = datetime.strptime(clean_date, '%Y-%m-%dT%H:%M:%S')
                        age_seconds = (datetime.now() - created_at).total_seconds()
                        if age_seconds > 300:
                            processed_orders.add(order_id)
                            continue
                    except Exception as e:
                        logger.warning(f"Buyurtma #{order_id}: Sana parse xatosi: {e}")

                business = order.get('business', {})

                # Sotuvchi ilova tili (business yoki order dan)
                seller_language = (
                    business.get('language') or
                    business.get('lang') or
                    order.get('language') or
                    order.get('lang') or
                    DEFAULT_LANGUAGE
                )
                # Til kodini normallashtirish (uz, ru, en)
                if seller_language:
                    seller_language = seller_language.lower()[:2]
                    if seller_language not in TTS_VOICES:
                        seller_language = DEFAULT_LANGUAGE

                business_info = {
                    'biznes_nomi': business.get('title', 'Noma\'lum'),
                    'biznes_tel': None,
                    'biznes_manzil': business.get('address', ''),
                    'order_id': order_id,
                    'narx': order.get('total_price', 0),
                    'tolov': order.get('payment_method', 'CASH'),
                    'yetkazish': order.get('delivery_method', 'DELIVERY'),
                    'language': seller_language,  # Sotuvchi tili
                }

                user = order.get('user', {})
                first_name = user.get('first_name', '')
                last_name = user.get('last_name', '')
                mijoz_nomi = f"{first_name} {last_name}".strip() or 'Noma\'lum'
                business_info['mijoz_nomi'] = mijoz_nomi
                business_info['mijoz_tel'] = user.get('phone', '')

                # Barcha mahsulotlarni olish
                order_items = order.get('order_item', [])
                products_list = []
                if order_items:
                    for item in order_items:
                        if isinstance(item, dict):
                            product = item.get('product', {})
                            products_list.append({
                                'name': product.get('name', 'Mahsulot'),
                                'quantity': item.get('quantity', 1),
                                'price': product.get('price', 0)
                            })
                    # Birinchi mahsulot nomi (qisqa ko'rinish uchun)
                    business_info['mahsulot'] = products_list[0]['name'] if products_list else 'Noma\'lum'
                    business_info['miqdor'] = len(order_items)
                    business_info['products'] = products_list  # Barcha mahsulotlar
                else:
                    business_info['mahsulot'] = 'Noma\'lum'
                    business_info['miqdor'] = 1
                    business_info['products'] = []

                phone = sanitize_phone(business_info.get('mijoz_tel') or user.get('phone', ''))

                if not phone:
                    logger.warning(f"Buyurtma #{order_id}: Telefon raqami topilmadi yoki noto'g'ri")
                    continue

                if phone not in new_orders_by_seller:
                    new_orders_by_seller[phone] = []
                new_orders_by_seller[phone].append((order_id, business_info))

                pending_orders[order_id] = {
                    'order_id': order_id,
                    'seller_phone': phone,
                    'business_info': business_info,
                    'created_at': order.get('created_at', datetime.now().isoformat())
                }
                processed_orders.add(order_id)
                order_to_seller[order_id] = phone

                logger.info(f"Yangi buyurtma: #{order_id} - {phone} - {business_info.get('biznes_nomi')}")

            for seller_phone, orders_list in new_orders_by_seller.items():
                order_ids = [o[0] for o in orders_list]
                business_info = orders_list[0][1]

                if seller_phone not in seller_order_groups:
                    seller_order_groups[seller_phone] = []
                    seller_order_groups[seller_phone].extend(order_ids)
                    seller_lang = business_info.get('language', DEFAULT_LANGUAGE)
                    logger.info(f"Sotuvchi {seller_phone}: {len(order_ids)} ta yangi buyurtma, til={seller_lang}, task ishga tushirilmoqda")
                    asyncio.create_task(process_seller_orders(seller_phone, order_ids, business_info, seller_lang))
                else:
                    seller_order_groups[seller_phone].extend(order_ids)
                    logger.info(f"Sotuvchi {seller_phone}: {len(order_ids)} ta buyurtma qo'shildi, jami {len(seller_order_groups[seller_phone])} ta")

            current_order_ids = set(o.get('id') for o in all_orders if o.get('id'))
            old_processed = [oid for oid in list(processed_orders) if oid not in current_order_ids]
            for oid in old_processed[:100]:
                processed_orders.discard(oid)
                if oid in pending_orders:
                    del pending_orders[oid]

            if old_processed:
                logger.info(f"Eski buyurtmalar tozalandi: {len(old_processed)} ta")

        except Exception as e:
            logger.error(f"Polling xatosi: {e}")

        await asyncio.sleep(POLLING_INTERVAL)

# ============ HTTP HANDLERS ============

@rate_limit_middleware
async def handle_call_result(request):
    """IVR qo'ng'iroq natijasi"""
    try:
        data = await request.json()
        order_id = validate_order_id(data.get('order_id'))
        status = data.get('status')
        phone = sanitize_phone(data.get('phone'))

        logger.info(f"IVR natija: Order #{order_id} - {status} ({phone})")

        if order_id and status in ['accepted', 'rejected', 'timeout']:
            call_results[order_id] = status

            if order_id in pending_orders:
                del pending_orders[order_id]

        return web.json_response({"status": "ok", "received": {"order_id": order_id, "status": status}})

    except Exception as e:
        logger.error(f"Call result xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

@rate_limit_middleware
async def handle_order_webhook(request):
    """Nonbor backend dan buyurtma statusi o'zgarganda webhook"""
    try:
        data = await request.json()
        order_id = validate_order_id(data.get('order_id') or data.get('id'))
        new_status = data.get('status') or data.get('state')
        old_status = data.get('old_status')
        seller_phone = sanitize_phone(data.get('seller_phone') or data.get('business', {}).get('phone'))
        seller_name = sanitize_text(data.get('seller_name') or data.get('business', {}).get('title', 'Noma\'lum'))

        if not order_id:
            return web.json_response({"status": "error", "message": "Invalid order_id"}, status=400)

        if not new_status or new_status not in [
            ORDER_STATUS_CHECKING, ORDER_STATUS_ACCEPTED, ORDER_STATUS_READY,
            ORDER_STATUS_DELIVERING, ORDER_STATUS_COMPLETED, ORDER_STATUS_CANCELLED,
            ORDER_STATUS_CANCELLED_SELLER, ORDER_STATUS_CANCELLED_CLIENT, ORDER_STATUS_PENDING
        ]:
            return web.json_response({"status": "error", "message": "Invalid status"}, status=400)

        logger.info(f"WEBHOOK: Buyurtma #{order_id} | {old_status} → {new_status} | {seller_name}")

        today = datetime.now().strftime('%Y-%m-%d')

        if today not in order_statistics["by_date"]:
            order_statistics["by_date"][today] = {
                "total": 0, "accepted": 0, "cancelled": 0,
                "ready": 0, "delivering": 0, "completed": 0,
                "telegram_accepted": 0
            }

        if seller_name not in order_statistics["by_seller"]:
            order_statistics["by_seller"][seller_name] = {
                "total": 0, "accepted": 0, "cancelled": 0,
                "ready": 0, "delivering": 0, "completed": 0,
                "telegram_accepted": 0
            }

        if new_status == ORDER_STATUS_ACCEPTED:
            order_statistics["accepted_orders"] += 1
            order_statistics["by_date"][today]["accepted"] += 1
            order_statistics["by_seller"][seller_name]["accepted"] += 1

            if old_status == ORDER_STATUS_CHECKING and order_id in pending_orders:
                order_statistics["telegram_accepted"] += 1
                order_statistics["by_date"][today]["telegram_accepted"] += 1
                order_statistics["by_seller"][seller_name]["telegram_accepted"] += 1

        elif new_status == ORDER_STATUS_CANCELLED:
            order_statistics["cancelled_orders"] += 1
            order_statistics["by_date"][today]["cancelled"] += 1
            order_statistics["by_seller"][seller_name]["cancelled"] += 1

        elif new_status == ORDER_STATUS_READY:
            order_statistics["ready_orders"] += 1
            order_statistics["by_date"][today]["ready"] += 1
            order_statistics["by_seller"][seller_name]["ready"] += 1

        elif new_status == ORDER_STATUS_DELIVERING:
            order_statistics["delivering_orders"] += 1
            order_statistics["by_date"][today]["delivering"] += 1
            order_statistics["by_seller"][seller_name]["delivering"] += 1

        elif new_status == ORDER_STATUS_COMPLETED:
            order_statistics["completed_orders"] += 1
            order_statistics["by_date"][today]["completed"] += 1
            order_statistics["by_seller"][seller_name]["completed"] += 1

        if new_status == ORDER_STATUS_CHECKING:
            order_statistics["total_orders"] += 1
            order_statistics["by_date"][today]["total"] += 1
            order_statistics["by_seller"][seller_name]["total"] += 1

        seller_key = seller_phone or seller_name
        if seller_key:
            update_seller_telegram_on_status_change(seller_key, order_id, new_status)

        if order_id in pending_orders:
            del pending_orders[order_id]

        if order_id in order_statuses:
            order_statuses[order_id] = new_status

        return web.json_response({
            "status": "ok",
            "order_id": order_id,
            "new_status": new_status,
            "statistics": {
                "total": order_statistics["total_orders"],
                "accepted": order_statistics["accepted_orders"],
                "cancelled": order_statistics["cancelled_orders"],
                "completed": order_statistics["completed_orders"]
            }
        })

    except json.JSONDecodeError:
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Webhook xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

@rate_limit_middleware
async def handle_statistics(request):
    """Statistikani olish"""
    period = request.query.get('period', 'today')
    output_format = request.query.get('format', 'json')

    if period not in ['today', 'week', 'month', 'year']:
        period = 'today'

    today = datetime.now().strftime('%Y-%m-%d')

    if period == 'week':
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    elif period == 'month':
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    elif period == 'year':
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    else:
        start_date = today

    if period == 'today':
        today_calls = call_statistics["by_date"].get(today, {})
        calls_data = {
            "total": today_calls.get("total", 0),
            "answered": today_calls.get("answered", 0),
            "unanswered": today_calls.get("unanswered", 0),
            "first_attempt": today_calls.get("first_attempt", 0),
            "second_attempt": today_calls.get("second_attempt", 0)
        }
    else:
        calls_data = {"total": 0, "answered": 0, "unanswered": 0, "first_attempt": 0, "second_attempt": 0}
        for date_str, stats in call_statistics["by_date"].items():
            if date_str >= start_date:
                calls_data["total"] += stats.get("total", 0)
                calls_data["answered"] += stats.get("answered", 0)
                calls_data["unanswered"] += stats.get("unanswered", 0)
                calls_data["first_attempt"] += stats.get("first_attempt", 0)
                calls_data["second_attempt"] += stats.get("second_attempt", 0)

    if period == 'today':
        today_orders = order_statistics["by_date"].get(today, {})
        orders_data = {
            "total": today_orders.get("total", 0),
            "accepted": today_orders.get("accepted", 0),
            "cancelled": today_orders.get("cancelled", 0),
            "telegram_accepted": today_orders.get("telegram_accepted", 0)
        }
    else:
        orders_data = {"total": 0, "accepted": 0, "cancelled": 0, "telegram_accepted": 0}
        for date_str, stats in order_statistics["by_date"].items():
            if date_str >= start_date:
                orders_data["total"] += stats.get("total", 0)
                orders_data["accepted"] += stats.get("accepted", 0)
                orders_data["cancelled"] += stats.get("cancelled", 0)
                orders_data["telegram_accepted"] += stats.get("telegram_accepted", 0)

    if output_format == 'telegram':
        period_names = {
            'today': 'BUGUNGI',
            'week': 'HAFTALIK',
            'month': 'OYLIK',
            'year': 'YILLIK'
        }
        period_name = period_names.get(period, 'BUGUNGI')

        telegram_text = f"""
📊 <b>{period_name} STATISTIKA</b>

📞 <b>QO'NG'IROQLAR:</b> {calls_data['total']} ta
✅ Javob berildi: {calls_data['answered']}
❌ Javob berilmadi: {calls_data['unanswered']}
1️⃣ 1-urinishda: {calls_data['first_attempt']}
2️⃣ 2-urinishda: {calls_data['second_attempt']}

📦 <b>BUYURTMALAR:</b> {orders_data['total']} ta
✅ Qabul qilindi: {orders_data['accepted']}
❌ Bekor qilindi: {orders_data['cancelled']}
📱 Telegram'dan qabul: {orders_data['telegram_accepted']}
"""
        return web.json_response({
            "status": "ok",
            "period": period,
            "telegram_text": telegram_text.strip(),
            "calls": calls_data,
            "orders": orders_data
        })

    return web.json_response({
        "status": "ok",
        "period": period,
        "date_range": {"start": start_date, "end": today},
        "calls": calls_data,
        "orders": orders_data,
        "all_time": {
            "calls": {
                "total": call_statistics["total_calls"],
                "answered": call_statistics["answered_calls"],
                "unanswered": call_statistics["unanswered_calls"],
                "first_attempt": call_statistics["first_attempt_answered"],
                "second_attempt": call_statistics["second_attempt_answered"]
            },
            "orders": {
                "total": order_statistics["total_orders"],
                "accepted": order_statistics["accepted_orders"],
                "cancelled": order_statistics["cancelled_orders"],
                "telegram_accepted": order_statistics.get("telegram_accepted", 0)
            }
        },
        "pending_count": len(pending_orders),
        "processed_count": len(processed_orders)
    })

@rate_limit_middleware
async def handle_test(request):
    """Test endpoint"""
    return web.json_response({
        "status": "ok",
        "message": "Autodialer v2 LINUX ishlayapti!",
        "version": "2.0-linux",
        "pending_orders": len(pending_orders),
        "processed_orders": len(processed_orders),
        "call_results": len(call_results),
        "active_calls": len(active_calls),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID),
        "time": datetime.now().isoformat()
    })

async def handle_test_alert(request):
    """
    Hozirgi CHECKING buyurtmalarni Telegram ga yuborish
    Bu endpoint barcha kutilayotgan buyurtmalar uchun darhol Telegram xabar yuboradi
    """
    try:
        # Barcha buyurtmalarni olish
        all_orders = get_all_orders()
        pending = [o for o in all_orders if o.get('state') == ORDER_STATUS_CHECKING]

        if not pending:
            return web.json_response({"status": "error", "message": "CHECKING buyurtma yo'q"})

        # Sotuvchi telefon raqami bo'yicha guruhlash
        orders_by_seller = {}
        for order in pending:
            business = order.get('business', {})
            biznes_nomi = business.get('title', 'Noma\'lum')

            # Sotuvchi telefon raqamini olish
            seller_phone = (
                business.get('phone') or
                business.get('seller_phone') or
                order.get('seller_phone') or
                biznes_nomi
            )

            if seller_phone not in orders_by_seller:
                orders_by_seller[seller_phone] = {
                    "seller_name": biznes_nomi,
                    "seller_phone": seller_phone,
                    "seller_address": business.get('address', 'Noma\'lum'),
                    "orders": []
                }

            # Buyurtma ma'lumotlarini qo'shish
            user = order.get('user', {})
            mijoz_nomi = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or 'Noma\'lum'
            mijoz_tel = user.get('phone', '') or 'Noma\'lum'

            # Barcha mahsulotlarni olish
            order_items = order.get('order_item', [])
            products_list = []
            if order_items:
                for item in order_items:
                    if isinstance(item, dict):
                        product = item.get('product', {})
                        products_list.append({
                            'name': product.get('name', 'Mahsulot'),
                            'quantity': item.get('quantity', 1),
                            'price': product.get('price', 0)
                        })

            # Birinchi mahsulot nomi
            first_product = products_list[0]['name'] if products_list else 'Buyurtma'

            orders_by_seller[seller_phone]["orders"].append({
                "lead_id": order.get('id'),
                "order_number": order.get('id'),
                "mijoz_nomi": mijoz_nomi,
                "mijoz_tel": mijoz_tel,
                "mahsulot": first_product,
                "miqdor": len(order_items) or 1,
                "narx": order.get('total_price', 0),
                "products": products_list  # Barcha mahsulotlar
            })

            # order_to_seller mapping
            order_to_seller[order.get('id')] = seller_phone

        # Har bir sotuvchi uchun Telegram xabar yuborish
        sent_count = 0
        for seller_phone, seller_orders in orders_by_seller.items():
            result = send_seller_orders_alert(seller_orders, call_attempts=0)
            if result:
                sent_count += 1
                logger.info(f"Alert yuborildi: {seller_phone}, {len(seller_orders['orders'])} ta buyurtma")

        return web.json_response({
            "status": "ok",
            "message": f"{sent_count} ta sotuvchi uchun Telegram xabar yuborildi",
            "total_orders": len(pending),
            "sellers": list(orders_by_seller.keys())
        })

    except Exception as e:
        logger.error(f"Test alert xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})

@rate_limit_middleware
async def handle_check_leads(request):
    """Hozirgi CHECKING buyurtmalarni ko'rsatish"""
    orders = get_checking_orders()
    return web.json_response({
        "count": len(orders),
        "orders": [
            {
                "id": o.get('id'),
                "business": sanitize_text(o.get('business', {}).get('title', '')),
                "state": o.get('state')
            }
            for o in orders[:100]
        ]
    })

@rate_limit_middleware
async def handle_incoming_call(request):
    """Kiruvchi qo'ng'iroq"""
    try:
        data = await request.json()
        caller_id = sanitize_phone(data.get('caller_id'))
        called_number = sanitize_phone(data.get('called_number'))
        call_id = data.get('call_id') or secrets.token_hex(8)

        logger.info(f"Kiruvchi qo'ng'iroq: {caller_id} -> {called_number}")

        if not caller_id:
            return web.json_response({"status": "error", "message": "Valid caller_id required"}, status=400)

        call_record = {
            "id": call_id,
            "phone": caller_id,
            "direction": "incoming",
            "status": "ringing",
            "duration": 0,
            "timestamp": datetime.now().isoformat()
        }

        if len(call_history) >= 1000:
            call_history.pop()
        call_history.insert(0, call_record)
        active_calls[call_id] = call_record

        send_telegram_message(
            f"📞 <b>Kiruvchi qo'ng'iroq!</b>\n\n"
            f"📱 Raqam: {caller_id}\n"
            f"⏰ Vaqt: {datetime.now().strftime('%H:%M:%S')}"
        )

        return web.json_response({
            "status": "ok",
            "call_id": call_id,
            "caller_id": caller_id,
            "action": "create_contact"
        })

    except Exception as e:
        logger.error(f"Kiruvchi qo'ng'iroq xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

@rate_limit_middleware
async def handle_call_hangup(request):
    """Qo'ng'iroq tugatilganda"""
    try:
        data = await request.json()
        call_id = data.get('call_id')
        duration = int(data.get('duration', 0))
        status = data.get('status', 'completed')

        if not call_id:
            return web.json_response({"status": "error", "message": "call_id required"}, status=400)

        logger.info(f"Qo'ng'iroq tugadi: {call_id}, {duration} sek, {status}")

        if call_id in active_calls:
            call_data = active_calls[call_id]
            call_data['status'] = status
            call_data['duration'] = duration

            for record in call_history:
                if record['id'] == call_id:
                    record['status'] = status
                    record['duration'] = duration
                    break

            del active_calls[call_id]

        return web.json_response({"status": "ok"})

    except Exception as e:
        logger.error(f"Call hangup xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

@rate_limit_middleware
async def handle_call_history(request):
    """Qo'ng'iroq tarixini olish"""
    try:
        limit = min(int(request.query.get('limit', 50)), 100)
        direction = request.query.get('direction')

        filtered_history = call_history
        if direction in ['incoming', 'outgoing']:
            filtered_history = [c for c in call_history if c['direction'] == direction]

        return web.json_response({
            "status": "ok",
            "total": len(filtered_history),
            "calls": filtered_history[:limit]
        })

    except Exception as e:
        logger.error(f"Call history xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

@rate_limit_middleware
async def handle_amocrm_call(request):
    """amoCRM ichidan operator qo'ng'iroq qilganda (Click-to-Call)"""
    try:
        data = await request.json()
        phone = data.get('phone')
        user_id = data.get('user_id')
        lead_id = data.get('lead_id')
        contact_id = data.get('contact_id')

        logger.info(f"amoCRM Click-to-Call: {phone}, Operator: {user_id}, Lead: {lead_id}")

        if not phone:
            return web.json_response({"status": "error", "message": "Phone number required"}, status=400)

        phone = sanitize_phone(phone)
        if not phone:
            return web.json_response({"status": "error", "message": "Invalid phone number"}, status=400)

        call_id = secrets.token_hex(8)
        result = make_call_secure(phone)

        if result:
            call_record = {
                "id": call_id,
                "phone": phone,
                "direction": "outgoing",
                "status": "dialing",
                "duration": 0,
                "timestamp": datetime.now().isoformat(),
                "user_id": user_id,
                "lead_id": lead_id,
                "contact_id": contact_id
            }

            if len(call_history) >= 1000:
                call_history.pop()
            call_history.insert(0, call_record)
            active_calls[call_id] = call_record

            logger.info(f"Qo'ng'iroq boshlandi: {call_id}")

            return web.json_response({
                "status": "ok",
                "call_id": call_id,
                "phone": phone,
                "message": "Qo'ng'iroq boshlandi"
            })
        else:
            logger.error(f"Qo'ng'iroq qilishda xatolik: {phone}")
            return web.json_response({
                "status": "error",
                "message": "Qo'ng'iroq qilishda xatolik. Asterisk sozlamalarini tekshiring."
            }, status=500)

    except Exception as e:
        logger.error(f"amoCRM call xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

@rate_limit_middleware
async def handle_amocrm_call_event(request):
    """Asterisk dan qo'ng'iroq event lari"""
    try:
        data = await request.json()
        call_id = data.get('call_id')
        event = data.get('event')
        duration = int(data.get('duration', 0))
        phone = data.get('phone')

        logger.info(f"Call event: {call_id} - {event} ({duration} sek)")

        valid_events = ['answer', 'hangup', 'busy', 'no-answer']
        if event and event not in valid_events:
            return web.json_response({"status": "error", "message": "Invalid event type"}, status=400)

        if call_id and call_id in active_calls:
            call_data = active_calls[call_id]

            if event == 'hangup':
                call_data['status'] = 'completed'
                call_data['duration'] = duration

                for record in call_history:
                    if record['id'] == call_id:
                        record['status'] = 'completed'
                        record['duration'] = duration
                        break

                del active_calls[call_id]

            elif event == 'answer':
                call_data['status'] = 'in_progress'

            elif event in ['busy', 'no-answer']:
                call_data['status'] = event

                for record in call_history:
                    if record['id'] == call_id:
                        record['status'] = event
                        break

                del active_calls[call_id]

        return web.json_response({"status": "ok"})

    except Exception as e:
        logger.error(f"Call event xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

# ============ APP SETUP ============
async def on_startup(app):
    """Server ishga tushganda"""
    asyncio.create_task(polling_task())

async def on_cleanup(app):
    """Server to'xtaganda"""
    await close_http_session()

def create_app():
    """Web application yaratish"""
    app = web.Application()

    app.router.add_post('/call-result', handle_call_result)
    app.router.add_get('/test', handle_test)
    app.router.add_get('/test-alert', handle_test_alert)  # Telegram ga buyurtmalarni yuborish
    app.router.add_get('/check-leads', handle_check_leads)

    app.router.add_post('/api/webhook/order-status', handle_order_webhook)
    app.router.add_get('/api/statistics', handle_statistics)

    app.router.add_post('/api/incoming-call', handle_incoming_call)
    app.router.add_post('/api/call-hangup', handle_call_hangup)
    app.router.add_get('/api/call-history', handle_call_history)

    app.router.add_post('/api/amocrm/call', handle_amocrm_call)
    app.router.add_post('/api/amocrm/call-event', handle_amocrm_call_event)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app

if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("AUTODIALER V2 - LINUX VERSIYA")
    logger.info("=" * 50)
    logger.info(f"API: {NONBOR_API_URL}")
    logger.info(f"Status: {ORDER_STATUS_CHECKING}")
    logger.info(f"Polling interval: {POLLING_INTERVAL} sek")
    logger.info(f"Rate limit: {RATE_LIMIT_REQUESTS} requests / {RATE_LIMIT_WINDOW} sek")
    logger.info("=" * 50)
    logger.info("TELEGRAM:")
    logger.info(f"  Bot token: {'***' + TELEGRAM_BOT_TOKEN[-10:] if TELEGRAM_BOT_TOKEN else 'YO`Q'}")
    logger.info(f"  Chat ID: {TELEGRAM_ADMIN_CHAT_ID or 'YO`Q'}")
    logger.info("=" * 50)
    logger.info("ASTERISK:")
    logger.info(f"  Audio path: {TTS_ASTERISK_PATH}")
    logger.info(f"  SIP Server: {SIP_SERVER}")
    logger.info(f"  Endpoint: {SARKOR_ENDPOINT}")
    logger.info("=" * 50)

    app = create_app()

    logger.info("Server: http://0.0.0.0:8080")
    logger.info("=" * 50)
    logger.info("ENDPOINTS:")
    logger.info("-" * 50)
    logger.info("Autodialer:")
    logger.info("  GET  /test              - Server test")
    logger.info("  GET  /check-leads       - amoCRM leadlar")
    logger.info("  POST /call-result       - IVR callback")
    logger.info("-" * 50)
    logger.info("amoCRM Telephony:")
    logger.info("  POST /api/amocrm/call       - Click-to-Call")
    logger.info("  POST /api/amocrm/call-event - Qo'ng'iroq holati")
    logger.info("-" * 50)
    logger.info("Kiruvchi qo'ng'iroqlar:")
    logger.info("  POST /api/incoming-call     - Kiruvchi qo'ng'iroq")
    logger.info("  POST /api/call-hangup       - Qo'ng'iroq tugadi")
    logger.info("  GET  /api/call-history      - Qo'ng'iroq tarixi")
    logger.info("=" * 50)

    web.run_app(app, host='0.0.0.0', port=8080)
