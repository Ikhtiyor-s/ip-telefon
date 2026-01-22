#!/usr/bin/env python3
"""
Avtomatik Qo'ng'iroq Tizimi v2
- Nonbor API dan PENDING statusidagi buyurtmalarni polling qiladi
- Sotuvchiga qo'ng'iroq qilish (Click-to-Call)
- Kiruvchi qo'ng'iroqlarni qayta ishlash
"""

import asyncio
import subprocess
import logging
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime
from aiohttp import web
import requests

# ============ SOZLAMALAR ============
ASTERISK_CMD = "wsl -u root -e bash -c 'asterisk -rx"
SARKOR_ENDPOINT = "sarkor-endpoint"
SIP_SERVER = "well-tech.sip.uz"

# Telegram Bot - ENVIRONMENT VARIABLES dan
import os
from dotenv import load_dotenv
load_dotenv()  # .env faylni yuklash

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

# Nonbor API
NONBOR_API_URL = os.environ.get("NONBOR_API_URL", "https://test.nonbor.uz/api/v2/telegram_bot/get-order-for-courier/")
NONBOR_BASE_URL = "https://test.nonbor.uz/api/v2"
NONBOR_BUSINESSES_URL = f"{NONBOR_BASE_URL}/telegram_bot/businesses/accepted/"
NONBOR_REGIONS_URL = f"{NONBOR_BASE_URL}/regions/"
NONBOR_ORDERS_URL = f"{NONBOR_BASE_URL}/orders/"
NONBOR_BUSINESS_STATS_URL = f"{NONBOR_BASE_URL}/telegram_bot/business-stats/"
NONBOR_SECRET_KEY = os.environ.get("NONBOR_SECRET_KEY", "nonbor-secret-key")

def get_nonbor_headers():
    """Nonbor API uchun headerlar"""
    return {
        "X-Telegram-Bot-Secret": NONBOR_SECRET_KEY,
        "Content-Type": "application/json",
        "accept": "application/json"
    }

# amoCRM API sozlamalari - ENVIRONMENT VARIABLES dan
AMOCRM_DOMAIN = os.environ.get("AMOCRM_DOMAIN", "")
AMOCRM_ACCESS_TOKEN = os.environ.get("AMOCRM_ACCESS_TOKEN", "")
AMOCRM_PIPELINE_ID = int(os.environ.get("AMOCRM_PIPELINE_ID", "0"))
AMOCRM_STATUS_TEKSHIRILMOQDA = int(os.environ.get("AMOCRM_STATUS_TEKSHIRILMOQDA", "0"))

# Order statuslar - Nonbor yangi statuslari
ORDER_STATUS_CHECKING = "CHECKING"  # Tekshirilmoqda - qo'ng'iroq qilish kerak
ORDER_STATUS_PENDING = "PENDING"  # Eski status (CHECKING ga almashtirish kerak)
ORDER_STATUS_ACCEPTED = "ACCEPTED"  # Qabul qilindi
ORDER_STATUS_READY = "READY"  # Tayyor
ORDER_STATUS_DELIVERING = "DELIVERING"  # Yetkazilmoqda
ORDER_STATUS_COMPLETED = "COMPLETED"  # Yakunlandi
ORDER_STATUS_CANCELLED = "CANCELLED"  # Bekor qilindi
ORDER_STATUS_CANCELLED_SELLER = "CANCELLED_SELLER"  # Sotuvchi rad etdi
ORDER_STATUS_CANCELLED_CLIENT = "CANCELLED_CLIENT"  # Mijoz rad etdi

# Qo'ng'iroq sozlamalari
WAIT_BEFORE_CALL = 90  # 1.5 daqiqa (90 sek) kutish
MAX_RETRIES = 2  # 2 marta qo'ng'iroq
TELEGRAM_ALERT_TIME = 150  # 2.5 daqiqada (150 sek) Telegram xabar
POLLING_INTERVAL = 3  # Har 3 sekundda tekshirish (real-time)
CALL_WAIT_TIME = 10  # Javob bermasa 10 sekdan keyin qayta qo'ng'iroq
AUDIO_PLAY_TIME = 40  # Qo'ng'iroq davomiyligi (40 sek)
STATUS_ALERT_TIMEOUT = 180  # 180 sekundda status o'zgarmasa Telegram ogohlantirish
# PARALLEL ishlash: Har bir sotuvchi MUSTAQIL task da ishlaydi (asyncio.create_task)

# TTS (Text-to-Speech) sozlamalari
TTS_AUDIO_CACHE = Path("C:/Users/Asus/autodialer-pro/audio/cache")
TTS_WSL_PATH = "/usr/share/asterisk/sounds/custom"  # Asterisk custom sounds papkasi

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

# ============ LOGGING ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============ BUYURTMALAR QUEUE ============
pending_orders = {}  # {order_id: order_data}
order_messages = {}  # {order_id: message_id} - Telegram xabar ID lari
seller_messages = {}  # {seller_phone: message_id} - Sotuvchi bo'yicha Telegram xabar
seller_orders_data = {}  # {seller_phone: seller_orders_dict} - Sotuvchi buyurtmalari (xabar yangilash uchun)
order_to_seller = {}  # {order_id: seller_phone} - Buyurtma → Sotuvchi mapping (status kuzatish uchun)
order_statuses = {}  # {order_id: status} - Buyurtma statuslarini kuzatish
processed_orders = set()  # Qayta ishlanmagan buyurtmalar
call_results = {}  # {order_id: result} - IVR natijalar
phone_call_answered = {}  # {phone: True} - Telefon bo'yicha javob berilgan qo'ng'iroqlar
order_first_seen = {}  # {order_id: datetime} - Buyurtma birinchi ko'rilgan vaqt (180 sek alert uchun)
status_alerted_orders = set()  # Ogohlantirish yuborilgan buyurtmalar (qayta yubormaslik uchun)
status_alert_messages = {}  # {seller_phone: message_id} - Status alert xabarlari (yangilash uchun)

# ============ QO'NG'IROQ TARIXI (amoCRM uchun) ============
call_history = []  # [{id, phone, direction, status, duration, timestamp}]
active_calls = {}  # {call_id: call_data} - Hozirgi faol qo'ng'iroqlar


# ============ TTS FUNKSIYALARI ============
def get_tts_cache_path(text: str) -> Path:
    """Matn uchun cache fayl yo'lini olish (MD5 hash)"""
    text_hash = hashlib.md5(text.encode()).hexdigest()
    return TTS_AUDIO_CACHE / f"{text_hash}.wav"


def get_order_message_text(count: int, language: str = None) -> str:
    """Buyurtma soni uchun xabar matni (til bo'yicha)"""
    lang = language or DEFAULT_LANGUAGE
    if lang not in TTS_MESSAGES:
        lang = DEFAULT_LANGUAGE

    messages = TTS_MESSAGES[lang]
    if count == 1:
        return messages["single"]
    else:
        return messages["multiple"].format(count=count)


async def generate_tts_audio(text: str, language: str = None) -> Path:
    """Edge TTS orqali audio yaratish (til bo'yicha)"""
    # Til bo'yicha cache path (til + matn hash)
    lang = language or DEFAULT_LANGUAGE
    if lang not in TTS_VOICES:
        lang = DEFAULT_LANGUAGE

    # Cache key = til + matn
    cache_key = f"{lang}_{text}"
    text_hash = hashlib.md5(cache_key.encode()).hexdigest()
    cache_path = TTS_AUDIO_CACHE / f"{text_hash}.wav"

    # Cache da bo'lsa, qaytarish
    if cache_path.exists():
        logger.debug(f"TTS cache dan olindi: {cache_path} (til: {lang})")
        return cache_path

    try:
        import edge_tts

        voice = TTS_VOICES[lang]
        logger.info(f"TTS yaratilmoqda: til={lang}, ovoz={voice}")

        # Edge TTS bilan audio yaratish
        communicate = edge_tts.Communicate(text, voice)
        mp3_path = cache_path.with_suffix(".mp3")
        await communicate.save(str(mp3_path))

        # WAV ga convert (Asterisk uchun 8kHz mono)
        cmd = f'ffmpeg -y -i "{mp3_path}" -ar 8000 -ac 1 -acodec pcm_s16le "{cache_path}"'
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await process.wait()

        # MP3 ni o'chirish
        mp3_path.unlink(missing_ok=True)

        logger.info(f"TTS yaratildi: {cache_path}")
        return cache_path

    except ImportError:
        logger.error("edge_tts kutubxonasi o'rnatilmagan! pip install edge-tts")
        return None
    except Exception as e:
        logger.error(f"TTS xatosi: {e}")
        return None


async def generate_order_audio(count: int, language: str = None) -> Path:
    """Buyurtma soni uchun audio yaratish (til bo'yicha)"""
    text = get_order_message_text(count, language)
    return await generate_tts_audio(text, language)


def sync_audio_to_asterisk(audio_path: Path) -> str:
    """Audio faylni WSL/Asterisk ga ko'chirish va alaw formatga o'zgartirish"""
    try:
        if not audio_path or not audio_path.exists():
            return None

        # Windows path ni WSL path ga aylantirish
        win_path = str(audio_path).replace("\\", "/")
        if len(win_path) > 1 and win_path[1] == ":":
            wsl_source = f"/mnt/{win_path[0].lower()}{win_path[2:]}"
        else:
            wsl_source = win_path

        # Fayl nomini olish (.wav siz)
        audio_name = audio_path.stem

        # Asterisk sounds papkasiga WAV ko'chirish va ALAW formatga o'zgartirish
        cmd = f'wsl -u root -e bash -c "cp {wsl_source} {TTS_WSL_PATH}/{audio_name}.wav && sox {TTS_WSL_PATH}/{audio_name}.wav -t raw -r 8000 -c 1 -e a-law {TTS_WSL_PATH}/{audio_name}.alaw && chown asterisk:asterisk {TTS_WSL_PATH}/{audio_name}.* && chmod 644 {TTS_WSL_PATH}/{audio_name}.*"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            logger.debug(f"Audio Asterisk ga ko'chirildi: {TTS_WSL_PATH}/{audio_name}.alaw")
            return audio_name
        else:
            logger.error(f"Audio ko'chirish xatosi: {result.stderr}")
            return None

    except Exception as e:
        logger.error(f"Audio sync xatosi: {e}")
        return None


def make_call(phone_number, order_id=None):
    """Asterisk orqali qo'ng'iroq qilish - Sarkor Telecom"""
    try:
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number

        if order_id:
            # IVR konteksti bilan qo'ng'iroq (buyurtma tasdiqlash uchun)
            # autodialer-ivr context ishlatamiz - audio eshittirib, 10 sek kutadi
            cmd = f"wsl -u root -e bash -c \"asterisk -rx 'channel originate PJSIP/{phone_number}@{SARKOR_ENDPOINT} extension s@autodialer-ivr'\""
        else:
            # Oddiy qo'ng'iroq - faqat audio eshittirish
            cmd = f"wsl -u root -e bash -c \"asterisk -rx 'channel originate PJSIP/{phone_number}@{SARKOR_ENDPOINT} extension s@autodialer-ivr'\""

        logger.info(f"Qo'ng'iroq qilinmoqda: {phone_number}, Order ID: {order_id}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"Qo'ng'iroq muvaffaqiyatli: {phone_number}")
            return True
        else:
            logger.error(f"Qo'ng'iroq xatosi: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Qo'ng'iroq xatosi: {e}")
        return False


def verify_audio_in_asterisk(audio_name: str) -> bool:
    """Asterisk da audio fayl mavjudligini tekshirish"""
    try:
        # To'g'ri yo'lda tekshirish
        cmd = f'wsl -u root -e bash -c "test -f {TTS_WSL_PATH}/{audio_name}.wav && echo exists"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if "exists" in result.stdout:
            logger.info(f"Audio Asterisk da mavjud: {audio_name}")
            return True
        else:
            logger.warning(f"Audio Asterisk da topilmadi: {TTS_WSL_PATH}/{audio_name}.wav")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Audio tekshirish timeout")
        return False
    except Exception as e:
        logger.error(f"Audio tekshirish xatosi: {e}")
        return False


async def prepare_audio_for_call(order_count: int, language: str = None) -> str:
    """
    Qo'ng'iroq uchun audio tayyorlash va Asterisk ga ko'chirish

    Returns:
        audio_name: Asterisk dagi fayl nomi (muvaffaqiyatli bo'lsa)
        None: Audio tayyorlab bo'lmasa
    """
    try:
        # 1. Audio yaratish yoki cache dan olish (til bo'yicha)
        audio_path = await generate_order_audio(order_count, language)

        if not audio_path or not audio_path.exists():
            logger.error(f"Audio yaratib bo'lmadi: {order_count} ta buyurtma")
            return None

        logger.info(f"Audio tayyor: {audio_path}")

        # 2. Asterisk ga ko'chirish
        audio_name = sync_audio_to_asterisk(audio_path)

        if not audio_name:
            logger.error(f"Audio Asterisk ga ko'chirib bo'lmadi")
            return None

        # 3. Asterisk da mavjudligini tekshirish
        if not verify_audio_in_asterisk(audio_name):
            logger.error(f"Audio Asterisk da topilmadi: {audio_name}")
            return None

        logger.info(f"Audio Asterisk da tasdiqlandi: {audio_name}")
        return audio_name

    except Exception as e:
        logger.error(f"Audio tayyorlash xatosi: {e}")
        return None


async def test_audio_playback(audio_name: str) -> bool:
    """Asterisk da audio eshitilishini test qilish (console orqali)"""
    try:
        # Asterisk console da audio play qilish
        cmd = f'wsl -u root -e bash -c "asterisk -rx \'channel originate Local/s@test-audio extension {audio_name}@autodialer-dynamic\'"'
        logger.info(f"Audio test qilinmoqda: {audio_name}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)

        # Asterisk log dan tekshirish (audio play bo'ldimi)
        return True  # Console test - manual tekshirish kerak
    except Exception as e:
        logger.error(f"Audio test xatosi: {e}")
        return False


async def make_call_with_count(phone_number, order_count, order_ids=None, language=None):
    """
    Dinamik audio bilan qo'ng'iroq qilish - buyurtmalar soni aytiladi

    MUHIM: Audio mavjud bo'lmasagina qo'ng'iroq qilinmaydi!

    Args:
        phone_number: Qo'ng'iroq qilinadigan telefon
        order_count: Buyurtmalar soni (1, 5, 10, 20...)
        order_ids: Buyurtma ID lari ro'yxati (log uchun)
        language: Til kodi (uz, ru, en)

    Returns:
        True: Qo'ng'iroq muvaffaqiyatli
        False: Xatolik (audio yo'q yoki qo'ng'iroq xatosi)
    """
    try:
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number

        # 1. AVVAL audio tayyorlash va tekshirish (til bo'yicha)
        audio_name = await prepare_audio_for_call(order_count, language)

        if not audio_name:
            logger.error(f"⚠️ QO'NG'IROQ QILINMADI - Audio mavjud emas: {order_count} ta buyurtma, {phone_number}")
            return False

        # 2. Audio tayyor - qo'ng'iroq qilish
        cmd = f"wsl -u root -e bash -c \"asterisk -rx 'channel originate PJSIP/{phone_number}@{SARKOR_ENDPOINT} extension {audio_name}@autodialer-dynamic'\""

        logger.info(f"Qo'ng'iroq qilinmoqda: {phone_number}, {order_count} ta buyurtma, IDs: {order_ids}, Audio: {audio_name}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"✅ Qo'ng'iroq muvaffaqiyatli: {phone_number}, {order_count} ta buyurtma")
            return True
        else:
            logger.error(f"❌ Qo'ng'iroq xatosi: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Qo'ng'iroq xatosi: {e}")
        return False


def send_telegram_message(message, order_id=None):
    """Telegram orqali xabar yuborish va message_id qaytarish"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            result = response.json()
            message_id = result.get('result', {}).get('message_id')
            logger.info(f"Telegram xabari yuborildi, message_id: {message_id}")

            if order_id and message_id:
                order_messages[order_id] = message_id

            return message_id
        else:
            logger.error(f"Telegram xatosi: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Telegram xatosi: {e}")
        return None


def format_seller_orders_message(seller_orders, call_attempts=0):
    """
    Sotuvchi buyurtmalari uchun professional Telegram xabar formati
    Shablon: S-Kafe namunasi asosida
    HAR BIR SOTUVCHI UCHUN ALOHIDA XABAR!
    """
    seller_name = seller_orders.get("seller_name", "Noma'lum")
    seller_phone = seller_orders.get("seller_phone", "Noma'lum")
    # Sotuvchi telefon raqamini formatlash
    if seller_phone and not str(seller_phone).startswith('+'):
        seller_phone = '+' + str(seller_phone)
    seller_address = seller_orders.get("seller_address", "Noma'lum")
    delivery_time = seller_orders.get("delivery_time", "")
    orders = seller_orders.get("orders", [])
    orders_count = len(orders)

    # Umumiy narx
    total_price = 0
    for o in orders:
        price = o.get("price") or o.get("narx", 0)
        if isinstance(price, str):
            price = price.replace(",", "").replace(" ", "")
            try:
                price = float(price)
            except:
                price = 0
        total_price += price or 0

    total_price_str = f"{total_price:,.0f}".replace(",", " ") + " so'm"

    # Header
    text = f"""🚨 <b>DIQQAT! {orders_count} ta buyurtma qabul qilinmadi!</b>

<b>SOTUVCHI:</b>
  Nomi: {seller_name}
  Tel: {seller_phone}
  Manzil: {seller_address}"""

    if delivery_time:
        text += f"\n  Yetkazish vaqti: {delivery_time}"

    # Buyurtmalar bo'limi
    text += "\n\n<b>━━━ BUYURTMALAR ━━━</b>\n"

    for i, order in enumerate(orders[:10], 1):
        order_number = order.get("order_number") or order.get("lead_id", "N/A")
        client_name = order.get("client_name") or order.get("mijoz_nomi", "Noma'lum")
        client_phone = order.get("client_phone") or order.get("mijoz_tel", "Noma'lum")
        # Telefon raqamini formatlash
        if client_phone and not str(client_phone).startswith('+'):
            client_phone = '+' + str(client_phone)
        price = order.get("price") or order.get("narx", 0)

        # Narxni formatlash
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
                prod_name = prod.get('name', 'Noma\'lum')
                prod_qty = prod.get('quantity', 1)
                prod_price = prod.get('price', 0)
                if isinstance(prod_price, (int, float)) and prod_price:
                    prod_price_str = f"{prod_price:,.0f}".replace(",", " ")
                else:
                    prod_price_str = "0"
                text += f"      {idx}. {prod_name} x{prod_qty} ({prod_price_str} so'm)\n"
        else:
            # Eski format uchun orqaga moslik
            product_name = order.get("product_name") or order.get("mahsulot", "Noma'lum")
            quantity = order.get("quantity") or order.get("miqdor", 1)
            text += f"   📦 Mahsulot: {product_name} x{quantity}\n"

    if orders_count > 10:
        text += f"\n... va yana {orders_count - 10} ta buyurtma\n"

    # Footer
    text += f"""
<b>━━━━━━━━━━━━━━━━━━━━━</b>
📦 Jami: <b>{orders_count}</b> ta buyurtma
💰 Umumiy: <b>{total_price_str}</b>

❌ Buyurtmalarni qabul qilmayapti!
📞 {call_attempts} marta qo'ng'iroq qilindi.
🔴 Zudlik bilan bog'laning!

📱 <a href="https://test.nonbor.uz">Buyurtmalarni ko'rish</a>"""

    return text


def send_seller_orders_alert(seller_orders, call_attempts=0):
    """
    Sotuvchi buyurtmalari haqida professional Telegram xabar yuborish
    MUHIM: Har bir sotuvchi uchun ALOHIDA xabar yuboriladi!
    Message ID va buyurtmalar sotuvchi bo'yicha saqlanadi (status o'zgarganda tahrirlash uchun)
    """
    seller_phone = seller_orders.get("seller_phone", "Noma'lum")
    orders_count = len(seller_orders.get("orders", []))

    logger.info(f"📨 Telegram xabar yuborilmoqda: Sotuvchi {seller_phone}, {orders_count} ta buyurtma")

    message = format_seller_orders_message(seller_orders, call_attempts)

    # Birinchi buyurtma ID sini olish (xabar ID ni saqlash uchun)
    orders = seller_orders.get("orders", [])
    first_order_id = orders[0].get("lead_id") if orders else None

    result = send_telegram_message(message, order_id=first_order_id)

    if result:
        # Message ID ni sotuvchi bo'yicha saqlash
        seller_messages[seller_phone] = result
        # Buyurtmalar ma'lumotlarini saqlash (xabar tahrirlash uchun)
        seller_orders_data[seller_phone] = {
            "seller_orders": seller_orders,
            "call_attempts": call_attempts,
            "created_at": datetime.now()
        }
        logger.info(f"✅ Telegram xabar yuborildi: Sotuvchi {seller_phone}, message_id: {result}")
    else:
        logger.error(f"❌ Telegram xabar yuborilmadi: Sotuvchi {seller_phone}")

    return result


def delete_telegram_message(order_id):
    """Telegram xabarni o'chirish"""
    try:
        message_id = order_messages.get(order_id)
        if not message_id:
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            logger.info(f"Telegram xabari o'chirildi, message_id: {message_id}")
            del order_messages[order_id]
            return True
        else:
            logger.error(f"Telegram o'chirish xatosi: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram o'chirish xatosi: {e}")
        return False


def edit_telegram_message(message_id, new_text):
    """Telegram xabarni tahrirlash"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            logger.info(f"Telegram xabari yangilandi, message_id: {message_id}")
            return True
        else:
            logger.error(f"Telegram tahrirlash xatosi: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram tahrirlash xatosi: {e}")
        return False


def delete_seller_telegram_message(seller_phone):
    """Sotuvchi uchun yuborilgan Telegram xabarni o'chirish"""
    try:
        message_id = seller_messages.get(seller_phone)
        if not message_id:
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
        data = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            logger.info(f"Sotuvchi {seller_phone} Telegram xabari o'chirildi")
            del seller_messages[seller_phone]
            return True
        else:
            logger.error(f"Telegram o'chirish xatosi: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram o'chirish xatosi: {e}")
        return False


def update_seller_telegram_on_status_change(seller_phone, order_id, new_status):
    """
    Buyurtma statusi o'zgarganda:
    1. Eski Telegram xabarni O'CHIRISH
    2. Yangi xabarni QAYTA YUBORISH (yangilangan buyurtmalar ro'yxati bilan)

    Bu usul "tahrirlash" o'rniga "o'chirish + qayta yuborish" qiladi
    """
    try:
        # Agar buyurtma qabul qilingan bo'lsa - telefon javob berdi deb belgilash
        if new_status in [ORDER_STATUS_ACCEPTED, ORDER_STATUS_READY, ORDER_STATUS_DELIVERING, ORDER_STATUS_COMPLETED]:
            phone_call_answered[seller_phone] = True
            logger.info(f"Telefon {seller_phone}: Javob berdi (status: {new_status})")

        message_id = seller_messages.get(seller_phone)

        # Saqlangan buyurtmalar ma'lumotini olish
        saved_data = seller_orders_data.get(seller_phone)
        if not saved_data:
            logger.debug(f"Sotuvchi {seller_phone} uchun buyurtmalar ma'lumoti topilmadi")
            return False

        seller_orders = saved_data.get("seller_orders", {})
        call_attempts = saved_data.get("call_attempts", 0)
        orders = seller_orders.get("orders", [])

        # O'zgargan buyurtmani topish (order_id - amoCRM lead ID yoki order_number)
        changed_order = None
        for o in orders:
            if o.get("lead_id") == order_id or str(o.get("order_number")) == str(order_id):
                changed_order = o
                break

        order_number = changed_order.get("order_number") if changed_order else order_id

        # Qabul qilingan/rad etilgan buyurtmani ro'yxatdan olib tashlash
        updated_orders = [o for o in orders if o.get("lead_id") != order_id and str(o.get("order_number")) != str(order_id)]

        # 1. ESKI XABARNI O'CHIRISH
        if message_id:
            logger.info(f"Sotuvchi {seller_phone}: Eski xabar o'chirilmoqda (message_id: {message_id})")
            delete_seller_telegram_message(seller_phone)

        # 2. AGAR BUYURTMA QOLMASA - TOZALASH (xabar yuborilmaydi)
        if len(updated_orders) == 0:
            logger.info(f"Sotuvchi {seller_phone}: Barcha buyurtmalar qabul qilindi")
            # Ma'lumotlarni tozalash
            if seller_phone in seller_orders_data:
                del seller_orders_data[seller_phone]
            if seller_phone in seller_messages:
                del seller_messages[seller_phone]
            return True

        # 3. QOLGAN BUYURTMALAR BILAN YANGI XABAR YUBORISH
        logger.info(f"Sotuvchi {seller_phone}: {len(orders)} → {len(updated_orders)} ta buyurtma qoldi, YANGI xabar yuborilmoqda")

        # Yangilangan seller_orders
        updated_seller_orders = seller_orders.copy()
        updated_seller_orders["orders"] = updated_orders

        # Yangi xabar matni
        new_message = format_seller_orders_message(updated_seller_orders, call_attempts)

        # YANGI XABAR YUBORISH (tahrirlash emas!)
        new_message_id = send_telegram_message(new_message)

        if new_message_id:
            # Yangi message_id ni saqlash
            seller_messages[seller_phone] = new_message_id
            # Saqlangan ma'lumotni yangilash
            seller_orders_data[seller_phone] = {
                "seller_orders": updated_seller_orders,
                "call_attempts": call_attempts,
                "created_at": datetime.now()
            }
            logger.info(f"✅ YANGI Telegram xabar yuborildi: {len(updated_orders)} ta buyurtma qoldi (message_id: {new_message_id})")
            return True
        else:
            logger.error(f"❌ Yangi Telegram xabar yuborib bo'lmadi")
            return False

    except Exception as e:
        logger.error(f"Telegram yangilash xatosi: {e}")
        return False


def convert_amocrm_lead_to_order(lead):
    """
    amoCRM leadni Nonbor order formatiga o'tkazish

    amoCRM Lead: {id, name, price, status_id, created_at, ...}
    Nonbor Order: {id, state, business: {title}, user: {phone, first_name}, ...}
    """
    # Lead nomidan order ID va mijoz nomini olish
    # Format: "#1755 | Ixtiyor Suyunov | CASH | 202 351"
    lead_name = lead.get('name', '')
    parts = lead_name.split('|')

    order_number = ''
    mijoz_nomi = ''
    tolov = 'CASH'

    if len(parts) >= 1:
        order_number = parts[0].strip().replace('#', '')
    if len(parts) >= 2:
        mijoz_nomi = parts[1].strip()
    if len(parts) >= 3:
        tolov = parts[2].strip()

    # Notes dan biznes ma'lumotlarini olish
    business_info = parse_business_info(lead.get('id'))

    # Nonbor formatiga o'tkazish
    order = {
        'id': lead.get('id'),  # amoCRM lead ID
        'order_number': order_number,  # Nonbor order number
        'state': ORDER_STATUS_CHECKING,  # Tekshirilmoqda = CHECKING
        'status_id': lead.get('status_id'),
        'created_at': datetime.fromtimestamp(lead.get('created_at', 0)).isoformat() if lead.get('created_at') else None,
        'total_price': lead.get('price', 0),
        'payment_method': tolov,
        'delivery_method': business_info.get('yetkazish') or 'DELIVERY',
        'business': {
            'title': business_info.get('biznes_nomi') or 'Noma\'lum',
            'address': business_info.get('biznes_manzil') or '',
            'phone': business_info.get('biznes_tel'),
        },
        'user': {
            'phone': business_info.get('biznes_tel'),  # Sotuvchi telefoni
            'first_name': mijoz_nomi,
            'last_name': '',
        },
        'order_item': [{
            'product': {
                'name': business_info.get('mahsulot') or 'Mahsulot'
            }
        }] if business_info.get('mahsulot') else [],
        # amoCRM specific
        'lead_id': lead.get('id'),
        'amocrm_lead': True,  # Flag: bu amoCRM dan kelgan
    }

    return order


def get_all_orders():
    """
    Nonbor API dan CHECKING statusidagi buyurtmalarni olish

    MUHIM: Faqat CHECKING statusidagi buyurtmalar qaytariladi!
    Bu qo'ng'iroq qilish kerak bo'lgan buyurtmalar.
    """
    try:
        response = requests.get(NONBOR_API_URL, headers=get_nonbor_headers(), timeout=30)

        if response.status_code == 200:
            data = response.json()

            # Yangi API format: {success: true, result: {results: [...]}}
            if isinstance(data, dict) and data.get('success'):
                orders = data.get('result', {}).get('results', [])
            # Eski format: to'g'ridan-to'g'ri list
            elif isinstance(data, list):
                orders = data
            else:
                orders = []

            # Faqat CHECKING statusdagi orderlarni filtr qilish
            checking_orders = [o for o in orders if o.get('state') == ORDER_STATUS_CHECKING]
            logger.info(f"CHECKING: {len(checking_orders)} ta buyurtma topildi")
            return checking_orders
        else:
            logger.error(f"Nonbor API xatosi: {response.status_code} - {response.text}")
            return []

    except Exception as e:
        logger.error(f"Nonbor API xatosi: {e}")
        return []


def get_all_orders_from_nonbor():
    """Nonbor API dan BARCHA buyurtmalarni olish (debug uchun)"""
    try:
        response = requests.get(NONBOR_API_URL, headers=get_nonbor_headers(), timeout=30)

        if response.status_code == 200:
            data = response.json()
            # Yangi API format
            if isinstance(data, dict) and data.get('success'):
                return data.get('result', {}).get('results', [])
            elif isinstance(data, list):
                return data
            return []
        else:
            logger.error(f"Nonbor API xatosi: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        logger.error(f"Nonbor API xatosi: {e}")
        return []


def get_amocrm_headers():
    """amoCRM API uchun headerlar"""
    return {
        "Authorization": f"Bearer {AMOCRM_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }


def get_amocrm_tekshirilmoqda_orders():
    """
    amoCRM dan "Tekshirilmoqda" ustunidagi buyurtmalarni olish

    Returns:
        list: Tekshirilmoqda ustunidagi leadlar ro'yxati
    """
    if not AMOCRM_ACCESS_TOKEN:
        logger.warning("amoCRM access token yo'q! AMOCRM_ACCESS_TOKEN ni to'ldiring.")
        return []

    try:
        # amoCRM Leads API - "Tekshirilmoqda" statusidagi leadlarni olish
        url = f"https://{AMOCRM_DOMAIN}/api/v4/leads"
        params = {
            "filter[statuses][0][pipeline_id]": AMOCRM_PIPELINE_ID,
            "filter[statuses][0][status_id]": AMOCRM_STATUS_TEKSHIRILMOQDA,
            "limit": 250  # Maksimum 250 ta
        }

        response = requests.get(url, headers=get_amocrm_headers(), params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            leads = data.get('_embedded', {}).get('leads', [])
            logger.info(f"amoCRM Tekshirilmoqda: {len(leads)} ta buyurtma topildi")
            return leads
        elif response.status_code == 204:
            # No content - buyurtma yo'q
            logger.info("amoCRM Tekshirilmoqda: 0 ta buyurtma")
            return []
        elif response.status_code == 401:
            logger.error("amoCRM: Unauthorized - token yaroqsiz yoki muddati o'tgan")
            return []
        else:
            logger.error(f"amoCRM API xatosi: {response.status_code} - {response.text}")
            return []

    except Exception as e:
        logger.error(f"amoCRM API xatosi: {e}")
        return []


def get_checking_orders():
    """
    Nonbor API dan CHECKING statusidagi buyurtmalarni olish

    API yangi formati: {success: true, result: {results: [...]}}
    """
    try:
        response = requests.get(NONBOR_API_URL, headers=get_nonbor_headers(), timeout=30)

        if response.status_code == 200:
            data = response.json()

            # Yangi API format: {success: true, result: {results: [...]}}
            if isinstance(data, dict) and data.get('success'):
                orders = data.get('result', {}).get('results', [])
            # Eski format: to'g'ridan-to'g'ri list
            elif isinstance(data, list):
                orders = data
            else:
                orders = []

            # Faqat CHECKING statusdagi orderlarni filtr qilish
            checking_orders_list = [o for o in orders if o.get('state') == ORDER_STATUS_CHECKING]
            logger.info(f"CHECKING: {len(checking_orders_list)} ta buyurtma topildi")
            return checking_orders_list
        else:
            logger.error(f"Nonbor API xatosi: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        logger.error(f"Nonbor API xatosi: {e}")
        return []


def get_order_status(order_id):
    """Buyurtma statusini tekshirish"""
    try:
        response = requests.get(NONBOR_API_URL, headers=get_nonbor_headers(), timeout=30)
        if response.status_code == 200:
            data = response.json()

            # API formatini to'g'ri parse qilish
            if isinstance(data, dict) and data.get('success'):
                orders = data.get('result', {}).get('results', [])
            elif isinstance(data, list):
                orders = data
            else:
                return None

            for order in orders:
                if isinstance(order, dict) and order.get('id') == order_id:
                    return order.get('state')
        return None
    except Exception as e:
        logger.error(f"Order status tekshirish xatosi: {e}")
        return None


def get_lead_contact_info(lead):
    """
    Leaddan mijoz (contact) ma'lumotlarini olish

    Returns:
        dict: {
            'mijoz_nomi': str,
            'mijoz_tel': str,
            'mijoz_email': str
        }
    """
    contact_info = {
        'mijoz_nomi': None,
        'mijoz_tel': None,
        'mijoz_email': None
    }

    try:
        # Lead nomidan olish (ko'pincha "Mijoz nomi | ..." formatda)
        lead_name = lead.get('name', '')
        if lead_name:
            # Agar "|" bo'lsa, birinchi qism mijoz nomi
            if '|' in lead_name:
                contact_info['mijoz_nomi'] = lead_name.split('|')[0].strip()
            else:
                contact_info['mijoz_nomi'] = lead_name

        # Leadning o'zidan custom field ni tekshirish
        custom_fields = lead.get('custom_fields_values', []) or []
        for field in custom_fields:
            field_code = field.get('field_code', '')
            field_name = field.get('field_name', '').lower()
            values = field.get('values', [])

            if (field_code == 'PHONE' or 'phone' in field_name or 'telefon' in field_name) and values:
                contact_info['mijoz_tel'] = values[0].get('value')
            elif (field_code == 'EMAIL' or 'email' in field_name) and values:
                contact_info['mijoz_email'] = values[0].get('value')

        # Contactdan olish
        contacts = lead.get('_embedded', {}).get('contacts', [])
        if contacts:
            contact_id = contacts[0].get('id')
            contact_url = f"https://{AMOCRM_DOMAIN}/api/v4/contacts/{contact_id}"
            response = requests.get(contact_url, headers=get_amocrm_headers())

            if response.status_code == 200:
                contact_data = response.json()

                # Contact nomi
                if not contact_info['mijoz_nomi']:
                    contact_info['mijoz_nomi'] = contact_data.get('name')

                # Contact fields
                contact_fields = contact_data.get('custom_fields_values', []) or []
                for field in contact_fields:
                    field_code = field.get('field_code', '')
                    values = field.get('values', [])

                    if field_code == 'PHONE' and values and not contact_info['mijoz_tel']:
                        contact_info['mijoz_tel'] = values[0].get('value')
                    elif field_code == 'EMAIL' and values and not contact_info['mijoz_email']:
                        contact_info['mijoz_email'] = values[0].get('value')

        return contact_info

    except Exception as e:
        logger.error(f"Mijoz ma'lumotlari olish xatosi: {e}")
        return contact_info


def get_lead_phone(lead):
    """Leaddan telefon raqamini olish"""
    try:
        contact_info = get_lead_contact_info(lead)
        return contact_info.get('mijoz_tel')
    except Exception as e:
        logger.error(f"Telefon olish xatosi: {e}")
        return None


def check_lead_status(lead_id):
    """Leadning hozirgi statusini tekshirish"""
    try:
        url = f"https://{AMOCRM_DOMAIN}/api/v4/leads/{lead_id}"
        response = requests.get(url, headers=get_amocrm_headers())

        if response.status_code == 200:
            lead = response.json()
            return lead.get('status_id')
        return None
    except Exception as e:
        logger.error(f"Status tekshirish xatosi: {e}")
        return None


def update_lead_status(lead_id, new_status_id):
    """Leadning statusini yangilash"""
    try:
        url = f"https://{AMOCRM_DOMAIN}/api/v4/leads/{lead_id}"
        data = {
            "status_id": new_status_id
        }
        response = requests.patch(url, headers=get_amocrm_headers(), json=data)

        if response.status_code == 200:
            logger.info(f"Lead #{lead_id} statusi yangilandi: {new_status_id}")
            return True
        else:
            logger.error(f"Status yangilash xatosi: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Status yangilash xatosi: {e}")
        return False


def get_lead_notes(lead_id):
    """Lead notes/comments olish"""
    try:
        url = f"https://{AMOCRM_DOMAIN}/api/v4/leads/{lead_id}/notes"
        response = requests.get(url, headers=get_amocrm_headers())

        if response.status_code == 200:
            data = response.json()
            return data.get('_embedded', {}).get('notes', [])
        return []
    except Exception as e:
        logger.error(f"Notes olish xatosi: {e}")
        return []


def parse_business_info(lead_id):
    """
    Notes dan biznes ma'lumotlarini parse qilish

    amoCRM Notes formati (screenshotdan):
    BUYURTMA TAFSILOTLARI
    Order ID: #1610
    Narx: 1,000,100 so'm
    Sana: 15.01.2026 14:32
    To'lov: CASH
    Yetkazib berish: DELIVERY
    Manzil: Tashkent, Ahmad Donish Street
    Yetkazish vaqti: 15.01.2026 14:44

    MAHSULOTLAR:
    1. Chust oshi 1
       Miqdor: 1 ta
       Narx: 1,000,000 so'm

    BIZNES:
    Nomi: Doniyorbek oshxonasi
    Tel: 998930824736
    Manzil: O'zbekiston, Toshkent, Qibray ko'chasi, 57
    ...
    """
    business_info = {
        'biznes_nomi': None,
        'biznes_tel': None,
        'biznes_manzil': None,
        'order_id': None,
        'mahsulot': None,
        'miqdor': None,
        'narx': None,
        'tolov': None,
        'yetkazish': None,
        'mijoz_nomi': None,
        'mijoz_tel': None,
        'mijoz_manzil': None,
        'yetkazish_vaqti': None
    }

    notes = get_lead_notes(lead_id)

    for note in notes:
        text = note.get('params', {}).get('text', '') or ''

        # Order ID: #1610
        order_match = re.search(r'Order ID:\s*#?(\d+)', text)
        if order_match:
            business_info['order_id'] = order_match.group(1).strip()

        # BIZNES bo'limidan ma'lumotlar
        # Nomi: Doniyorbek oshxonasi
        biznes_section = re.search(r'BIZNES:(.+?)(?:MUOZ:|$)', text, re.DOTALL)
        if biznes_section:
            biznes_text = biznes_section.group(1)

            # Nomi
            nomi_match = re.search(r'Nomi:\s*(.+?)(?:\n|$)', biznes_text)
            if nomi_match:
                business_info['biznes_nomi'] = nomi_match.group(1).strip()

            # Tel
            tel_match = re.search(r'Tel:\s*(\d+)', biznes_text)
            if tel_match:
                business_info['biznes_tel'] = tel_match.group(1).strip()

            # Manzil
            manzil_match = re.search(r'Manzil:\s*(.+?)(?:\n|$)', biznes_text)
            if manzil_match:
                business_info['biznes_manzil'] = manzil_match.group(1).strip()

        # Agar BIZNES bo'limi topilmasa, oddiy qidirish
        if not business_info['biznes_nomi']:
            nomi_match = re.search(r'Nomi:\s*(.+?)(?:\n|Tel:|$)', text)
            if nomi_match:
                business_info['biznes_nomi'] = nomi_match.group(1).strip()

        if not business_info['biznes_tel']:
            tel_match = re.search(r'Tel:\s*(\d+)', text)
            if tel_match:
                business_info['biznes_tel'] = tel_match.group(1).strip()

        # Mahsulot (1. Chust oshi 1)
        mahsulot_match = re.search(r'\d+\.\s*(.+?)(?:\n\s+Miqdor:|$)', text)
        if mahsulot_match:
            business_info['mahsulot'] = mahsulot_match.group(1).strip()

        # Miqdor: 1 ta
        miqdor_match = re.search(r'Miqdor:\s*(\d+)', text)
        if miqdor_match:
            business_info['miqdor'] = miqdor_match.group(1).strip()

        # Narx: 1,000,100 so'm (BUYURTMA TAFSILOTLARI dan)
        narx_match = re.search(r'Narx:\s*([\d,.\s]+)\s*so', text)
        if narx_match:
            business_info['narx'] = narx_match.group(1).strip()

        # To'lov: CASH
        tolov_match = re.search(r"To'lov:\s*(\w+)", text)
        if tolov_match:
            business_info['tolov'] = tolov_match.group(1).strip()

        # Yetkazib berish: DELIVERY
        yetkazish_match = re.search(r'Yetkazib berish:\s*(\w+)', text)
        if yetkazish_match:
            business_info['yetkazish'] = yetkazish_match.group(1).strip()

        # Mijoz manzili
        mijoz_manzil_match = re.search(r'Manzil:\s*(.+?)(?:\nYetkazish|$)', text)
        if mijoz_manzil_match:
            business_info['mijoz_manzil'] = mijoz_manzil_match.group(1).strip()

        # Yetkazish vaqti
        yetkazish_vaqti_match = re.search(r'Yetkazish vaqti:\s*(.+?)(?:\n|$)', text)
        if yetkazish_vaqti_match:
            business_info['yetkazish_vaqti'] = yetkazish_vaqti_match.group(1).strip()

    return business_info


async def process_order(order_data):
    """Buyurtmani qayta ishlash"""
    order_id = order_data.get('order_id')
    seller_phone = order_data.get('seller_phone')
    seller_name = order_data.get('seller_name', 'Sotuvchi')
    created_at = datetime.fromisoformat(order_data.get('created_at'))
    business_info = order_data.get('business_info', {})

    logger.info(f"Buyurtma #{order_id} qayta ishlanmoqda...")

    # 90 sekund kutish
    logger.info(f"Buyurtma #{order_id}: 90 sek kutilmoqda...")
    await asyncio.sleep(WAIT_BEFORE_CALL)

    # Status o'zgarganmi tekshirish
    current_status = check_lead_status(order_id)
    if current_status and current_status != AMOCRM_STATUS_TEKSHIRILMOQDA:
        logger.info(f"Buyurtma #{order_id} statusi o'zgardi: {current_status}")
        if order_id in pending_orders:
            del pending_orders[order_id]
        return

    # 2 marta qo'ng'iroq qilish (IVR bilan)
    for attempt in range(1, MAX_RETRIES + 1):
        # Har safar statusni tekshirish
        current_status = check_lead_status(order_id)
        if current_status and current_status != AMOCRM_STATUS_TEKSHIRILMOQDA:
            logger.info(f"Buyurtma #{order_id} qabul qilindi!")
            if order_id in pending_orders:
                del pending_orders[order_id]
            return

        # IVR result tekshirish
        if order_id in call_results:
            result = call_results[order_id]
            logger.info(f"Buyurtma #{order_id} IVR natija: {result}")
            if result == 'accepted':
                update_lead_status(order_id, AMOCRM_STATUS_QABUL_QILINDI)
                delete_telegram_message(order_id)
            elif result == 'rejected':
                update_lead_status(order_id, AMOCRM_STATUS_RAD_ETILDI)
            del call_results[order_id]
            if order_id in pending_orders:
                del pending_orders[order_id]
            return

        logger.info(f"Buyurtma #{order_id}: {attempt}-qo'ng'iroq (IVR)")
        make_call(seller_phone, order_id=order_id)

        # Qo'ng'iroq javobini kutish
        await asyncio.sleep(CALL_WAIT_TIME)

        # IVR result tekshirish
        if order_id in call_results:
            result = call_results[order_id]
            logger.info(f"Buyurtma #{order_id} IVR natija: {result}")
            if result == 'accepted':
                update_lead_status(order_id, AMOCRM_STATUS_QABUL_QILINDI)
                delete_telegram_message(order_id)
                send_telegram_message(f"✅ Buyurtma #{order_id} qabul qilindi!", order_id=None)
            elif result == 'rejected':
                update_lead_status(order_id, AMOCRM_STATUS_RAD_ETILDI)
                send_telegram_message(f"❌ Buyurtma #{order_id} rad etildi!", order_id=None)
            del call_results[order_id]
            if order_id in pending_orders:
                del pending_orders[order_id]
            return

    # 4 daqiqa to'lguncha kutish
    elapsed = (datetime.now() - created_at).total_seconds()
    remaining = TELEGRAM_ALERT_TIME - elapsed

    if remaining > 0:
        logger.info(f"Buyurtma #{order_id}: Telegram xabar uchun {remaining:.0f} sek kutilmoqda...")
        await asyncio.sleep(remaining)

    # Oxirgi marta statusni tekshirish
    current_status = check_lead_status(order_id)
    if current_status and current_status != AMOCRM_STATUS_TEKSHIRILMOQDA:
        logger.info(f"Buyurtma #{order_id} qabul qilindi!")
        if order_id in pending_orders:
            del pending_orders[order_id]
        return

    # Oxirgi marta IVR result tekshirish
    if order_id in call_results:
        result = call_results[order_id]
        if result in ['accepted', 'rejected']:
            if order_id in pending_orders:
                del pending_orders[order_id]
            return

    # Hali ham TEKSHIRILMOQDA - Telegram xabar yuborish
    logger.warning(f"Buyurtma #{order_id}: 2.5 daqiqa o'tdi, status o'zgarmadi!")

    # Biznes ma'lumotlarini formatlash - Professional format (autodialer-pro dan)
    biznes_nomi = business_info.get('biznes_nomi') or 'Noma\'lum'
    biznes_tel = business_info.get('biznes_tel') or seller_phone
    mahsulot = business_info.get('mahsulot') or '-'
    miqdor = business_info.get('miqdor') or '1'
    narx = business_info.get('narx') or '-'

    # Professional format - sotuvchi va buyurtmalar
    seller_orders = {
        "seller_name": biznes_nomi,
        "seller_phone": biznes_tel,
        "seller_address": "Noma'lum",
        "orders": [
            {
                "lead_id": order_id,
                "order_number": order_id,
                "client_name": seller_name,
                "client_phone": seller_phone,
                "product_name": mahsulot,
                "miqdor": miqdor,
                "quantity": miqdor,
                "price": narx,
                "narx": narx
            }
        ]
    }

    # Professional Telegram xabar yuborish
    send_seller_orders_alert(seller_orders, call_attempts=MAX_RETRIES)

    if order_id in pending_orders:
        del pending_orders[order_id]


# ============ SOTUVCHI BUYURTMALARINI GURUHLASH ============
seller_order_groups = {}  # {seller_phone: [order_ids]}
seller_last_call = {}  # {seller_phone: datetime} - oxirgi qo'ng'iroq vaqti


async def process_seller_orders(seller_phone, order_ids, business_info, language=None):
    """
    Bir sotuvchining barcha buyurtmalarini qayta ishlash
    Buyurtmalar soni aytib qo'ng'iroq qiladi

    MUHIM: Har bir sotuvchi MUSTAQIL ishlaydi (parallel)
    - 90 sek kutadi
    - Muddat yetganda qo'ng'iroq qiladi
    - Boshqa sotuvchilarni KUTMAYDI

    Args:
        language: Sotuvchi ilova tili (uz, ru, en)
    """
    # Til aniqlash
    seller_language = language or business_info.get('language') or DEFAULT_LANGUAGE
    logger.info(f"Sotuvchi {seller_phone}: Til = {seller_language}")

    order_count = len(order_ids)
    logger.info(f"Sotuvchi {seller_phone}: {order_count} ta buyurtma qayta ishlanmoqda...")

    # 90 sekund kutish - BU TASK MUSTAQIL, boshqalarni bloklamaydi
    logger.info(f"Sotuvchi {seller_phone}: 90 sek kutilmoqda...")
    await asyncio.sleep(WAIT_BEFORE_CALL)
    logger.info(f"Sotuvchi {seller_phone}: Muddat yetdi, qo'ng'iroq qilinmoqda!")

    # Yangi buyurtmalar qo'shilganmi tekshirish
    current_ids = seller_order_groups.get(seller_phone, [])
    if len(current_ids) > order_count:
        order_ids = current_ids
        order_count = len(order_ids)
        logger.info(f"Sotuvchi {seller_phone}: Yangi buyurtmalar qo'shildi, jami {order_count} ta")

    # Barcha buyurtmalar statusini tekshirish
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

    # 2 marta qo'ng'iroq qilish (buyurtmalar soni bilan)
    call_answered = False
    answered_at_attempt = 0

    for attempt in range(1, MAX_RETRIES + 1):
        # Har safar statusni tekshirish
        still_pending = []
        for oid in active_order_ids:
            status = get_order_status(oid)
            if status and status == ORDER_STATUS_CHECKING:
                still_pending.append(oid)

        if not still_pending:
            logger.info(f"Sotuvchi {seller_phone}: Barcha buyurtmalar qabul qilindi!")
            call_answered = True
            answered_at_attempt = attempt
            break

        order_count = len(still_pending)
        logger.info(f"Sotuvchi {seller_phone}: {attempt}-qo'ng'iroq, {order_count} ta buyurtma")

        # Dinamik audio bilan qo'ng'iroq (til bo'yicha)
        call_success = await make_call_with_count(seller_phone, order_count, still_pending, seller_language)

        # Qo'ng'iroq statistikasini yozish (faqat qo'ng'iroq muvaffaqiyatli bo'lsa)
        if call_success:
            # Audio to'liq ijro etilishi uchun kutish (ringing + audio)
            logger.info(f"Sotuvchi {seller_phone}: Audio ijro etilmoqda, {AUDIO_PLAY_TIME} sek kutilmoqda...")
            await asyncio.sleep(AUDIO_PLAY_TIME)

            # Qo'ng'iroqdan keyin buyurtma holati tekshirish (bir necha marta)
            orders_accepted_after_call = 0
            for check_attempt in range(3):  # 3 marta tekshirish
                for oid in still_pending:
                    new_status = get_order_status(oid)
                    if new_status and new_status != ORDER_STATUS_CHECKING:
                        orders_accepted_after_call += 1
                        phone_call_answered[seller_phone] = True  # Telefon javob berdi

                if orders_accepted_after_call > 0:
                    break  # Qabul qilindi - tekshirishni to'xtatish

                # Qisqa kutish va qayta tekshirish
                if check_attempt < 2:
                    await asyncio.sleep(5)

            # Agar hech bo'lmasa bitta buyurtma qabul qilingan bo'lsa - javob berilgan
            if orders_accepted_after_call > 0 or phone_call_answered.get(seller_phone):
                record_call_statistic(answered=True, attempt=attempt)
                call_answered = True
                answered_at_attempt = attempt
                logger.info(f"Sotuvchi {seller_phone}: ✅ Javob berildi! Qayta qo'ng'iroq to'xtatildi")
                break  # Javob berildi - qayta qo'ng'iroq qilmaslik
            else:
                logger.info(f"Sotuvchi {seller_phone}: Javob berilmadi, keyingi urinish...")
                # Javob bermasa 10 sekdan keyin qayta qo'ng'iroq
                await asyncio.sleep(CALL_WAIT_TIME)
        else:
            await asyncio.sleep(CALL_WAIT_TIME)

        # Oxirgi qo'ng'iroq vaqtini saqlash
        seller_last_call[seller_phone] = datetime.now()

    # Agar 2 ta qo'ng'iroqdan keyin ham javob berilmagan bo'lsa
    if not call_answered:
        record_call_statistic(answered=False, attempt=MAX_RETRIES)

    # 2.5 daqiqa o'tgandan keyin Telegram xabar yuborish
    await asyncio.sleep(60)  # Qo'shimcha kutish

    # Oxirgi marta tekshirish
    final_pending = []
    for oid in active_order_ids:
        status = get_order_status(oid)
        if status and status == ORDER_STATUS_CHECKING:
            final_pending.append(oid)

    if final_pending:
        order_count = len(final_pending)
        logger.warning(f"Sotuvchi {seller_phone}: {order_count} ta buyurtma qabul qilinmadi!")

        # Telegram xabar yuborish - hali qabul qilinmagan buyurtmalar ro'yxati
        seller_orders = []
        for oid in final_pending:
            if oid in pending_orders:
                seller_orders.append(pending_orders[oid])

        if seller_orders:
            send_seller_orders_alert(seller_orders, call_attempts=MAX_RETRIES)

    # Guruhni tozalash
    if seller_phone in seller_order_groups:
        del seller_order_groups[seller_phone]

    # Qayta ishlangan buyurtmalarni pending dan o'chirish
    for oid in order_ids:
        if oid in pending_orders:
            del pending_orders[oid]

    logger.info(f"Sotuvchi {seller_phone}: Task tugadi, guruh tozalandi")


# ============ CLEANUP TASK - ESKI XABARLARNI TOZALASH ============
CLEANUP_INTERVAL = 300  # 5 daqiqada bir marta tozalash
MESSAGE_MAX_AGE = 600  # 10 daqiqadan eski xabarlarni tozalash


async def cleanup_task():
    """
    Eski/qolgan xabarlar va ma'lumotlarni davriy tozalash:
    - seller_messages va seller_orders_data (10 daqiqadan eski)
    - order_statuses, order_to_seller (pending_orders da yo'q)
    - phone_call_answered (1 soatdan eski)
    - Telegram xabarlarni o'chirish
    """
    logger.info("🧹 Cleanup task boshlandi...")

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)

            now = datetime.now()
            cleaned_count = 0

            # ===== 1. ESKI seller_orders_data va seller_messages TOZALASH =====
            sellers_to_remove = []
            for seller_phone, data in list(seller_orders_data.items()):
                created_at = data.get("created_at")

                if created_at:
                    age_seconds = (now - created_at).total_seconds()

                    # 10 daqiqadan eski
                    if age_seconds > MESSAGE_MAX_AGE:
                        sellers_to_remove.append(seller_phone)
                        logger.info(f"🧹 Sotuvchi {seller_phone}: {age_seconds:.0f} sek eski, tozalanmoqda")
                else:
                    # created_at yo'q bo'lsa, buyurtmalar sonini tekshirish
                    orders_list = data.get("seller_orders", {}).get("orders", [])
                    if not orders_list:
                        sellers_to_remove.append(seller_phone)
                        logger.info(f"🧹 Sotuvchi {seller_phone}: Buyurtmalar yo'q, tozalanmoqda")

            # Tozalash
            for seller_phone in sellers_to_remove:
                # Telegram xabarni O'CHIRISH
                if seller_phone in seller_messages:
                    message_id = seller_messages[seller_phone]
                    try:
                        delete_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
                        response = requests.post(delete_url, data={
                            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                            "message_id": message_id
                        }, timeout=10)
                        if response.status_code == 200:
                            logger.info(f"🧹 Telegram xabar o'chirildi: {seller_phone}")
                    except Exception as e:
                        logger.warning(f"🧹 Telegram o'chirish xatosi: {e}")
                    del seller_messages[seller_phone]

                # seller_orders_data dan o'chirish
                if seller_phone in seller_orders_data:
                    del seller_orders_data[seller_phone]

                cleaned_count += 1

            # ===== 2. ORDER_STATUSES va ORDER_TO_SELLER TOZALASH =====
            # pending_orders da yo'q va 5 daqiqadan eski statuslarni o'chirish
            statuses_to_remove = []
            for order_id in list(order_statuses.keys()):
                if order_id not in pending_orders:
                    statuses_to_remove.append(order_id)

            for order_id in statuses_to_remove:
                if order_id in order_statuses:
                    del order_statuses[order_id]
                if order_id in order_to_seller:
                    del order_to_seller[order_id]
                cleaned_count += 1

            # ===== 3. PHONE_CALL_ANSWERED TOZALASH (1 soatdan eski) =====
            # phone_call_answered ni tozalash - 1 soatdan keyin
            if len(phone_call_answered) > 100:
                # Juda ko'p bo'lsa, eng eskilerini o'chirish
                phone_call_answered.clear()
                logger.info(f"🧹 phone_call_answered tozalandi (> 100 ta)")
                cleaned_count += 1

            # ===== 4. PROCESSED_ORDERS TOZALASH =====
            # 1000 dan oshsa, eski qismini o'chirish
            if len(processed_orders) > 1000:
                # Set dan 500 ta o'chirish
                items_to_remove = list(processed_orders)[:500]
                for item in items_to_remove:
                    processed_orders.discard(item)
                logger.info(f"🧹 processed_orders tozalandi (500 ta o'chirildi)")
                cleaned_count += 1

            if cleaned_count > 0:
                logger.info(f"🧹 Cleanup tugadi: {cleaned_count} ta element tozalandi")

        except Exception as e:
            logger.error(f"🧹 Cleanup xatosi: {e}")
            await asyncio.sleep(60)  # Xatolikda 1 daqiqa kutish


def check_status_alerts():
    """
    180 sekundda statusi o'zgarmagan buyurtmalar uchun Telegram ogohlantirish
    Sotuvchi bo'yicha guruhlangan batafsil xabar yuboriladi
    Yangi buyurtma kelganda eski xabar o'chiriladi va yangilanadi
    """
    now = datetime.now()
    alerts_sent = 0

    # Sotuvchi bo'yicha BARCHA 180+ sekundlik buyurtmalarni yig'ish
    sellers_all_orders = {}  # {seller_phone: [ALL order_ids 180+ sek]}
    sellers_new_orders = {}  # {seller_phone: [NEW order_ids - hali alert yuborilmagan]}

    for order_id, first_seen in list(order_first_seen.items()):
        # Agar buyurtma hali pending_orders da bo'lsa
        if order_id in pending_orders:
            age_seconds = (now - first_seen).total_seconds()

            # 180 sekunddan oshgan
            if age_seconds >= STATUS_ALERT_TIMEOUT:
                order_data = pending_orders.get(order_id, {})
                seller_phone = order_data.get('seller_phone', 'Noma\'lum')

                # Barcha 180+ sek buyurtmalarni yig'ish
                if seller_phone not in sellers_all_orders:
                    sellers_all_orders[seller_phone] = []
                sellers_all_orders[seller_phone].append(order_id)

                # Faqat yangi (hali alert yuborilmagan) buyurtmalarni alohida belgilash
                if order_id not in status_alerted_orders:
                    if seller_phone not in sellers_new_orders:
                        sellers_new_orders[seller_phone] = []
                    sellers_new_orders[seller_phone].append(order_id)
        else:
            # Buyurtma pending_orders dan o'chirilgan - order_first_seen dan ham o'chirish
            if order_id in order_first_seen:
                del order_first_seen[order_id]
            if order_id in status_alerted_orders:
                status_alerted_orders.discard(order_id)

    # Har bir sotuvchi uchun batafsil alert yuborish (faqat yangi buyurtma bo'lsa)
    for seller_phone, new_order_ids in sellers_new_orders.items():
        # Bu sotuvchi uchun barcha 180+ sek buyurtmalar
        order_ids = sellers_all_orders.get(seller_phone, new_order_ids)
        # seller_orders_data dan ma'lumotlarni olish
        saved_data = seller_orders_data.get(seller_phone, {})
        seller_orders = saved_data.get("seller_orders", {})
        call_attempts = saved_data.get("call_attempts", 0)

        # Sotuvchi ma'lumotlari
        seller_name = seller_orders.get("seller_name", "Noma'lum")
        seller_address = seller_orders.get("seller_address", "Noma'lum")
        delivery_time = seller_orders.get("delivery_time", "")
        orders = seller_orders.get("orders", [])

        # Agar seller_orders bo'sh bo'lsa, pending_orders dan olish
        if not orders:
            orders = []
            for oid in order_ids:
                od = pending_orders.get(oid, {})
                bi = od.get('business_info', {})
                orders.append({
                    "order_number": oid,
                    "lead_id": oid,
                    "client_name": bi.get('mijoz_nomi', 'Noma\'lum'),
                    "client_phone": bi.get('mijoz_tel', 'Noma\'lum'),
                    "price": bi.get('narx', 0),
                    "products": bi.get('products', []),
                })
                if not seller_name or seller_name == "Noma'lum":
                    seller_name = bi.get('biznes_nomi', 'Noma\'lum')
                if not seller_address or seller_address == "Noma'lum":
                    seller_address = bi.get('biznes_manzil', 'Noma\'lum')

        orders_count = len(orders)
        if orders_count == 0:
            orders_count = len(order_ids)

        # Umumiy narx
        total_price = 0
        for o in orders:
            price = o.get("price") or o.get("narx", 0)
            if isinstance(price, str):
                price = price.replace(",", "").replace(" ", "")
                try:
                    price = float(price)
                except:
                    price = 0
            total_price += price or 0

        total_price_str = f"{total_price:,.0f}".replace(",", " ") + " so'm"

        # Sotuvchi telefon raqamini formatlash
        seller_phone_fmt = seller_phone
        if seller_phone_fmt and not str(seller_phone_fmt).startswith('+'):
            seller_phone_fmt = '+' + str(seller_phone_fmt)

        # Kutish vaqti (eng eski buyurtma)
        oldest_age = 0
        for oid in order_ids:
            if oid in order_first_seen:
                age = (now - order_first_seen[oid]).total_seconds()
                if age > oldest_age:
                    oldest_age = age

        # Batafsil xabar
        text = f"""🚨 <b>DIQQAT! {orders_count} ta buyurtma qabul qilinmadi!</b>

<b>SOTUVCHI:</b>
  Nomi: {seller_name}
  Tel: {seller_phone_fmt}
  Manzil: {seller_address}"""

        if delivery_time:
            text += f"\n  Yetkazish vaqti: {delivery_time}"

        # Buyurtmalar bo'limi
        text += "\n\n<b>━━━ BUYURTMALAR ━━━</b>\n"

        for i, order in enumerate(orders[:10], 1):
            order_number = order.get("order_number") or order.get("lead_id", "N/A")
            client_name = order.get("client_name") or order.get("mijoz_nomi", "Noma'lum")
            client_phone = order.get("client_phone") or order.get("mijoz_tel", "Noma'lum")
            if client_phone and not str(client_phone).startswith('+'):
                client_phone = '+' + str(client_phone)
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
            # Mahsulotlar
            products = order.get("products", [])
            if products:
                text += "   📦 Mahsulotlar:\n"
                for idx, prod in enumerate(products, 1):
                    prod_name = prod.get('name', 'Noma\'lum')
                    prod_qty = prod.get('quantity', 1)
                    prod_price = prod.get('price', 0)
                    if isinstance(prod_price, (int, float)) and prod_price:
                        prod_price_str = f"{prod_price:,.0f}".replace(",", " ")
                    else:
                        prod_price_str = "0"
                    text += f"      {idx}. {prod_name} x{prod_qty} ({prod_price_str} so'm)\n"

        if orders_count > 10:
            text += f"\n... va yana {orders_count - 10} ta buyurtma\n"

        # Footer
        text += f"""
<b>━━━━━━━━━━━━━━━━━━━━━</b>
📦 Jami: <b>{orders_count}</b> ta buyurtma
💰 Umumiy: <b>{total_price_str}</b>
⏱ Kutish: <b>{int(oldest_age)} sek</b> (180+ sek o'tdi!)

❌ Buyurtmalarni qabul qilmayapti!
📞 {call_attempts} marta qo'ng'iroq qilindi.
🔴 Zudlik bilan bog'laning!

📱 <a href="https://test.nonbor.uz">Buyurtmalarni ko'rish</a>"""

        # Telegram ga yuborish (eski xabarni o'chirib yangi yuborish)
        try:
            # 1. Eski xabarni o'chirish (agar mavjud bo'lsa)
            old_message_id = status_alert_messages.get(seller_phone)
            if old_message_id:
                try:
                    delete_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
                    requests.post(delete_url, data={
                        "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                        "message_id": old_message_id
                    }, timeout=5)
                except:
                    pass  # Eski xabar o'chirilmasa ham davom etamiz

            # 2. Yangi xabar yuborish
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            response = requests.post(url, data={
                "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            }, timeout=10)

            if response.status_code == 200:
                result = response.json()
                new_message_id = result.get("result", {}).get("message_id")
                # Yangi xabar ID ni saqlash
                if new_message_id:
                    status_alert_messages[seller_phone] = new_message_id
                logger.warning(f"⚠️ Status alert yuborildi: Sotuvchi {seller_phone}, {len(order_ids)} ta buyurtma, {int(oldest_age)} sek")
                # Barcha buyurtmalarni alerted ga qo'shish
                for oid in order_ids:
                    status_alerted_orders.add(oid)
                alerts_sent += 1
            else:
                logger.error(f"⚠️ Status alert xatosi: {response.text}")
        except Exception as e:
            logger.error(f"⚠️ Status alert xatosi: {e}")

    return alerts_sent


async def polling_task():
    """Nonbor API dan buyurtmalarni polling qilish - sotuvchi bo'yicha guruhlash"""
    logger.info("Polling boshlandi...")

    while True:
        try:
            # ===== BARCHA BUYURTMALARNI OLISH (status tekshirish uchun) =====
            all_orders = get_all_orders()

            # ===== API DAGI BUYURTMALAR ID LARINI OLISH =====
            current_api_order_ids = set(o.get('id') for o in all_orders)

            # ===== YO'QOLGAN BUYURTMALARNI ANIQLASH (API dan o'chirilgan) =====
            # seller_orders_data da saqlangan lekin API da yo'q buyurtmalar
            for seller_key, data in list(seller_orders_data.items()):
                orders_in_telegram = data.get("seller_orders", {}).get("orders", [])

                for order in orders_in_telegram:
                    order_id = order.get("lead_id") or order.get("order_number")

                    # Agar buyurtma API da yo'q bo'lsa - qabul qilingan deb hisoblaymiz
                    if order_id and order_id not in current_api_order_ids:
                        logger.info(f"📊 Buyurtma #{order_id} API dan yo'qoldi (qabul qilingan)")

                        # Telegram xabarni yangilash - qabul qilingan sifatida
                        update_seller_telegram_on_status_change(seller_key, order_id, ORDER_STATUS_ACCEPTED)

                        # order_statuses va order_to_seller dan tozalash
                        if order_id in order_statuses:
                            del order_statuses[order_id]
                        if order_id in order_to_seller:
                            del order_to_seller[order_id]

            # ===== STATUS O'ZGARISHINI TEKSHIRISH =====
            for order in all_orders:
                order_id = order.get('id')
                current_status = order.get('state')
                old_status = order_statuses.get(order_id)

                # Agar order_to_seller da yo'q bo'lsa - biznes nomi bo'yicha mapping qo'shish
                if order_id not in order_to_seller:
                    business = order.get('business', {})
                    biznes_nomi = business.get('title', 'Noma\'lum')
                    # Biznes nomini seller_phone sifatida ishlatamiz (unique identifier)
                    order_to_seller[order_id] = biznes_nomi

                # Agar status o'zgargan bo'lsa
                if old_status and old_status != current_status:
                    logger.info(f"📊 Status o'zgardi: #{order_id} {old_status} → {current_status}")

                    # Sotuvchi/biznes nomini topish - order_to_seller dan
                    seller_key = order_to_seller.get(order_id)

                    # Agar order_to_seller da yo'q bo'lsa, pending_orders dan qidirish
                    if not seller_key:
                        order_data = pending_orders.get(order_id, {})
                        seller_key = order_data.get('seller_phone')

                    if seller_key:
                        logger.info(f"📊 Sotuvchi/Biznes topildi: {seller_key}, buyurtma #{order_id}")
                        logger.info(f"📊 seller_messages: {list(seller_messages.keys())}")
                        logger.info(f"📊 seller_orders_data: {list(seller_orders_data.keys())}")
                        # Telegram xabarni yangilash
                        update_seller_telegram_on_status_change(seller_key, order_id, current_status)

                        # Pending dan o'chirish
                        if order_id in pending_orders:
                            del pending_orders[order_id]
                    else:
                        logger.warning(f"📊 Sotuvchi topilmadi: buyurtma #{order_id}")

                    # Guruh xabarini yangilash (fallback)
                    for msg_id, msg_data in list(order_group_messages.items()):
                        if msg_data.get('order_id') == order_id:
                            update_order_status_in_group(msg_id, current_status)
                            break

                # Statusni saqlash
                order_statuses[order_id] = current_status

            # ===== FAQAT CHECKING BUYURTMALARNI FILTR QILISH =====
            # get_all_orders() allaqachon faqat CHECKING qaytaradi
            orders = all_orders  # CHECKING statusidagi buyurtmalar

            # Yangi buyurtmalarni sotuvchi bo'yicha guruhlash
            new_orders_by_seller = {}  # {seller_phone: [(order_id, order_data)]}

            for order in orders:
                order_id = order.get('id')

                # Allaqachon qayta ishlangan buyurtmalarni o'tkazib yuborish
                if order_id in processed_orders or order_id in pending_orders:
                    continue

                # Eski buyurtmalarni o'tkazib yuborish (5 daqiqadan eski)
                created_at_str = order.get('created_at', '')
                if created_at_str:
                    try:
                        # ISO format: 2026-01-16T23:15:43.123456+05:00
                        # Timezone ni olib tashlaymiz
                        clean_date = created_at_str.split('+')[0].split('Z')[0]
                        if '.' in clean_date:
                            created_at = datetime.strptime(clean_date, '%Y-%m-%dT%H:%M:%S.%f')
                        else:
                            created_at = datetime.strptime(clean_date, '%Y-%m-%dT%H:%M:%S')
                        age_seconds = (datetime.now() - created_at).total_seconds()
                        if age_seconds > 300:  # 5 daqiqadan eski
                            # Eski buyurtma - qayta ishlamaslik uchun processed ga qo'shish
                            processed_orders.add(order_id)
                            logger.debug(f"Buyurtma #{order_id}: Eski ({age_seconds:.0f} sek), o'tkazib yuborildi")
                            continue
                    except Exception as e:
                        logger.warning(f"Buyurtma #{order_id}: Sana parse xatosi: {e}")

                # Nonbor API dan biznes ma'lumotlarini olish
                business = order.get('business', {})

                # Sotuvchi ilova tili (business yoki user dan)
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

                business_id = business.get('id')
                business_info = {
                    'biznes_nomi': business.get('title', 'Noma\'lum'),
                    'biznes_tel': None,  # API dan olinadi
                    'biznes_manzil': business.get('address', ''),
                    'business_id': business_id,
                    'order_id': order_id,
                    'narx': order.get('total_price', 0),
                    'summa': order.get('total_price', 0),
                    'tolov': order.get('payment_method', 'CASH'),
                    'yetkazish': order.get('delivery_method', 'DELIVERY'),
                    'language': seller_language,  # Sotuvchi tili
                    'created_at': order.get('created_at', ''),
                }

                # Mijoz ma'lumotlari
                user = order.get('user', {})
                first_name = user.get('first_name', '')
                last_name = user.get('last_name', '')
                mijoz_nomi = f"{first_name} {last_name}".strip() or 'Noma\'lum'
                business_info['mijoz_nomi'] = mijoz_nomi
                business_info['mijoz_tel'] = user.get('phone', '')

                # Barcha mahsulotlarni olish - order_item dan
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

                # Telefon raqamini olish - biznes yoki mijoz tel
                # Biznes telefon yo'q, mijoz telefon ishlatamiz
                phone = business_info.get('mijoz_tel') or user.get('phone', '')

                if phone:
                    # Format telefon raqamini
                    phone = str(phone)
                    if not phone.startswith('+'):
                        phone = '+' + phone
                    logger.info(f"Buyurtma #{order_id}: Tel ishlatilmoqda: {phone}")
                else:
                    logger.warning(f"Buyurtma #{order_id}: Telefon raqami topilmadi")
                    phone = "+998948679300"  # Default telefon

                # Sotuvchi guruhiga qo'shish
                if phone not in new_orders_by_seller:
                    new_orders_by_seller[phone] = []
                new_orders_by_seller[phone].append((order_id, business_info))

                # Pending va processed ga qo'shish
                pending_orders[order_id] = {
                    'order_id': order_id,
                    'order_number': order_id,
                    'seller_phone': phone,
                    'biznes_nomi': business_info.get('biznes_nomi', 'Noma\'lum'),
                    'business_info': business_info,
                    'created_at': order.get('created_at', datetime.now().isoformat())
                }
                processed_orders.add(order_id)
                # 180 sek alert uchun birinchi ko'rilgan vaqtni saqlash
                order_first_seen[order_id] = datetime.now()

                # order_to_seller mapping (status kuzatish uchun)
                order_to_seller[order_id] = phone

                logger.info(f"Yangi buyurtma: #{order_id} - {phone} - {business_info.get('biznes_nomi')}")

                # Biznes guruhiga xabar yuborish
                if business_id:
                    send_order_to_business_group(order_id, business_id, business_info)

            # Har bir sotuvchi uchun yangi buyurtmalarni qayta ishlash
            for seller_phone, orders_list in new_orders_by_seller.items():
                order_ids = [o[0] for o in orders_list]
                business_info = orders_list[0][1]  # Birinchi buyurtmaning biznes info

                # Sotuvchi guruhiga qo'shish (mavjud bo'lsa qo'shib qo'yish)
                if seller_phone not in seller_order_groups:
                    seller_order_groups[seller_phone] = []
                    # Yangi guruh - task ishga tushirish
                    seller_order_groups[seller_phone].extend(order_ids)
                    seller_lang = business_info.get('language', DEFAULT_LANGUAGE)
                    logger.info(f"Sotuvchi {seller_phone}: {len(order_ids)} ta yangi buyurtma, til={seller_lang}, task ishga tushirilmoqda")
                    asyncio.create_task(process_seller_orders(seller_phone, order_ids, business_info, seller_lang))
                else:
                    # Mavjud guruhga qo'shish (task allaqachon ishlayapti)
                    seller_order_groups[seller_phone].extend(order_ids)
                    logger.info(f"Sotuvchi {seller_phone}: {len(order_ids)} ta buyurtma qo'shildi, jami {len(seller_order_groups[seller_phone])} ta")

            # Eski processed buyurtmalarni tozalash
            # API da yo'q bo'lgan buyurtmalarni processed dan o'chirish
            current_order_ids = set(o.get('id') for o in orders)

            # pending_orders dagi lekin API da yo'q buyurtmalarni tozalash
            pending_to_remove = [oid for oid in pending_orders if oid not in current_order_ids]
            # Sotuvchi bo'yicha guruhlash (Telegram xabar o'chirish uchun)
            sellers_to_update = set()
            for oid in pending_to_remove:
                logger.info(f"Buyurtma #{oid}: API dan yo'qoldi - pending dan tozalandi (status o'zgargan)")
                # Sotuvchini topish
                seller_phone = order_to_seller.get(oid) or pending_orders.get(oid, {}).get('seller_phone')
                if seller_phone:
                    sellers_to_update.add(seller_phone)
                # Telegram xabarini o'chirish (order_messages)
                delete_telegram_message(oid)
                del pending_orders[oid]
                processed_orders.discard(oid)
                if oid in order_statuses:
                    del order_statuses[oid]
                if oid in order_to_seller:
                    del order_to_seller[oid]
                # 180 sek alert uchun tozalash
                if oid in order_first_seen:
                    del order_first_seen[oid]
                status_alerted_orders.discard(oid)

            # Sotuvchi xabarlarini o'chirish
            for seller_phone in sellers_to_update:
                # Agar bu sotuvchiga tegishli boshqa buyurtmalar bo'lmasa, xabarni o'chirish
                remaining_orders = [oid for oid, data in pending_orders.items() if data.get('seller_phone') == seller_phone]
                if not remaining_orders:
                    # Barcha buyurtmalar qabul qilindi - xabarni O'CHIRISH
                    delete_seller_telegram_message(seller_phone)

                    # Status alert xabarini o'chirish
                    if seller_phone in status_alert_messages:
                        try:
                            delete_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
                            requests.post(delete_url, data={
                                "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                                "message_id": status_alert_messages[seller_phone]
                            }, timeout=5)
                        except:
                            pass
                        del status_alert_messages[seller_phone]

            # Eski processed buyurtmalarni ham tozalash
            old_processed = [oid for oid in processed_orders if oid not in current_order_ids]
            for oid in old_processed:
                processed_orders.discard(oid)
            if old_processed or pending_to_remove:
                logger.info(f"Eski buyurtmalar tozalandi: {len(old_processed) + len(pending_to_remove)} ta")

            # 180 sekundda status o'zgarmagan buyurtmalar uchun ogohlantirish
            check_status_alerts()

        except Exception as e:
            logger.error(f"Polling xatosi: {e}")

        await asyncio.sleep(POLLING_INTERVAL)


async def handle_call_result(request):
    """IVR qo'ng'iroq natijasi (Asterisk'dan callback)"""
    try:
        data = await request.json()
        order_id = data.get('order_id')
        status = data.get('status')  # accepted, rejected, timeout
        phone = data.get('phone')

        logger.info(f"IVR natija: Order #{order_id} - {status} ({phone})")

        if order_id:
            # String bo'lsa intga o'tkazish
            try:
                order_id = int(order_id)
            except:
                pass

            call_results[order_id] = status

            # amoCRM statusini yangilash
            if status == 'accepted':
                update_lead_status(order_id, AMOCRM_STATUS_QABUL_QILINDI)
                delete_telegram_message(order_id)
                send_telegram_message(f"✅ Buyurtma #{order_id} sotuvchi tomonidan qabul qilindi!", order_id=None)
            elif status == 'rejected':
                update_lead_status(order_id, AMOCRM_STATUS_RAD_ETILDI)
                send_telegram_message(f"❌ Buyurtma #{order_id} sotuvchi tomonidan rad etildi!", order_id=None)

            # Pending dan o'chirish
            if order_id in pending_orders:
                del pending_orders[order_id]

        return web.json_response({"status": "ok", "received": data})
    except Exception as e:
        logger.error(f"Call result xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})


# ============ STATISTIKA ============
# Qo'ng'iroqlar statistikasi
call_statistics = {
    "total_calls": 0,              # Jami qo'ng'iroqlar
    "answered_calls": 0,           # Javob berilgan
    "unanswered_calls": 0,         # Javob berilmagan
    "first_attempt_answered": 0,   # 1-urinishda javob
    "second_attempt_answered": 0,  # 2-urinishda javob
    "by_date": {},                 # Kun bo'yicha
}

# Buyurtmalar statistikasi
order_statistics = {
    "total_orders": 0,           # Jami buyurtmalar
    "accepted_orders": 0,        # Qabul qilingan
    "cancelled_orders": 0,       # Bekor qilingan
    "telegram_accepted": 0,      # Telegram orqali qabul qilingan (qo'ng'iroqsiz)
    "ready_orders": 0,           # Tayyor
    "delivering_orders": 0,      # Yetkazilmoqda
    "completed_orders": 0,       # Yakunlangan
    "by_seller": {},             # Sotuvchi bo'yicha: {seller: {accepted: X, cancelled: Y}}
    "by_date": {},               # Kun bo'yicha: {date: {accepted: X, cancelled: Y}}
}


def record_call_statistic(answered: bool, attempt: int = 1):
    """Qo'ng'iroq statistikasini yozish"""
    today = datetime.now().strftime('%Y-%m-%d')

    # Kun bo'yicha statistika
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


async def handle_order_webhook(request):
    """
    Nonbor backend dan buyurtma statusi o'zgarganda webhook

    Endpoint: POST /api/webhook/order-status

    Body:
    {
        "order_id": 123,
        "status": "ACCEPTED",  // CHECKING, ACCEPTED, READY, DELIVERING, COMPLETED, CANCELLED
        "seller_phone": "+998901234567",
        "seller_name": "Milliy",
        "old_status": "CHECKING"
    }
    """
    try:
        data = await request.json()
        order_id = data.get('order_id') or data.get('id')
        new_status = data.get('status') or data.get('state')
        old_status = data.get('old_status')
        seller_phone = data.get('seller_phone') or data.get('business', {}).get('phone')
        seller_name = data.get('seller_name') or data.get('business', {}).get('title', 'Noma\'lum')

        logger.info(f"📊 WEBHOOK: Buyurtma #{order_id} | {old_status} → {new_status} | {seller_name}")

        # ===== STATISTIKANI YANGILASH =====
        today = datetime.now().strftime('%Y-%m-%d')

        # Kun bo'yicha statistika
        if today not in order_statistics["by_date"]:
            order_statistics["by_date"][today] = {
                "total": 0, "accepted": 0, "cancelled": 0,
                "ready": 0, "delivering": 0, "completed": 0,
                "telegram_accepted": 0  # Telegram orqali qabul qilingan
            }

        # Sotuvchi bo'yicha statistika
        if seller_name not in order_statistics["by_seller"]:
            order_statistics["by_seller"][seller_name] = {
                "total": 0, "accepted": 0, "cancelled": 0,
                "ready": 0, "delivering": 0, "completed": 0,
                "telegram_accepted": 0
            }

        # Telegram orqali qabul tekshirish
        # Agar buyurtma CHECKING dan ACCEPTED ga o'tgan bo'lsa va
        # pending_orders da bo'lsa (qo'ng'iroq qilinmagan) - bu telegram_accepted
        is_telegram_accepted = (
            old_status == ORDER_STATUS_CHECKING and
            new_status == ORDER_STATUS_ACCEPTED and
            order_id in pending_orders  # Hali qo'ng'iroq qilinmagan
        )

        # Status bo'yicha hisoblash
        if new_status == ORDER_STATUS_ACCEPTED:
            order_statistics["accepted_orders"] += 1
            order_statistics["by_date"][today]["accepted"] += 1
            order_statistics["by_seller"][seller_name]["accepted"] += 1

            # Telegram orqali qabul qilingan
            if is_telegram_accepted:
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

        # Yangi buyurtma (CHECKING ga tushganda)
        if new_status == ORDER_STATUS_CHECKING:
            order_statistics["total_orders"] += 1
            order_statistics["by_date"][today]["total"] += 1
            order_statistics["by_seller"][seller_name]["total"] += 1

        # ===== TELEGRAM XABARNI YANGILASH =====
        # Sotuvchi telefoni yoki nomi bo'yicha mapping
        seller_key = seller_phone or seller_name

        if seller_key:
            update_seller_telegram_on_status_change(seller_key, order_id, new_status)

        # ===== GURUH XABARINI YANGILASH =====
        # order_group_messages da buyurtma xabarini topish va yangilash
        for msg_id, msg_data in list(order_group_messages.items()):
            if msg_data.get('order_id') == order_id:
                update_order_status_in_group(msg_id, new_status)
                logger.info(f"Guruh xabari yangilandi: Buyurtma #{order_id} → {new_status}")
                break

        # ===== PENDING/PROCESSED DAN TOZALASH =====
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

    except Exception as e:
        logger.error(f"Webhook xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_statistics(request):
    """
    Statistikani olish - Telegram bot formatida

    Query params:
    - period: today (default), week, month, year
    - format: json (default), telegram (matnli format)

    Response format (Telegram bot ga mos):
    {
        "calls": {
            "total": 0,          // Jami qo'ng'iroqlar
            "answered": 0,       // Javob berildi
            "unanswered": 0,     // Javob berilmadi
            "first_attempt": 0,  // 1-urinishda javob
            "second_attempt": 0  // 2-urinishda javob
        },
        "orders": {
            "total": 0,           // Jami buyurtmalar
            "accepted": 0,        // Qabul qilindi
            "cancelled": 0,       // Bekor qilindi
            "telegram_accepted": 0  // Telegramdan qabul
        }
    }
    """
    from datetime import timedelta

    # Query parametrlari
    period = request.query.get('period', 'today')
    output_format = request.query.get('format', 'json')

    today = datetime.now().strftime('%Y-%m-%d')

    # Sana oralig'ini aniqlash
    if period == 'week':
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    elif period == 'month':
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    elif period == 'year':
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    else:  # today
        start_date = today

    # Qo'ng'iroqlar statistikasi
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
        # Period bo'yicha yig'ish
        calls_data = {"total": 0, "answered": 0, "unanswered": 0, "first_attempt": 0, "second_attempt": 0}
        for date_str, stats in call_statistics["by_date"].items():
            if date_str >= start_date:
                calls_data["total"] += stats.get("total", 0)
                calls_data["answered"] += stats.get("answered", 0)
                calls_data["unanswered"] += stats.get("unanswered", 0)
                calls_data["first_attempt"] += stats.get("first_attempt", 0)
                calls_data["second_attempt"] += stats.get("second_attempt", 0)

    # Buyurtmalar statistikasi
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

    # Telegram formati (matnli)
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

    # JSON formati
    return web.json_response({
        "status": "ok",
        "period": period,
        "date_range": {
            "start": start_date,
            "end": today
        },
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
        "by_seller": order_statistics["by_seller"],
        "pending_count": len(pending_orders),
        "processed_count": len(processed_orders)
    })


async def handle_status_change(request):
    """Status o'zgarganda webhook (backend'dan) - eski endpoint"""
    # Yangi webhook ga yo'naltirish
    return await handle_order_webhook(request)


async def handle_test(request):
    """Test endpoint"""
    return web.json_response({
        "status": "ok",
        "message": "Autodialer v2 ishlayapti!",
        "pending_orders": len(pending_orders),
        "processed_orders": len(processed_orders),
        "call_results": len(call_results),
        "active_calls": len(active_calls),
        "time": datetime.now().isoformat()
    })


async def handle_test_call(request):
    """Test qo'ng'iroq"""
    try:
        data = await request.json()
        phone = data.get('phone', '+998948679300')
        order_id = data.get('order_id')
        result = make_call(phone, order_id=order_id)
        return web.json_response({
            "status": "ok" if result else "error",
            "phone": phone,
            "order_id": order_id
        })
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)})


async def handle_test_telegram(request):
    """Test Telegram xabar"""
    message = "✅ <b>Test xabar!</b>\n\nAutodialer v2 tizimi ishlayapti.\n⏰ Vaqt: " + datetime.now().strftime('%H:%M:%S')
    result = send_telegram_message(message)
    return web.json_response({"status": "ok" if result else "error"})


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
                biznes_nomi  # Agar telefon topilmasa, biznes nomini ishlatamiz
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
                logger.info(f"✅ Alert yuborildi: {seller_phone}, {len(seller_orders['orders'])} ta buyurtma")

        return web.json_response({
            "status": "ok",
            "message": f"{sent_count} ta sotuvchi uchun Telegram xabar yuborildi",
            "total_orders": len(pending),
            "sellers": list(orders_by_seller.keys())
        })

    except Exception as e:
        logger.error(f"Test alert xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})


async def handle_check_leads(request):
    """Hozirgi PENDING buyurtmalarni ko'rsatish"""
    orders = get_pending_orders()
    return web.json_response({
        "count": len(orders),
        "orders": [{"id": o.get('id'), "business": o.get('business', {}).get('title'), "state": o.get('state')} for o in orders]
    })


# ============ AMOCRM TELEPHONY - OPERATOR QO'NG'IROQ QILISHI ============

async def handle_amocrm_call(request):
    """
    amoCRM ichidan operator qo'ng'iroq qilganda (Click-to-Call)
    amoCRM telephony widget bu endpoint ga POST so'rov yuboradi
    """
    try:
        data = await request.json()
        phone = data.get('phone')
        user_id = data.get('user_id')  # amoCRM operator user ID
        lead_id = data.get('lead_id')  # Lead ID (agar mavjud bo'lsa)
        contact_id = data.get('contact_id')  # Contact ID

        logger.info(f"📞 amoCRM Click-to-Call: {phone}, Operator: {user_id}, Lead: {lead_id}")

        if not phone:
            return web.json_response({"status": "error", "message": "Phone number required"})

        # Telefon formatini tekshirish
        phone = ''.join(filter(lambda x: x.isdigit() or x == '+', phone))
        if not phone.startswith('+'):
            phone = '+' + phone

        # Asterisk orqali qo'ng'iroq qilish
        call_id = str(int(datetime.now().timestamp() * 1000))
        result = make_call(phone)

        if result:
            # Qo'ng'iroq tarixiga qo'shish
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
            call_history.insert(0, call_record)
            active_calls[call_id] = call_record

            logger.info(f"✅ Qo'ng'iroq boshlandi: {call_id}")

            return web.json_response({
                "status": "ok",
                "call_id": call_id,
                "phone": phone,
                "message": "Qo'ng'iroq boshlandi"
            })
        else:
            logger.error(f"❌ Qo'ng'iroq qilishda xatolik: {phone}")
            return web.json_response({
                "status": "error",
                "message": "Qo'ng'iroq qilishda xatolik. Asterisk sozlamalarini tekshiring."
            })

    except Exception as e:
        logger.error(f"amoCRM call xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})


async def handle_amocrm_call_event(request):
    """
    Asterisk dan qo'ng'iroq event lari (answer, hangup, busy, no-answer)
    Bu event amoCRM ga yuborilib, qo'ng'iroq tarixiga yoziladi
    """
    try:
        data = await request.json()
        call_id = data.get('call_id')
        event = data.get('event')  # answer, hangup, busy, no-answer
        duration = data.get('duration', 0)
        phone = data.get('phone')

        logger.info(f"📞 Call event: {call_id} - {event} ({duration} sek)")

        # Active call ni yangilash
        if call_id in active_calls:
            call_data = active_calls[call_id]

            if event == 'hangup':
                call_data['status'] = 'completed'
                call_data['duration'] = duration

                # amoCRM ga qo'ng'iroq tarixini yuborish
                await save_call_to_amocrm(call_data)

                del active_calls[call_id]

            elif event == 'answer':
                call_data['status'] = 'in_progress'

            elif event in ['busy', 'no-answer']:
                call_data['status'] = event
                await save_call_to_amocrm(call_data)
                del active_calls[call_id]

        return web.json_response({"status": "ok"})

    except Exception as e:
        logger.error(f"Call event xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})


async def save_call_to_amocrm(call_data):
    """Qo'ng'iroqni amoCRM tarixiga saqlash"""
    try:
        contact_id = call_data.get('contact_id')
        lead_id = call_data.get('lead_id')

        if not contact_id and not lead_id:
            logger.warning("Contact yoki Lead ID yo'q, amoCRM ga saqlanmadi")
            return False

        # amoCRM Calls API
        url = f"https://{AMOCRM_DOMAIN}/api/v4/calls"

        call_note = {
            "direction": "outbound" if call_data.get('direction') == 'outgoing' else 'inbound',
            "uniq": call_data.get('id'),
            "duration": call_data.get('duration', 0),
            "source": "asterisk",
            "phone": call_data.get('phone'),
            "call_status": 4 if call_data.get('status') == 'completed' else 6,  # 4=success, 6=missed
            "created_at": int(datetime.fromisoformat(call_data.get('timestamp')).timestamp())
        }

        if contact_id:
            call_note["entity_id"] = contact_id
            call_note["entity_type"] = "contacts"
        elif lead_id:
            call_note["entity_id"] = lead_id
            call_note["entity_type"] = "leads"

        response = requests.post(
            url,
            headers=get_amocrm_headers(),
            json=[call_note]
        )

        if response.status_code in [200, 201]:
            logger.info(f"✅ Qo'ng'iroq amoCRM ga saqlandi: {call_data.get('id')}")
            return True
        else:
            logger.error(f"amoCRM save xatosi: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.error(f"amoCRM save xatosi: {e}")
        return False


# ============ KIRUVCHI QO'NG'IROQLAR ============

async def handle_incoming_call(request):
    """
    Kiruvchi qo'ng'iroq (Asterisk'dan webhook)
    Bu amoCRM da popup chiqaradi va contact/lead ni ko'rsatadi
    """
    try:
        data = await request.json()
        caller_id = data.get('caller_id')  # Kim qo'ng'iroq qilyapti
        called_number = data.get('called_number')  # Bizning raqam
        call_id = data.get('call_id') or str(datetime.now().timestamp())

        logger.info(f"📞 Kiruvchi qo'ng'iroq: {caller_id} -> {called_number}")

        # Qo'ng'iroq tarixiga qo'shish
        call_record = {
            "id": call_id,
            "phone": caller_id,
            "direction": "incoming",
            "status": "ringing",
            "duration": 0,
            "timestamp": datetime.now().isoformat()
        }
        call_history.insert(0, call_record)
        active_calls[call_id] = call_record

        # Telegram ga xabar
        send_telegram_message(f"📞 <b>Kiruvchi qo'ng'iroq!</b>\n\n📱 Raqam: {caller_id}\n⏰ Vaqt: {datetime.now().strftime('%H:%M:%S')}")

        # Contact ni telefon raqami bo'yicha qidirish
        contact = await find_contact_by_phone(caller_id)

        if contact:
            # Contact topildi
            logger.info(f"Contact topildi: {contact.get('id')} - {contact.get('name')}")
            call_record['contact_id'] = contact.get('id')

            return web.json_response({
                "status": "ok",
                "call_id": call_id,
                "contact_id": contact.get('id'),
                "contact_name": contact.get('name'),
                "action": "open_card"
            })
        else:
            # Yangi raqam
            logger.info(f"Yangi raqam: {caller_id}")

            return web.json_response({
                "status": "ok",
                "call_id": call_id,
                "caller_id": caller_id,
                "action": "create_contact"
            })

    except Exception as e:
        logger.error(f"Kiruvchi qo'ng'iroq xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})


async def handle_call_hangup(request):
    """Qo'ng'iroq tugatilganda (Asterisk'dan webhook)"""
    try:
        data = await request.json()
        call_id = data.get('call_id')
        duration = data.get('duration', 0)
        status = data.get('status', 'completed')  # completed, missed, busy

        logger.info(f"📴 Qo'ng'iroq tugadi: {call_id}, {duration} sek, {status}")

        if call_id in active_calls:
            call_data = active_calls[call_id]
            call_data['status'] = status
            call_data['duration'] = duration

            # Tarixni yangilash
            for record in call_history:
                if record['id'] == call_id:
                    record['status'] = status
                    record['duration'] = duration
                    break

            # amoCRM ga saqlash
            await save_call_to_amocrm(call_data)

            del active_calls[call_id]

        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Call hangup xatosi: {e}")
        return web.json_response({"status": "error", "message": str(e)})


async def find_contact_by_phone(phone):
    """Telefon raqami bo'yicha contact qidirish"""
    try:
        # Telefon formatini tozalash
        clean_phone = ''.join(filter(str.isdigit, phone))

        url = f"https://{AMOCRM_DOMAIN}/api/v4/contacts"
        params = {
            "query": clean_phone[-9:]  # Oxirgi 9 ta raqam
        }

        response = requests.get(url, headers=get_amocrm_headers(), params=params)

        if response.status_code == 200:
            data = response.json()
            contacts = data.get('_embedded', {}).get('contacts', [])
            if contacts:
                return contacts[0]

        return None

    except Exception as e:
        logger.error(f"Contact qidirish xatosi: {e}")
        return None


async def handle_call_history(request):
    """Qo'ng'iroq tarixini olish"""
    limit = int(request.query.get('limit', 50))
    direction = request.query.get('direction')  # incoming, outgoing, all

    filtered_history = call_history
    if direction and direction != 'all':
        filtered_history = [c for c in call_history if c['direction'] == direction]

    return web.json_response({
        "status": "ok",
        "total": len(filtered_history),
        "calls": filtered_history[:limit]
    })


# ============ TELEGRAM BOT COMMANDS ============
telegram_last_update_id = 0
from datetime import timedelta

def get_date_range(period):
    """Davr uchun sana oralig'ini hisoblash"""
    today = datetime.now().date()
    if period == "daily":
        return [today.strftime('%Y-%m-%d')]
    elif period == "weekly":
        # Oxirgi 7 kun
        return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    elif period == "monthly":
        # Oxirgi 30 kun
        return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(30)]
    elif period == "yearly":
        # Oxirgi 365 kun
        return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(365)]
    return [today.strftime('%Y-%m-%d')]

def get_period_statistics(period="daily"):
    """Davr bo'yicha statistika olish"""
    dates = get_date_range(period)

    # Qo'ng'iroqlar statistikasi
    total_calls = 0
    answered = 0
    unanswered = 0
    first_attempt = 0
    second_attempt = 0

    for date in dates:
        day_calls = call_statistics["by_date"].get(date, {})
        total_calls += day_calls.get("total", 0)
        answered += day_calls.get("answered", 0)
        unanswered += day_calls.get("unanswered", 0)
        first_attempt += day_calls.get("first_attempt", 0)
        second_attempt += day_calls.get("second_attempt", 0)

    # Buyurtmalar statistikasi
    total_orders = 0
    accepted = 0
    cancelled = 0

    for date in dates:
        day_orders = order_statistics["by_date"].get(date, {})
        total_orders += day_orders.get("total", 0)
        accepted += day_orders.get("accepted", 0)
        cancelled += day_orders.get("cancelled", 0)

    return {
        "total_calls": total_calls,
        "answered": answered,
        "unanswered": unanswered,
        "first_attempt": first_attempt,
        "second_attempt": second_attempt,
        "total_orders": total_orders,
        "accepted": accepted,
        "cancelled": cancelled
    }

def get_bot_statistics_text(period="daily"):
    """Statistika matnini yaratish"""
    stats = get_period_statistics(period)

    # Davr nomi
    period_names = {
        "daily": "📅 Bugun",
        "weekly": "📆 Oxirgi 7 kun",
        "monthly": "🗓 Oxirgi 30 kun",
        "yearly": "📊 Oxirgi 1 yil"
    }
    period_name = period_names.get(period, "📅 Bugun")

    # Hozirgi holat
    current_pending = len(pending_orders)
    current_processed = len(processed_orders)
    active_sellers = len(seller_order_groups)

    text = f"""📊 <b>NONBOR AUTODIALER STATISTIKA</b>

<b>{period_name}:</b>

📞 <b>Qo'ng'iroqlar:</b>
   Jami: {stats['total_calls']} ta
   ✅ Javob berildi: {stats['answered']}
   ❌ Javob berilmadi: {stats['unanswered']}
   1️⃣ 1-urinishda: {stats['first_attempt']}
   2️⃣ 2-urinishda: {stats['second_attempt']}

📦 <b>Buyurtmalar:</b>
   Jami: {stats['total_orders']} ta
   ✅ Qabul qilindi: {stats['accepted']}
   ❌ Bekor qilindi: {stats['cancelled']}

🔄 <b>Hozirgi holat:</b>
   Kutilayotgan: {current_pending} ta
   Qayta ishlangan: {current_processed} ta
   Faol sotuvchilar: {active_sellers} ta

⏰ Yangilangan: {datetime.now().strftime('%H:%M:%S')}
"""
    return text

def get_orders_list_text():
    """Kutilayotgan buyurtmalar ro'yxatini yaratish"""
    if not pending_orders:
        return """📦 <b>KUTILAYOTGAN BUYURTMALAR</b>

Hozirda kutilayotgan buyurtmalar yo'q.

⏰ Yangilangan: """ + datetime.now().strftime('%H:%M:%S')

    text = """📦 <b>KUTILAYOTGAN BUYURTMALAR</b>

"""
    for i, (order_id, order_data) in enumerate(list(pending_orders.items())[:10], 1):
        seller_phone = order_data.get('seller_phone', 'Noma\'lum')
        order_time = order_data.get('created_at', '')
        status = order_data.get('status', 'pending')
        text += f"{i}. #{order_id}\n"
        text += f"   📞 {seller_phone}\n"
        text += f"   🕐 {order_time}\n\n"

    if len(pending_orders) > 10:
        text += f"... va yana {len(pending_orders) - 10} ta buyurtma\n"

    text += f"\n📊 Jami: {len(pending_orders)} ta"
    text += f"\n⏰ Yangilangan: {datetime.now().strftime('%H:%M:%S')}"
    return text

def get_calls_history_text():
    """Oxirgi qo'ng'iroqlar tarixini yaratish"""
    today = datetime.now().strftime("%Y-%m-%d")
    today_calls = call_statistics["by_date"].get(today, {})

    text = """📞 <b>BUGUNGI QO'NG'IROQLAR</b>

"""
    text += f"📊 <b>Umumiy statistika:</b>\n"
    text += f"   Jami: {today_calls.get('total', 0)} ta\n"
    text += f"   ✅ Javob berildi: {today_calls.get('answered', 0)}\n"
    text += f"   ❌ Javob berilmadi: {today_calls.get('unanswered', 0)}\n"
    text += f"   1️⃣ 1-urinishda: {today_calls.get('first_attempt', 0)}\n"
    text += f"   2️⃣ 2-urinishda: {today_calls.get('second_attempt', 0)}\n"

    # Oxirgi qo'ng'iroqlar (processed_orders dan)
    if processed_orders:
        text += f"\n📝 <b>Oxirgi qayta ishlangan:</b>\n"
        recent = list(processed_orders.items())[-5:]
        for order_id, data in reversed(recent):
            result = data.get('result', 'unknown')
            emoji = "✅" if result == "answered" else "❌"
            text += f"   {emoji} #{order_id}\n"

    text += f"\n⏰ Yangilangan: {datetime.now().strftime('%H:%M:%S')}"
    return text

def get_sellers_list_text():
    """Faol sotuvchilar ro'yxatini yaratish"""
    if not seller_order_groups:
        return """👨‍💼 <b>FAOL SOTUVCHILAR</b>

Hozirda faol sotuvchilar yo'q.

⏰ Yangilangan: """ + datetime.now().strftime('%H:%M:%S')

    text = """👨‍💼 <b>FAOL SOTUVCHILAR</b>

"""
    for i, (seller_phone, orders) in enumerate(list(seller_order_groups.items())[:10], 1):
        order_count = len(orders) if isinstance(orders, list) else 1
        text += f"{i}. 📞 {seller_phone}\n"
        text += f"   📦 Buyurtmalar: {order_count} ta\n\n"

    if len(seller_order_groups) > 10:
        text += f"... va yana {len(seller_order_groups) - 10} ta sotuvchi\n"

    text += f"\n📊 Jami faol: {len(seller_order_groups)} ta"
    text += f"\n⏰ Yangilangan: {datetime.now().strftime('%H:%M:%S')}"
    return text

# Bizneslar cache va pagination uchun global o'zgaruvchilar
businesses_cache = []
businesses_cache_time = None
businesses_current_page = 1
businesses_current_region = None
businesses_current_district = None
businesses_filtered_list = []  # Joriy filtrangan ro'yxat
selected_business_id = None  # Tanlangan biznes ID
waiting_for_group_id = False  # Guruh ID kutilmoqda
waiting_for_phone_edit = False  # Telefon raqami kutilmoqda
pending_phone_change = {}  # {"business_id": X, "new_phone": "+998..."}
BUSINESSES_PER_PAGE = 10

# Biznes egasi uchun guruh qo'shish holati
owner_waiting_for_group = {}  # {chat_id: {"business_id": X, "telegram_user_id": Y}}

# Bizneslar telegram guruhlari (local storage - faylga saqlanadi)
BUSINESS_GROUPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "business_groups.json")
business_telegram_groups = {}  # {business_id: {"group_id": "-100xxx", "group_name": "Guruh nomi"}}

def load_business_groups():
    """Fayldan guruh ma'lumotlarini yuklash"""
    global business_telegram_groups
    try:
        if os.path.exists(BUSINESS_GROUPS_FILE):
            with open(BUSINESS_GROUPS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Kalitlarni int ga aylantirish
                business_telegram_groups = {int(k): v for k, v in data.items()}
                logger.info(f"Guruh ma'lumotlari yuklandi: {len(business_telegram_groups)} ta")
    except Exception as e:
        logger.error(f"Guruh ma'lumotlarini yuklashda xato: {e}")
        business_telegram_groups = {}

def save_business_groups():
    """Guruh ma'lumotlarini faylga saqlash"""
    try:
        with open(BUSINESS_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(business_telegram_groups, f, ensure_ascii=False, indent=2)
        logger.info(f"Guruh ma'lumotlari saqlandi: {len(business_telegram_groups)} ta")
    except Exception as e:
        logger.error(f"Guruh ma'lumotlarini saqlashda xato: {e}")

# Dastur ishga tushganda guruhlarni yuklash
load_business_groups()

# Buyurtma xabarlari saqlash (message_id -> order_data)
order_group_messages = {}  # {message_id: {"order_id": X, "business_id": Y, "group_id": Z, "order_data": {...}}}

def send_order_to_business_group(order_id, business_id, order_data):
    """Biznes guruhiga yangi buyurtma xabari yuborish"""
    group_info = business_telegram_groups.get(business_id)
    if not group_info:
        logger.debug(f"Biznes #{business_id} uchun guruh topilmadi")
        return None

    group_id = group_info.get('group_id')
    if not group_id:
        return None

    # Buyurtma ma'lumotlari
    biznes_nomi = order_data.get('biznes_nomi', 'Nomalum')
    summa = order_data.get('summa', 0)
    yetkazish = order_data.get('yetkazish', 'DELIVERY')
    products = order_data.get('products', [])
    miqdor = order_data.get('miqdor', 1)
    created_at = order_data.get('created_at', '')

    # Vaqtni formatlash
    try:
        if created_at:
            from datetime import datetime
            if 'T' in str(created_at):
                dt = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
            else:
                dt = datetime.now()
            vaqt_str = dt.strftime("%H:%M:%S")
        else:
            vaqt_str = datetime.now().strftime("%H:%M:%S")
    except:
        vaqt_str = datetime.now().strftime("%H:%M:%S")

    # Yetkazish turi
    yetkazish_emoji = "🚗" if yetkazish == "DELIVERY" else "🏃"
    yetkazish_text = "Yetkazib berish" if yetkazish == "DELIVERY" else "Olib ketish"

    # Mahsulotlar ro'yxati
    products_text = ""
    if products:
        for i, p in enumerate(products, 1):
            products_text += f"  {i}. {p.get('name', 'Mahsulot')} x{p.get('quantity', 1)}\n"
    else:
        products_text = f"  • {order_data.get('mahsulot', 'Mahsulot')} x{miqdor}\n"

    # Xabar matni (mijoz ma'lumotlarisiz) - faqat kuzatish uchun
    text = f"""🆕 <b>YANGI BUYURTMA #{order_id}</b>

🏢 {biznes_nomi}
⏰ Qabul qilingan: {vaqt_str}
{yetkazish_emoji} {yetkazish_text}

📦 <b>Mahsulotlar:</b>
{products_text}
💰 <b>Jami:</b> {summa:,.0f} so'm

📊 <b>Holat:</b> ⏳ Kutilmoqda"""

    # Xabar yuborish (tugmalarsiz - faqat kuzatish)
    message_id = send_telegram_message_to_chat(group_id, text, None)

    if message_id:
        # Xabar ma'lumotlarini saqlash
        order_group_messages[message_id] = {
            "order_id": order_id,
            "business_id": business_id,
            "group_id": group_id,
            "order_data": order_data,
            "status": "pending",
            "created_at": vaqt_str
        }
        logger.info(f"Buyurtma #{order_id} guruhga yuborildi: {group_id}")

    return message_id

def update_order_status_in_group(message_id, new_status, status_time=None):
    """Guruhdagi buyurtma xabarini yangilash (API dan kelgan status)"""
    if message_id not in order_group_messages:
        return False

    msg_data = order_group_messages[message_id]
    old_status = msg_data.get('status', 'PENDING')

    # Agar status o'zgarmagan bo'lsa, yangilamaymiz
    if old_status == new_status:
        return False

    order_id = msg_data['order_id']
    group_id = msg_data['group_id']
    order_data = msg_data['order_data']
    created_at = msg_data.get('created_at', '')

    # Buyurtma ma'lumotlari
    biznes_nomi = order_data.get('biznes_nomi', 'Nomalum')
    summa = order_data.get('summa', 0)
    yetkazish = order_data.get('yetkazish', 'DELIVERY')
    products = order_data.get('products', [])
    miqdor = order_data.get('miqdor', 1)

    # Yetkazish turi
    yetkazish_emoji = "🚗" if yetkazish == "DELIVERY" else "🏃"
    yetkazish_text = "Yetkazib berish" if yetkazish == "DELIVERY" else "Olib ketish"

    # Mahsulotlar ro'yxati
    products_text = ""
    if products:
        for i, p in enumerate(products, 1):
            products_text += f"  {i}. {p.get('name', 'Mahsulot')} x{p.get('quantity', 1)}\n"
    else:
        products_text = f"  • {order_data.get('mahsulot', 'Mahsulot')} x{miqdor}\n"

    # Status emoji va text (API statuslar)
    status_map = {
        'PENDING': ('⏳', 'Kutilmoqda'),
        'ACCEPTED': ('✅', 'Qabul qilindi'),
        'PREPARING': ('👨‍🍳', 'Tayyorlanmoqda'),
        'READY': ('🍽', 'Tayyor'),
        'ON_THE_WAY': ('🚗', 'Yo\'lda'),
        'DELIVERED': ('📦', 'Yetkazildi'),
        'COMPLETED': ('✅', 'Bajarildi'),
        'CANCELLED': ('❌', 'Bekor qilindi'),
    }
    status_emoji, status_text = status_map.get(new_status, ('❓', new_status))

    # Vaqtni formatlash
    time_str = ""
    if status_time:
        try:
            if 'T' in str(status_time):
                dt = datetime.fromisoformat(str(status_time).replace('Z', '+00:00'))
                time_str = dt.strftime("%H:%M:%S")
            else:
                time_str = str(status_time)
        except:
            time_str = datetime.now().strftime("%H:%M:%S")

    # Xabar matni
    text = f"""📋 <b>BUYURTMA #{order_id}</b>

🏢 {biznes_nomi}
⏰ Qabul qilingan: {created_at}
{yetkazish_emoji} {yetkazish_text}

📦 <b>Mahsulotlar:</b>
{products_text}
💰 <b>Jami:</b> {summa:,.0f} so'm

📊 <b>Holat:</b> {status_emoji} {status_text}"""

    if time_str:
        text += f"\n⏱ Yangilangan: {time_str}"

    # Mijoz ma'lumotlarini ko'rsatish (tayyor yoki yetkazilmoqda bo'lganda)
    if new_status in ['READY', 'ON_THE_WAY', 'DELIVERED', 'COMPLETED']:
        mijoz_nomi = order_data.get('mijoz_nomi', 'Nomalum')
        mijoz_tel = order_data.get('mijoz_tel', '')
        if mijoz_tel:
            text += f"""

👤 <b>MIJOZ:</b>
📝 {mijoz_nomi}
📞 <code>{mijoz_tel}</code>"""

    # Xabarni tahrirlash (tugmalarsiz)
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        data = {
            "chat_id": group_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML"
        }

        response = requests.post(url, json=data, timeout=10)
        if response.status_code == 200:
            msg_data['status'] = new_status
            logger.info(f"Buyurtma #{order_id} holati yangilandi: {new_status}")
            return True
        elif response.status_code == 400:
            # Xabar o'zgartirilmagan - content ayniy
            msg_data['status'] = new_status
            return True
    except Exception as e:
        logger.error(f"Buyurtma holatini yangilashda xato: {e}")

    return False

# O'zbekiston viloyatlari
UZBEKISTAN_REGIONS = {
    1: "Toshkent shahri",
    2: "Toshkent viloyati",
    3: "Andijon viloyati",
    4: "Buxoro viloyati",
    5: "Farg'ona viloyati",
    6: "Jizzax viloyati",
    7: "Xorazm viloyati",
    8: "Namangan viloyati",
    9: "Navoiy viloyati",
    10: "Qashqadaryo viloyati",
    11: "Qoraqalpog'iston",
    12: "Samarqand viloyati",
    13: "Sirdaryo viloyati",
    14: "Surxondaryo viloyati",
}

def fetch_businesses_from_api():
    """API dan bizneslar ro'yxatini olish"""
    global businesses_cache, businesses_cache_time

    try:
        # Cache 5 daqiqa davomida amal qiladi
        if businesses_cache_time and (datetime.now() - businesses_cache_time).seconds < 300:
            return businesses_cache

        response = requests.get(NONBOR_BUSINESSES_URL, headers=get_nonbor_headers(), timeout=30)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                businesses_cache = data
            elif isinstance(data, dict):
                businesses_cache = data.get('result', data.get('results', data.get('businesses', data.get('data', []))))
            businesses_cache_time = datetime.now()
            logger.info(f"API dan {len(businesses_cache)} ta biznes olindi")
            return businesses_cache
    except Exception as e:
        logger.error(f"Bizneslarni olishda xato: {e}")

    return businesses_cache if businesses_cache else []

def find_business_by_telegram_id(telegram_user_id):
    """Telegram user ID bo'yicha biznesni topish"""
    try:
        # API dan biznesni qidirish
        url = f"{NONBOR_BASE_URL}/telegram_bot/get-business-by-telegram/"
        params = {"telegram_id": telegram_user_id}
        response = requests.get(url, headers=get_nonbor_headers(), params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if data.get('success') and data.get('business'):
                return data.get('business')
    except Exception as e:
        logger.error(f"Biznesni telegram_id bo'yicha topishda xato: {e}")

    # Agar API ishlamasa, cache dan qidirish
    all_businesses = fetch_businesses_from_api()
    for business in all_businesses:
        # owner_telegram_id yoki telegram_id maydoni bo'yicha tekshirish
        owner_tg = business.get('owner_telegram_id') or business.get('telegram_id') or business.get('owner', {}).get('telegram_id')
        if str(owner_tg) == str(telegram_user_id):
            return business
    return None

def get_business_statistics(business_id, telegram_user_id=None):
    """Biznes statistikasini olish"""
    try:
        # API dan statistika olish
        url = NONBOR_BUSINESS_STATS_URL
        params = {"business_id": business_id}
        if telegram_user_id:
            params["telegram_id"] = telegram_user_id

        response = requests.get(url, headers=get_nonbor_headers(), params=params, timeout=30)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"Biznes statistikasini olishda xato: {e}")

    return None

def format_business_stats_message(business, stats):
    """Biznes statistikasi xabarini formatlash"""
    business_name = business.get('name', 'Noma\'lum')

    # Statistika ma'lumotlari
    total_orders = stats.get('total_orders', 0)
    completed = stats.get('completed', 0)
    cancelled = stats.get('cancelled', 0)
    in_progress = stats.get('in_progress', 0)
    pending = stats.get('pending', 0)
    total_revenue = stats.get('total_revenue', 0)
    today_orders = stats.get('today_orders', 0)
    today_revenue = stats.get('today_revenue', 0)

    # Format qilish
    text = f"""📊 <b>{business_name}</b> statistikasi

📅 <b>Bugungi ko'rsatkichlar:</b>
   📦 Buyurtmalar: <b>{today_orders}</b> ta
   💰 Tushum: <b>{today_revenue:,.0f}</b> so'm

📈 <b>Umumiy ko'rsatkichlar:</b>
   📦 Jami buyurtmalar: <b>{total_orders}</b> ta
   ✅ Bajarilgan: <b>{completed}</b> ta
   ❌ Bajarilmagan: <b>{cancelled}</b> ta
   ⏳ Jarayonda: <b>{in_progress}</b> ta
   🕐 Kutilmoqda: <b>{pending}</b> ta
   💵 Jami tushum: <b>{total_revenue:,.0f}</b> so'm
"""

    # Foiz hisoblash
    if total_orders > 0:
        completed_percent = (completed / total_orders) * 100
        cancelled_percent = (cancelled / total_orders) * 100
        text += f"""
📉 <b>Samaradorlik:</b>
   ✅ Bajarilish: <b>{completed_percent:.1f}%</b>
   ❌ Bekor qilinish: <b>{cancelled_percent:.1f}%</b>
"""

    return text

def send_business_owner_stats(chat_id, telegram_user_id):
    """Biznes egasiga statistika yuborish"""
    # Biznesni topish
    business = find_business_by_telegram_id(telegram_user_id)

    if not business:
        text = """❌ <b>Biznes topilmadi</b>

Sizning Telegram hisobingiz hech qanday biznesga biriktirilmagan.

Biznes egasi bo'lsangiz, admin bilan bog'laning."""
        send_telegram_message_to_chat(chat_id, text)
        return False

    business_id = business.get('id')
    business_name = business.get('title') or business.get('name') or 'Nomalum'

    # Statistikani olish
    stats = get_business_statistics(business_id, telegram_user_id)

    if stats:
        text = format_business_stats_message(business, stats)
    else:
        # Agar API dan statistika kelmasa, asosiy ma'lumotlarni ko'rsatish
        text = f"""📊 <b>{business_name}</b>

ℹ️ Statistika ma'lumotlari yuklanmoqda...

Tez orada batafsil statistika mavjud bo'ladi."""

    # Guruh holati
    group_info = business_telegram_groups.get(business_id)
    if group_info:
        g_name = group_info.get('group_name', 'Guruh')
        text += f"\n\n💬 Ulangan guruh: <b>{g_name}</b>"

    # Keyboard yaratish
    keyboard = {"inline_keyboard": []}

    # Guruh qo'shish/o'zgartirish tugmasi
    if group_info:
        keyboard["inline_keyboard"].append([
            {"text": "💬 Guruhni o'zgartirish", "callback_data": f"owner_change_group_{business_id}"},
            {"text": "🗑 Guruhni o'chirish", "callback_data": f"owner_remove_group_{business_id}"}
        ])
    else:
        keyboard["inline_keyboard"].append([
            {"text": "➕ Guruh qo'shish", "callback_data": f"owner_add_group_{business_id}"}
        ])

    # Yangilash tugmasi
    keyboard["inline_keyboard"].append([
        {"text": "🔄 Yangilash", "callback_data": f"owner_refresh_{business_id}"}
    ])

    send_telegram_message_to_chat(chat_id, text, keyboard)
    return True

def send_telegram_message_to_chat(chat_id, text, reply_markup=None):
    """Istalgan chatga xabar yuborish"""
    if not TELEGRAM_BOT_TOKEN:
        return None

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        response = requests.post(url, json=data, timeout=10)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"Xabar yuborishda xato (chat_id={chat_id}): {e}")
    return None

def send_telegram_message_with_keyboard(text, keyboard):
    """Admin chatga keyboard bilan xabar yuborish"""
    return send_telegram_message_to_chat(ADMIN_CHAT_ID, text, keyboard)

def get_businesses_list_text(page=1, region_id=None, district_id=None):
    """Bizneslar ro'yxatini yaratish - matn + raqam tugmalari"""
    global businesses_current_page, businesses_current_region, businesses_current_district, businesses_filtered_list

    businesses_current_page = page
    businesses_current_region = region_id
    businesses_current_district = district_id

    all_businesses = fetch_businesses_from_api()

    # Filtrangan ro'yxatni saqlash
    businesses_filtered_list = all_businesses

    total = len(all_businesses)
    total_pages = max(1, (total + BUSINESSES_PER_PAGE - 1) // BUSINESSES_PER_PAGE)

    # Pagination
    start = (page - 1) * BUSINESSES_PER_PAGE
    end = start + BUSINESSES_PER_PAGE
    page_businesses = all_businesses[start:end]

    text = f"🏢 <b>FAOL BIZNESLAR</b>\n\n"

    for i, business in enumerate(page_businesses, start + 1):
        b_id = business.get('id')
        name = business.get('name') or business.get('title') or 'Noma\'lum'
        phone = business.get('phone') or business.get('phone_number') or business.get('owner_phone') or ''

        # Telefon raqamini formatlash
        if phone:
            phone_clean = phone.replace('+', '').replace(' ', '')
            if phone_clean.startswith('998') and len(phone_clean) == 12:
                phone_formatted = f"+{phone_clean[:3]} {phone_clean[3:5]} {phone_clean[5:8]} {phone_clean[8:]}"
            else:
                phone_formatted = phone
        else:
            phone_formatted = ""

        # Telegram guruh holati
        group_info = business_telegram_groups.get(b_id)
        group_emoji = "✅" if group_info else "❌"

        text += f"{group_emoji} <b>{i}. {name}</b>\n"
        if phone_formatted:
            text += f"📞 {phone_formatted}\n"

        # Guruh nomi (agar ulangan bo'lsa) - telefon ostida
        if group_info:
            g_name = group_info.get('group_name', 'Guruh')
            g_id = group_info.get('group_id')
            # Guruh linkini yaratish (private guruhlar uchun t.me/c/xxx/1 format)
            if g_id and str(g_id).startswith('-100'):
                chat_id_clean = str(g_id)[4:]  # -100 ni olib tashlash
                group_link = f"https://t.me/c/{chat_id_clean}/1"
                text += f"💬 <a href=\"{group_link}\">{g_name}</a>\n"
            elif g_id and str(g_id).startswith('-'):
                chat_id_clean = str(g_id)[1:]  # - ni olib tashlash
                group_link = f"https://t.me/c/{chat_id_clean}/1"
                text += f"💬 <a href=\"{group_link}\">{g_name}</a>\n"
            else:
                text += f"💬 {g_name}\n"

        text += "\n"

    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📊 Jami: <b>{total}</b> ta biznes\n"
    text += f"📄 Sahifa: <b>{page}/{total_pages}</b>"

    return text, total_pages

def get_business_detail_text(business_id):
    """Bitta biznes tafsilotlari"""
    all_businesses = fetch_businesses_from_api()
    business = None
    for b in all_businesses:
        if b.get('id') == business_id:
            business = b
            break

    if not business:
        return "Biznes topilmadi.", None

    name = business.get('name') or business.get('title') or 'Noma\'lum'
    owner = business.get('owner_name', business.get('owner', {}).get('name', ''))
    phone = business.get('phone') or business.get('phone_number') or business.get('owner_phone') or ''
    region = business.get('region', {}).get('name', '') if isinstance(business.get('region'), dict) else ''
    district = business.get('district', {}).get('name', '') if isinstance(business.get('district'), dict) else ''
    is_active = business.get('is_active', True)

    # Telegram guruh
    group_info = business_telegram_groups.get(business_id)

    text = f"""🏢 <b>{name}</b>

📋 <b>Ma'lumotlar:</b>
   👤 Egasi: {owner or 'Kiritilmagan'}
   📞 Telefon: {phone or 'Kiritilmagan'}
   📍 Manzil: {region}, {district}
   {'🟢 Faol' if is_active else '🔴 Nofaol'}

💬 <b>Telegram guruh:</b>
"""

    if group_info:
        text += "   ✅ Ulangan\n"
        g_name = group_info.get('group_name', 'Nomalum')
        g_id = group_info.get('group_id', 'Nomalum')
        text += f"   📝 Nomi: {g_name}\n"
        text += f"   🆔 ID: {g_id}\n"
    else:
        text += "   ❌ Biriktirilmagan\n"

    text += f"\n⏰ Yangilangan: {datetime.now().strftime('%H:%M:%S')}"

    return text, business

def get_business_detail_keyboard(business_id):
    """Biznes tafsilotlari uchun keyboard"""
    group_info = business_telegram_groups.get(business_id)

    keyboard = []

    if group_info:
        keyboard.append([
            {"text": "✏️ Guruhni tahrirlash", "callback_data": f"biz_edit_group_{business_id}"},
            {"text": "🗑 Guruhni o'chirish", "callback_data": f"biz_remove_group_{business_id}"}
        ])
    else:
        keyboard.append([
            {"text": "➕ Guruh qo'shish", "callback_data": f"biz_add_group_{business_id}"}
        ])

    keyboard.append([
        {"text": "🔙 Ro'yxatga qaytish", "callback_data": "biz_back_list"}
    ])

    return {"inline_keyboard": keyboard}

def get_region_business_counts():
    """Har bir viloyatdagi bizneslar sonini hisoblash"""
    all_businesses = fetch_businesses_from_api()
    counts = {}
    for rid in UZBEKISTAN_REGIONS.keys():
        count = len([b for b in all_businesses if b.get('region_id') == rid or b.get('region', {}).get('id') == rid])
        counts[rid] = count
    return counts

def get_districts_with_businesses(region_id):
    """Viloyatdagi bizneslari bor tumanlar ro'yxati"""
    all_businesses = fetch_businesses_from_api()
    districts = {}

    for b in all_businesses:
        b_region_id = b.get('region_id') or b.get('region', {}).get('id')
        if b_region_id == region_id:
            district = b.get('district', {})
            if isinstance(district, dict) and district.get('id'):
                d_id = district.get('id')
                d_name = district.get('name', f'Tuman #{d_id}')
                if d_id not in districts:
                    districts[d_id] = {'name': d_name, 'count': 0}
                districts[d_id]['count'] += 1

    return districts

def get_businesses_keyboard(page=1, total_pages=1, region_id=None, district_id=None):
    """Bizneslar uchun inline keyboard - har bir biznes nomini bosib kirish mumkin"""
    keyboard = []
    all_businesses = fetch_businesses_from_api()

    # Pagination
    start = (page - 1) * BUSINESSES_PER_PAGE
    end = start + BUSINESSES_PER_PAGE
    page_businesses = all_businesses[start:end]

    # Raqam tugmalari - 7 ta bir qatorda
    row = []
    for i, business in enumerate(page_businesses, start + 1):
        b_id = business.get('id')

        row.append({
            "text": f"{i}",
            "callback_data": f"biz_select_{b_id}"
        })

        # Har 7 ta tugmadan keyin yangi qator
        if len(row) == 7:
            keyboard.append(row)
            row = []

    # Qolgan tugmalarni qo'shish
    if row:
        keyboard.append(row)

    # Pagination (agar kerak bo'lsa)
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append({"text": "⬅️ Oldingi", "callback_data": f"biz_page_{page-1}"})
        nav_row.append({"text": f"{page}/{total_pages}", "callback_data": "biz_info"})
        if page < total_pages:
            nav_row.append({"text": "Keyingi ➡️", "callback_data": f"biz_page_{page+1}"})
        keyboard.append(nav_row)

    # Boshqaruv tugmalari
    keyboard.append([
        {"text": "🔄 Yangilash", "callback_data": "biz_refresh"}
    ])
    keyboard.append([
        {"text": "🔙 Asosiy menyu", "callback_data": "menu_back"}
    ])

    return {"inline_keyboard": keyboard}

def send_businesses_message(page=1, region_id=None, district_id=None):
    """Bizneslar xabarini yuborish"""
    text, total_pages = get_businesses_list_text(page, region_id, district_id)
    keyboard = get_businesses_keyboard(page, total_pages, region_id, district_id)

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"Bizneslar xabarini yuborishda xato: {e}")
    return None

def edit_businesses_message(message_id, page=1, region_id=None, district_id=None):
    """Bizneslar xabarini yangilash"""
    text, total_pages = get_businesses_list_text(page, region_id, district_id)
    keyboard = get_businesses_keyboard(page, total_pages, region_id, district_id)

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
    except Exception as e:
        logger.error(f"Bizneslar xabarini yangilashda xato: {e}")
    return False

def send_stats_with_buttons(period="daily"):
    """Statistika xabarini tugmalar bilan yuborish"""
    text = get_bot_statistics_text(period)

    # Inline keyboard tugmalari
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📅 Kunlik", "callback_data": "stats_daily"},
                {"text": "📆 Haftalik", "callback_data": "stats_weekly"}
            ],
            [
                {"text": "🗓 Oylik", "callback_data": "stats_monthly"},
                {"text": "📊 Yillik", "callback_data": "stats_yearly"}
            ],
            [
                {"text": "📦 Buyurtmalar", "callback_data": "menu_orders"},
                {"text": "📞 Qo'ng'iroqlar", "callback_data": "menu_calls"}
            ],
            [
                {"text": "🏢 Bizneslar", "callback_data": "menu_businesses"},
                {"text": "🔄 Yangilash", "callback_data": f"stats_{period}"}
            ]
        ]
    }

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"Statistika tugmalar bilan yuborildi ({period})")
            return response.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"Tugmali xabar yuborishda xato: {e}")
    return None

def edit_stats_message(message_id, period="daily"):
    """Mavjud xabarni yangilash"""
    text = get_bot_statistics_text(period)

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📅 Kunlik", "callback_data": "stats_daily"},
                {"text": "📆 Haftalik", "callback_data": "stats_weekly"}
            ],
            [
                {"text": "🗓 Oylik", "callback_data": "stats_monthly"},
                {"text": "📊 Yillik", "callback_data": "stats_yearly"}
            ],
            [
                {"text": "📦 Buyurtmalar", "callback_data": "menu_orders"},
                {"text": "📞 Qo'ng'iroqlar", "callback_data": "menu_calls"}
            ],
            [
                {"text": "🏢 Bizneslar", "callback_data": "menu_businesses"},
                {"text": "🔄 Yangilash", "callback_data": f"stats_{period}"}
            ]
        ]
    }

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"Statistika xabari yangilandi ({period})")
            return True
    except Exception as e:
        logger.error(f"Xabarni yangilashda xato: {e}")
    return False

def answer_callback_query(callback_query_id, text=""):
    """Callback query ga javob berish"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Callback query javobida xato: {e}")

def edit_message_with_back(message_id, text):
    """Xabarni orqaga tugmasi bilan yangilash"""
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🔄 Yangilash", "callback_data": "menu_refresh"},
                {"text": "🔙 Orqaga", "callback_data": "menu_back"}
            ]
        ]
    }

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
    except Exception as e:
        logger.error(f"Xabarni yangilashda xato: {e}")
    return False

def edit_message_with_cancel(message_id, text):
    """Xabarni bekor qilish tugmasi bilan yangilash"""
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "❌ Bekor qilish", "callback_data": "biz_cancel_input"}
            ]
        ]
    }

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
    except Exception as e:
        logger.error(f"Xabarni yangilashda xato: {e}")
    return False

def edit_message_with_keyboard(message_id, text, keyboard):
    """Xabarni keyboard bilan yangilash"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
    except Exception as e:
        logger.error(f"Xabarni yangilashda xato: {e}")
    return False

def send_business_detail(business_id):
    """Biznes tafsilotlarini yuborish"""
    text, business = get_business_detail_text(business_id)
    keyboard = get_business_detail_keyboard(business_id)

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"Biznes tafsilotini yuborishda xato: {e}")
    return None

def delete_bot_commands_for_groups():
    """Guruhlar uchun bot komandalarini o'chirish"""
    try:
        # Barcha guruhlar uchun komandalarni o'chirish
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMyCommands"
        data = {
            "scope": {
                "type": "all_group_chats"
            }
        }
        response = requests.post(url, json=data, timeout=10)
        if response.status_code == 200 and response.json().get("ok"):
            logger.info("Guruhlar uchun bot komandalari o'chirildi")
        else:
            logger.warning(f"Bot komandalarini o'chirishda xato: {response.text}")
    except Exception as e:
        logger.error(f"Bot komandalarini o'chirishda xato: {e}")

async def telegram_bot_polling():
    """Telegram bot buyruqlarini tinglash"""
    global telegram_last_update_id, waiting_for_group_id, selected_business_id, businesses_cache_time, waiting_for_phone_edit, pending_phone_change, owner_waiting_for_group

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        logger.warning("Telegram credentials yo'q - bot polling o'chirilgan")
        return

    # Guruhlar uchun komandalarni o'chirish
    delete_bot_commands_for_groups()

    logger.info("Telegram bot polling boshlandi...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {
                "offset": telegram_last_update_id + 1,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"]
            }

            response = requests.get(url, params=params, timeout=35)

            if response.status_code == 200:
                data = response.json()

                if data.get("ok") and data.get("result"):
                    for update in data["result"]:
                        telegram_last_update_id = update["update_id"]

                        # Callback query (tugma bosilganda)
                        callback_query = update.get("callback_query")
                        if callback_query:
                            cb_id = callback_query.get("id")
                            cb_data = callback_query.get("data", "")
                            cb_message = callback_query.get("message", {})
                            cb_chat_id = cb_message.get("chat", {}).get("id")
                            cb_message_id = cb_message.get("message_id")

                            if str(cb_chat_id) == str(TELEGRAM_ADMIN_CHAT_ID):
                                # Statistika tugmalari
                                if cb_data.startswith("stats_"):
                                    period = cb_data.replace("stats_", "")
                                    if period in ["daily", "weekly", "monthly", "yearly"]:
                                        edit_stats_message(cb_message_id, period)
                                        answer_callback_query(cb_id, f"✅ {period.capitalize()} statistika")
                                        logger.info(f"Telegram: {period} statistika so'raldi")

                                # Menu tugmalari
                                elif cb_data == "menu_orders":
                                    text = get_orders_list_text()
                                    edit_message_with_back(cb_message_id, text)
                                    answer_callback_query(cb_id, "📦 Buyurtmalar")
                                    logger.info("Telegram: Buyurtmalar so'raldi")

                                elif cb_data == "menu_calls":
                                    text = get_calls_history_text()
                                    edit_message_with_back(cb_message_id, text)
                                    answer_callback_query(cb_id, "📞 Qo'ng'iroqlar")
                                    logger.info("Telegram: Qo'ng'iroqlar so'raldi")

                                elif cb_data == "menu_businesses":
                                    edit_businesses_message(cb_message_id, 1, None, None)
                                    answer_callback_query(cb_id, "🏢 Bizneslar")
                                    logger.info("Telegram: Bizneslar so'raldi")

                                # Bizneslar tugmalari
                                elif cb_data.startswith("biz_page_"):
                                    page = int(cb_data.replace("biz_page_", ""))
                                    edit_businesses_message(cb_message_id, page, businesses_current_region, businesses_current_district)
                                    answer_callback_query(cb_id, f"📄 {page}-sahifa")
                                    logger.info(f"Telegram: Bizneslar {page}-sahifa")

                                elif cb_data.startswith("biz_region_"):
                                    region_id = int(cb_data.replace("biz_region_", ""))
                                    edit_businesses_message(cb_message_id, 1, region_id, None)
                                    region_name = UZBEKISTAN_REGIONS.get(region_id, "")
                                    answer_callback_query(cb_id, f"📍 {region_name}")
                                    logger.info(f"Telegram: Bizneslar {region_name} filtri")

                                elif cb_data.startswith("biz_district_"):
                                    district_id = int(cb_data.replace("biz_district_", ""))
                                    # Agar shu tuman tanlangan bo'lsa - o'chirish
                                    new_district = None if businesses_current_district == district_id else district_id
                                    edit_businesses_message(cb_message_id, 1, businesses_current_region, new_district)
                                    answer_callback_query(cb_id, f"🏘 Tuman tanlandi")
                                    logger.info(f"Telegram: Bizneslar tuman #{district_id} filtri")

                                elif cb_data == "biz_refresh":
                                    businesses_cache_time = None  # Cache ni tozalash
                                    edit_businesses_message(cb_message_id, businesses_current_page, businesses_current_region, businesses_current_district)
                                    answer_callback_query(cb_id, "🔄 Yangilandi")
                                    logger.info("Telegram: Bizneslar yangilandi")

                                elif cb_data == "biz_clear":
                                    edit_businesses_message(cb_message_id, 1, None, None)
                                    answer_callback_query(cb_id, "❌ Filtr tozalandi")
                                    logger.info("Telegram: Bizneslar filtri tozalandi")

                                elif cb_data == "biz_info":
                                    answer_callback_query(cb_id, f"📄 {businesses_current_page}-sahifa")

                                elif cb_data.startswith("biz_select_"):
                                    # Biznes tanlandi - tafsilotlarni ko'rsatish
                                    b_id = int(cb_data.replace("biz_select_", ""))
                                    text, business = get_business_detail_text(b_id)
                                    keyboard = get_business_detail_keyboard(b_id)
                                    edit_message_with_keyboard(cb_message_id, text, keyboard)
                                    business_name = business.get('title', business.get('name', 'Biznes')) if business else 'Biznes'
                                    answer_callback_query(cb_id, f"🏢 {business_name[:30]}")
                                    logger.info(f"Telegram: Biznes #{b_id} tafsilotlari")

                                elif cb_data.startswith("biz_add_group_") or cb_data.startswith("biz_edit_group_"):
                                    selected_business_id = int(cb_data.split("_")[-1])
                                    waiting_for_group_id = True
                                    # Guruh ID so'rash xabari
                                    prompt_text = """💬 <b>TELEGRAM GURUH QO'SHISH</b>

Guruh ID va nomini quyidagi formatda yuboring:

<code>-1001234567890 Guruh nomi</code>

Guruh ID ni olish uchun:
1. Botni guruhga qo'shing
2. @userinfobot dan guruh ID sini oling

🔙 Bekor qilish uchun /cancel yuboring"""
                                    edit_message_with_cancel(cb_message_id, prompt_text)
                                    answer_callback_query(cb_id, "💬 Guruh ID kiriting")
                                    logger.info(f"Telegram: Biznes #{selected_business_id} uchun guruh qo'shish")

                                elif cb_data.startswith("biz_remove_group_"):
                                    b_id = int(cb_data.split("_")[-1])
                                    if b_id in business_telegram_groups:
                                        del business_telegram_groups[b_id]
                                        save_business_groups()  # Faylga saqlash
                                        logger.info(f"Biznes #{b_id} guruhi o'chirildi")
                                    # Biznes tafsilotlariga qaytish
                                    text, business = get_business_detail_text(b_id)
                                    keyboard = get_business_detail_keyboard(b_id)
                                    edit_message_with_keyboard(cb_message_id, text, keyboard)
                                    answer_callback_query(cb_id, "🗑 Guruh o'chirildi")

                                elif cb_data == "biz_back_list":
                                    edit_businesses_message(cb_message_id, businesses_current_page, businesses_current_region, businesses_current_district)
                                    answer_callback_query(cb_id, "🔙 Ro'yxat")
                                    logger.info("Telegram: Bizneslar ro'yxatiga qaytildi")

                                elif cb_data == "biz_cancel_input":
                                    waiting_for_group_id = False
                                    waiting_for_phone_edit = False
                                    selected_business_id = None
                                    pending_phone_change.clear()
                                    edit_businesses_message(cb_message_id, businesses_current_page, businesses_current_region, businesses_current_district)
                                    answer_callback_query(cb_id, "❌ Bekor qilindi")

                                # Telefon raqamini o'zgartirish
                                elif cb_data.startswith("biz_edit_phone_"):
                                    b_id = int(cb_data.split("_")[-1])
                                    selected_business_id = b_id
                                    waiting_for_phone_edit = True
                                    logger.info(f"DEBUG: Phone edit mode ON - b_id={b_id}, waiting_for_phone_edit={waiting_for_phone_edit}")

                                    # Joriy telefon raqamini olish
                                    all_businesses = fetch_businesses_from_api()
                                    current_phone = ""
                                    for b in all_businesses:
                                        if b.get('id') == b_id:
                                            current_phone = b.get('phone') or b.get('phone_number') or b.get('owner_phone') or ''
                                            break

                                    prompt_text = f"""📞 <b>Telefon raqamini o'zgartirish</b>

Joriy telefon: <code>{current_phone or 'Kiritilmagan'}</code>

Yangi telefon raqamini yuboring:
Masalan: <code>+998901234567</code>

🔙 Bekor qilish uchun tugmani bosing"""
                                    keyboard = {"inline_keyboard": [[{"text": "❌ Bekor qilish", "callback_data": "biz_cancel_phone"}]]}
                                    edit_message_with_keyboard(cb_message_id, prompt_text, keyboard)
                                    answer_callback_query(cb_id, "📞 Yangi raqam kiriting")
                                    logger.info(f"Telegram: Biznes #{b_id} telefon o'zgartirish")

                                elif cb_data == "biz_cancel_phone":
                                    waiting_for_phone_edit = False
                                    b_id = selected_business_id
                                    selected_business_id = None
                                    pending_phone_change.clear()
                                    if b_id:
                                        text, business = get_business_detail_text(b_id)
                                        keyboard = get_business_detail_keyboard(b_id)
                                        edit_message_with_keyboard(cb_message_id, text, keyboard)
                                    answer_callback_query(cb_id, "❌ Bekor qilindi")

                                elif cb_data == "biz_confirm_phone":
                                    # Telefon o'zgarishini tasdiqlash
                                    if pending_phone_change:
                                        b_id = pending_phone_change.get('business_id')
                                        new_phone = pending_phone_change.get('new_phone')

                                        # API ga yangi telefon yuborish
                                        try:
                                            api_url = f"https://test.nonbor.uz/api/v2/telegram_bot/update-business-phone/"
                                            headers = {
                                                "Content-Type": "application/json",
                                                "X-Telegram-Bot-Secret": NONBOR_API_SECRET
                                            }
                                            payload = {
                                                "business_id": b_id,
                                                "phone": new_phone
                                            }
                                            response = requests.post(api_url, json=payload, headers=headers, timeout=10)

                                            if response.status_code == 200:
                                                # Cache ni tozalash
                                                businesses_cache.clear()

                                                send_telegram_message(f"✅ Telefon muvaffaqiyatli o'zgartirildi!\n\n📞 Yangi raqam: <code>{new_phone}</code>")
                                                logger.info(f"Biznes #{b_id} telefon o'zgartirildi: {new_phone}")
                                            else:
                                                send_telegram_message(f"❌ Xatolik: {response.status_code}\n{response.text[:200]}")
                                                logger.error(f"Telefon o'zgartirishda xato: {response.status_code} - {response.text}")
                                        except Exception as e:
                                            send_telegram_message(f"❌ Xatolik: {str(e)}")
                                            logger.error(f"Telefon o'zgartirishda xato: {e}")

                                        pending_phone_change.clear()
                                        waiting_for_phone_edit = False
                                        selected_business_id = None

                                        # Biznes tafsilotlarini ko'rsatish
                                        send_business_detail(b_id)
                                    answer_callback_query(cb_id, "✅ Tasdiqlandi")

                                elif cb_data == "biz_reject_phone":
                                    # Telefon o'zgarishini rad etish
                                    b_id = pending_phone_change.get('business_id')
                                    pending_phone_change.clear()
                                    waiting_for_phone_edit = False
                                    selected_business_id = None
                                    if b_id:
                                        text, business = get_business_detail_text(b_id)
                                        keyboard = get_business_detail_keyboard(b_id)
                                        edit_message_with_keyboard(cb_message_id, text, keyboard)
                                    answer_callback_query(cb_id, "❌ Bekor qilindi")

                                elif cb_data == "menu_back":
                                    edit_stats_message(cb_message_id, "daily")
                                    answer_callback_query(cb_id, "🔙 Orqaga")
                                    logger.info("Telegram: Orqaga qaytildi")

                                elif cb_data == "menu_refresh":
                                    # Joriy sahifani yangilash
                                    answer_callback_query(cb_id, "🔄 Yangilandi")
                                    logger.info("Telegram: Sahifa yangilandi")

                            else:
                                # Biznes egasi callback handerlari (admin bo'lmagan chatlar)
                                cb_from_user = callback_query.get("from", {})
                                cb_telegram_user_id = cb_from_user.get("id")

                                # Guruh qo'shish
                                if cb_data.startswith("owner_add_group_") or cb_data.startswith("owner_change_group_"):
                                    b_id = int(cb_data.split("_")[-1])
                                    owner_waiting_for_group[cb_chat_id] = {
                                        "business_id": b_id,
                                        "telegram_user_id": cb_telegram_user_id
                                    }
                                    prompt_text = """💬 <b>Guruh qo'shish</b>

1️⃣ Avval botni guruhga qo'shing
2️⃣ Guruhda <code>/getid</code> buyrug'ini yuboring
3️⃣ Bot guruh ID sini ko'rsatadi
4️⃣ Shu ID ni menga yuboring

<b>Format:</b> <code>-1001234567890 Guruh nomi</code>"""
                                    cancel_keyboard = {"inline_keyboard": [[{"text": "❌ Bekor qilish", "callback_data": "owner_cancel_group"}]]}
                                    send_telegram_message_to_chat(cb_chat_id, prompt_text, cancel_keyboard)
                                    answer_callback_query(cb_id, "💬 Guruh ID kiriting")
                                    logger.info(f"Biznes egasi guruh qo'shmoqda: business_id={b_id}")

                                # Guruhni o'chirish
                                elif cb_data.startswith("owner_remove_group_"):
                                    b_id = int(cb_data.split("_")[-1])
                                    if b_id in business_telegram_groups:
                                        del business_telegram_groups[b_id]
                                        save_business_groups()
                                        send_telegram_message_to_chat(cb_chat_id, "✅ Guruh muvaffaqiyatli o'chirildi!")
                                        # Statistikani qayta ko'rsatish
                                        send_business_owner_stats(cb_chat_id, cb_telegram_user_id)
                                        answer_callback_query(cb_id, "✅ Guruh o'chirildi")
                                        logger.info(f"Biznes egasi guruhni o'chirdi: business_id={b_id}")
                                    else:
                                        answer_callback_query(cb_id, "❌ Guruh topilmadi")

                                # Yangilash
                                elif cb_data.startswith("owner_refresh_"):
                                    b_id = int(cb_data.split("_")[-1])
                                    send_business_owner_stats(cb_chat_id, cb_telegram_user_id)
                                    answer_callback_query(cb_id, "🔄 Yangilandi")

                                # Bekor qilish
                                elif cb_data == "owner_cancel_group":
                                    if cb_chat_id in owner_waiting_for_group:
                                        del owner_waiting_for_group[cb_chat_id]
                                    send_telegram_message_to_chat(cb_chat_id, "❌ Bekor qilindi")
                                    answer_callback_query(cb_id, "❌ Bekor qilindi")

                            continue

                        # Oddiy xabar
                        message = update.get("message", {})
                        chat_id = message.get("chat", {}).get("id")
                        text = message.get("text", "")

                        # DEBUG: Telefon o'zgartirish holati
                        if text and not text.startswith("/"):
                            logger.info(f"DEBUG: text='{text}', waiting_for_phone_edit={waiting_for_phone_edit}, selected_business_id={selected_business_id}")

                        # Guruh xabarlarini tekshirish (chat_id manfiy bo'lsa - bu guruh)
                        is_group_chat = chat_id and int(chat_id) < 0

                        # Guruhda /getid buyrug'i - guruh ID sini ko'rsatish
                        if is_group_chat and text in ["/getid", "/getid@nonborsupport_bot", "/getid@Nonborbuyurtmalar_bot"]:
                            group_title = message.get("chat", {}).get("title", "Bu guruh")
                            getid_text = f"""🆔 <b>Guruh ma'lumotlari</b>

📝 Nomi: <b>{group_title}</b>
🆔 ID: <code>{chat_id}</code>

Botga yuborish uchun quyidagini nusxalang:
<code>{chat_id} {group_title}</code>"""
                            send_telegram_message_to_chat(chat_id, getid_text)
                            logger.info(f"Guruh ID so'raldi: {chat_id} ({group_title})")
                            continue

                        # @botname bilan kelgan buyruqlarni guruhlarda e'tiborsiz qoldirish
                        if is_group_chat and "@" in text:
                            # Guruhdan kelgan @botname buyruqlari e'tiborsiz qoldiriladi
                            logger.debug(f"Guruhdan kelgan buyruq e'tiborsiz qoldirildi: {text}")
                            continue

                        # Telegram user ID ni olish
                        from_user = message.get("from", {})
                        telegram_user_id = from_user.get("id")
                        user_first_name = from_user.get("first_name", "")

                        # Biznes egasi /stats buyrug'i - shaxsiy chatda
                        if not is_group_chat and text in ["/start", "/stats", "/statistika", "/stats@nonborsupport_bot"]:
                            # Admin bo'lmasa - biznes egasi sifatida tekshirish
                            if str(chat_id) != str(TELEGRAM_ADMIN_CHAT_ID):
                                logger.info(f"Biznes egasi statistika so'radi: {telegram_user_id} ({user_first_name})")
                                send_business_owner_stats(chat_id, telegram_user_id)
                                continue

                        # Biznes egasi guruh ID yubormoqda
                        if not is_group_chat and chat_id in owner_waiting_for_group and text and not text.startswith("/"):
                            owner_data = owner_waiting_for_group[chat_id]
                            b_id = owner_data.get("business_id")

                            try:
                                parts = text.strip().split(" ", 1)
                                group_id = parts[0]
                                group_name = parts[1] if len(parts) > 1 else "Telegram guruh"

                                # Guruh ID ni tekshirish
                                if not group_id.startswith("-"):
                                    send_telegram_message_to_chat(chat_id, "❌ Noto'g'ri format. Guruh ID '-' bilan boshlanishi kerak.\n\nMasalan: <code>-1001234567890 Guruh nomi</code>")
                                    continue

                                # Guruh ma'lumotlarini saqlash
                                business_telegram_groups[b_id] = {
                                    "group_id": group_id,
                                    "group_name": group_name
                                }
                                save_business_groups()

                                # Holatni tozalash
                                del owner_waiting_for_group[chat_id]

                                # Muvaffaqiyat xabari
                                success_text = f"✅ Guruh muvaffaqiyatli qo'shildi!\n\n📝 Nomi: {group_name}\n🆔 ID: {group_id}"
                                send_telegram_message_to_chat(chat_id, success_text)

                                # Statistikani qayta ko'rsatish
                                send_business_owner_stats(chat_id, telegram_user_id)
                                logger.info(f"Biznes egasi guruh qo'shdi: business_id={b_id}, group_id={group_id}")

                            except Exception as e:
                                send_telegram_message_to_chat(chat_id, f"❌ Xato: {e}\n\nTo'g'ri format: <code>-1001234567890 Guruh nomi</code>")
                                logger.error(f"Biznes egasi guruh qo'shishda xato: {e}")

                            continue

                        # Faqat admin chat_id dan kelgan xabarlarga javob berish
                        if str(chat_id) == str(TELEGRAM_ADMIN_CHAT_ID):
                            if text == "/start" or text == "/stats" or text == "/statistika":
                                send_stats_with_buttons("daily")
                                logger.info(f"Telegram: /start buyrug'iga javob yuborildi")

                            elif text == "/help":
                                help_text = """🤖 <b>NONBOR AUTODIALER BOT</b>

<b>Buyruqlar:</b>
/start - Statistikani ko'rish
/stats - Statistikani ko'rish
/help - Yordam

<b>Bot vazifasi:</b>
Qabul qilinmagan buyurtmalar haqida xabar yuborish.

Buyurtma kelganda:
1. 90 sek kutiladi
2. 2 marta qo'ng'iroq qilinadi
3. Javob bermasa - bu botga xabar yuboriladi

<b>Statistika tugmalari:</b>
📅 Kunlik - Bugungi statistika
📆 Haftalik - Oxirgi 7 kun
🗓 Oylik - Oxirgi 30 kun
📊 Yillik - Oxirgi 1 yil

<b>Boshqaruv tugmalari:</b>
📦 Buyurtmalar - Kutilayotgan buyurtmalar
📞 Qo'ng'iroqlar - Qo'ng'iroqlar tarixi
👨‍💼 Sotuvchilar - Faol sotuvchilar
🏢 Bizneslar - Barcha bizneslar (viloyat filtri bilan)
🔄 Yangilash - Ma'lumotlarni yangilash
"""
                                send_telegram_message(help_text)
                                logger.info(f"Telegram: /help buyrug'iga javob yuborildi")

                            elif text == "/cancel":
                                # Guruh qo'shishni bekor qilish
                                if waiting_for_group_id:
                                    waiting_for_group_id = False
                                    selected_business_id = None
                                    send_businesses_message(businesses_current_page, businesses_current_region, businesses_current_district)
                                    logger.info("Telegram: Guruh qo'shish bekor qilindi")
                                elif waiting_for_phone_edit:
                                    waiting_for_phone_edit = False
                                    b_id = selected_business_id
                                    selected_business_id = None
                                    pending_phone_change.clear()
                                    if b_id:
                                        send_business_detail(b_id)
                                    logger.info("Telegram: Telefon o'zgartirish bekor qilindi")
                                else:
                                    send_telegram_message("❌ Bekor qilinadigan jarayon yo'q")

                            elif waiting_for_phone_edit and selected_business_id:
                                # Telefon raqamini qabul qilish va tasdiqlash
                                new_phone = text.strip()

                                # Telefon raqamini tekshirish
                                phone_clean = new_phone.replace('+', '').replace(' ', '').replace('-', '')
                                if not phone_clean.isdigit() or len(phone_clean) < 9:
                                    send_telegram_message("❌ Noto'g'ri telefon raqam formati.\n\nMasalan: <code>+998901234567</code>")
                                    continue

                                # +998 bilan formatlash
                                if phone_clean.startswith('998') and len(phone_clean) == 12:
                                    new_phone = f"+{phone_clean}"
                                elif len(phone_clean) == 9 and phone_clean[0] in '789':
                                    new_phone = f"+998{phone_clean}"
                                elif not new_phone.startswith('+'):
                                    new_phone = f"+{phone_clean}"

                                # Tasdiqlash uchun saqlash
                                pending_phone_change['business_id'] = selected_business_id
                                pending_phone_change['new_phone'] = new_phone

                                # Joriy telefon raqamini olish
                                all_businesses = fetch_businesses_from_api()
                                current_phone = ""
                                business_name = ""
                                for b in all_businesses:
                                    if b.get('id') == selected_business_id:
                                        current_phone = b.get('phone') or b.get('phone_number') or b.get('owner_phone') or ''
                                        business_name = b.get('title') or b.get('name') or ''
                                        break

                                # Tasdiqlash xabari
                                confirm_text = f"""⚠️ <b>Telefon raqamini o'zgartirishni tasdiqlang</b>

🏢 Biznes: <b>{business_name}</b>

📞 Joriy raqam: <code>{current_phone or 'Kiritilmagan'}</code>
📱 Yangi raqam: <code>{new_phone}</code>

O'zgarishni tasdiqlaysizmi?"""

                                keyboard = {"inline_keyboard": [
                                    [
                                        {"text": "✅ Tasdiqlash", "callback_data": "biz_confirm_phone"},
                                        {"text": "❌ Bekor qilish", "callback_data": "biz_reject_phone"}
                                    ]
                                ]}
                                send_telegram_message_with_keyboard(confirm_text, keyboard)
                                logger.info(f"Telefon o'zgartirish tasdiqlanmoqda: {current_phone} -> {new_phone}")

                            elif waiting_for_group_id and selected_business_id:
                                # Guruh ID va nomini qabul qilish
                                try:
                                    parts = text.strip().split(" ", 1)
                                    group_id = parts[0]
                                    group_name = parts[1] if len(parts) > 1 else "Telegram guruh"

                                    # Guruh ID ni tekshirish
                                    if not group_id.startswith("-"):
                                        send_telegram_message("❌ Noto'g'ri format. Guruh ID '-' bilan boshlanishi kerak.\n\nMasalan: <code>-1001234567890 Guruh nomi</code>")
                                        continue

                                    # Guruh ma'lumotlarini saqlash
                                    business_telegram_groups[selected_business_id] = {
                                        "group_id": group_id,
                                        "group_name": group_name
                                    }
                                    save_business_groups()  # Faylga saqlash

                                    waiting_for_group_id = False
                                    b_id = selected_business_id
                                    selected_business_id = None

                                    # Muvaffaqiyat xabari va biznes tafsilotlari
                                    success_text = f"✅ Guruh muvaffaqiyatli qo'shildi!\n\n📝 Nomi: {group_name}\n🆔 ID: {group_id}"
                                    send_telegram_message(success_text)

                                    # Biznes tafsilotlarini ko'rsatish
                                    send_business_detail(b_id)
                                    logger.info(f"Biznes #{b_id} ga guruh qo'shildi: {group_id}")

                                except Exception as e:
                                    send_telegram_message(f"❌ Xato: {e}\n\nTo'g'ri format: <code>-1001234567890 Guruh nomi</code>")
                                    logger.error(f"Guruh qo'shishda xato: {e}")

                            elif text.isdigit() and businesses_filtered_list:
                                # Biznesni raqam bo'yicha tanlash
                                num = int(text)
                                if 1 <= num <= len(businesses_filtered_list):
                                    business = businesses_filtered_list[num - 1]
                                    b_id = business.get('id')
                                    send_business_detail(b_id)
                                    logger.info(f"Telegram: Biznes #{b_id} tanlandi")
                                else:
                                    send_telegram_message(f"❌ Noto'g'ri raqam. 1 dan {len(businesses_filtered_list)} gacha raqam kiriting.")

            await asyncio.sleep(1)

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"Telegram bot polling xatosi: {e}")
            await asyncio.sleep(5)

async def on_startup(app):
    """Server ishga tushganda polling boshlash"""
    asyncio.create_task(polling_task())
    asyncio.create_task(telegram_bot_polling())
    asyncio.create_task(cleanup_task())


def create_app():
    """Web application yaratish"""
    app = web.Application()

    # Asosiy endpoints
    app.router.add_post('/call-result', handle_call_result)  # IVR callback
    app.router.add_post('/status-change', handle_status_change)
    app.router.add_get('/test', handle_test)
    app.router.add_post('/test-call', handle_test_call)
    app.router.add_get('/test-telegram', handle_test_telegram)
    app.router.add_get('/test-alert', handle_test_alert)  # Test uchun darhol Telegram xabar yuborish
    app.router.add_get('/check-leads', handle_check_leads)

    # Webhook - Nonbor backend dan buyurtma statusi o'zgarganda
    app.router.add_post('/api/webhook/order-status', handle_order_webhook)
    app.router.add_get('/api/statistics', handle_statistics)

    # amoCRM Telephony Integration (Operator qo'ng'iroq qilishi)
    app.router.add_post('/api/amocrm/call', handle_amocrm_call)  # Click-to-Call
    app.router.add_post('/api/amocrm/call-event', handle_amocrm_call_event)  # Call events

    # Kiruvchi qo'ng'iroqlar
    app.router.add_post('/api/incoming-call', handle_incoming_call)
    app.router.add_post('/api/call-hangup', handle_call_hangup)

    # Qo'ng'iroq tarixi
    app.router.add_get('/api/call-history', handle_call_history)

    app.on_startup.append(on_startup)

    return app


if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("AUTODIALER V2 + NONBOR API")
    logger.info("=" * 50)
    logger.info(f"API: {NONBOR_API_URL}")
    logger.info(f"Status: {ORDER_STATUS_PENDING} (Kutilmoqda)")
    logger.info(f"Polling interval: {POLLING_INTERVAL} sek")
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
    logger.info("amoCRM Telephony (Operator qo'ng'iroqi):")
    logger.info("  POST /api/amocrm/call       - Click-to-Call (amoCRM dan)")
    logger.info("  POST /api/amocrm/call-event - Qo'ng'iroq holati")
    logger.info("-" * 50)
    logger.info("Kiruvchi qo'ng'iroqlar:")
    logger.info("  POST /api/incoming-call     - Kiruvchi qo'ng'iroq")
    logger.info("  POST /api/call-hangup       - Qo'ng'iroq tugadi")
    logger.info("  GET  /api/call-history      - Qo'ng'iroq tarixi")
    logger.info("=" * 50)

    web.run_app(app, host='0.0.0.0', port=8080)
