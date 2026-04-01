"""
TTS (Text-to-Speech) Servisi
Matnni ovozga aylantirish - 3 til qo'llab-quvvatlanadi (uz, ru, en)

Startup da barcha audio fayllar oldindan yaratiladi:
  - 3 til x 30 buyurtma = 90 ta order audio
  - 3 til x 1 reja = 3 ta planned audio
  Jami: 93 ta WAV fayl audio/cache/ papkada tayyor turadi
"""

import os
import asyncio
import logging
import hashlib
from pathlib import Path
from typing import Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# TILLAR KONFIGURATSIYASI - FAQAT 3 TIL
# ─────────────────────────────────────────────────────────────

LANG_VOICES = {
    "uz": "uz-UZ-MadinaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "en": "en-US-JennyNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
}
DEFAULT_LANG = "uz"
PRIMARY_LANGS = ["uz", "ru", "en", "zh"]

# Yangi buyurtma xabarlari ({count} → songa almashtiriladi)
ORDER_MESSAGES = {
    "uz": "Assalomu alaykum! Bu Nonbor xizmati. Sizda {count} ta yangi buyurtma keldi, iltimos ilovani tekshiring.",
    "ru": "Здравствуйте! Звонит сервис Нонбо́р. У вас {count} новых заказа, пожалуйста проверьте приложение.",
    "en": "Hello! This is Nonbor calling. You have {count} new orders, please check your app.",
    "zh": "您好！Nonbor来电通知。您有{count}个新订单，请查看您的应用。",
}

# Reja (scheduled) eslatma xabarlari - har til uchun 1 ta
PLANNED_MESSAGES = {
    "uz": "Assalomu alaykum! Bu Nonbor xizmati. Sizda rejalashtirilgan buyurtma bor. Buyurtmangizni tayyorlang.",
    "ru": "Здравствуйте! Звонит сервис Нонбо́р. У вас запланированный заказ. Пожалуйста, подготовьте ваш заказ.",
    "en": "Hello! This is Nonbor calling. You have a scheduled order. Please prepare your order.",
    "zh": "您好！Nonbor来电提醒。您有一个计划订单，请准备好您的订单。",
}

# Admin: yangi biznes qo'ng'iroq xabarlari
ADMIN_NEW_BUSINESS_MESSAGES = {
    "uz": "Assalomu alaykum! Nonbor platformasida yangi biznes ochildi. Hozirda {count} ta restoran tekshiruv holatida.",
    "ru": "Здравствуйте! Звонит платформа Нонбор. Зарегистрирован новый бизнес. Сейчас {count} ресторанов на проверке.",
    "en": "Hello! A new business has registered on Nonbor. Currently {count} restaurants are in checking status.",
    "zh": "您好！Nonbor平台有新商家注册。目前有{count}家餐厅正在审核中。",
}

# Admin: kunlik hisobot xabarlari
ADMIN_DAILY_REPORT_MESSAGES = {
    "uz": "Assalomu alaykum! Nonbor hisoboti. Hozirda {biz_count} ta biznes va {product_count} ta mahsulot tekshiruv holatida.",
    "ru": "Здравствуйте! Отчёт платформы Нонбор. Сейчас {biz_count} бизнесов и {product_count} товаров на проверке.",
    "en": "Hello! Nonbor daily report. Currently {biz_count} businesses and {product_count} products are in checking status.",
    "zh": "您好！Nonbor每日报告。目前有{biz_count}个商家和{product_count}个产品正在审核中。",
}

# Admin: ertalabki hisobot — tunda yangi bizneslar bor
ADMIN_MORNING_REPORT_MESSAGES = {
    "uz": "Assalomu alaykum! Nonbor ertalabki hisobot. Kechasi {night_count} ta yangi biznes qo'shildi. Hozirda jami {biz_count} ta biznes platformada.",
    "ru": "Здравствуйте! Утренний отчёт Нонбор. За ночь добавлено {night_count} новых бизнесов. Всего на платформе {biz_count} бизнесов.",
    "en": "Hello! Nonbor morning report. {night_count} new businesses were added overnight. Currently {biz_count} businesses on the platform.",
    "zh": "您好！Nonbor早报。夜间新增{night_count}个商家。平台目前共有{biz_count}个商家。",
}

# Oldindan yaratiladigan maksimal buyurtma soni
MAX_PREGENERATE = 30


def _admin_new_business_text(count: int, lang: str) -> str:
    lang = (lang or DEFAULT_LANG).lower()
    template = ADMIN_NEW_BUSINESS_MESSAGES.get(lang) or ADMIN_NEW_BUSINESS_MESSAGES[DEFAULT_LANG]
    return template.format(count=count)


def _admin_daily_report_text(biz_count: int, product_count: int, lang: str) -> str:
    lang = (lang or DEFAULT_LANG).lower()

    # Ikkalasi 0 — test qo'ng'iroq uchun
    if biz_count == 0 and product_count == 0:
        test_messages = {
            "uz": "Assalomu alaykum! Nonbor tizimi ishlayapti. Hozirda tekshiruvda biznes va mahsulot yo'q.",
            "ru": "Здравствуйте! Система Нонбор работает. Сейчас на проверке нет бизнесов и товаров.",
            "en": "Hello! Nonbor system is working. Currently no businesses or products are in checking status.",
            "zh": "您好！Nonbor系统正常运行。目前没有待审核的商家和产品。",
        }
        return test_messages.get(lang) or test_messages[DEFAULT_LANG]

    # Faqat biznes bor
    if product_count == 0:
        biz_only = {
            "uz": "Assalomu alaykum! Nonbor hisoboti. Hozirda {biz_count} ta biznes tekshiruv holatida.",
            "ru": "Здравствуйте! Отчёт платформы Нонбор. Сейчас {biz_count} бизнесов на проверке.",
            "en": "Hello! Nonbor report. Currently {biz_count} businesses are in checking status.",
            "zh": "您好！Nonbor报告。目前有{biz_count}个商家正在审核中。",
        }
        return (biz_only.get(lang) or biz_only[DEFAULT_LANG]).format(biz_count=biz_count)

    # Faqat mahsulot bor
    if biz_count == 0:
        prod_only = {
            "uz": "Assalomu alaykum! Nonbor hisoboti. Hozirda {product_count} ta mahsulot tekshiruv holatida.",
            "ru": "Здравствуйте! Отчёт платформы Нонбор. Сейчас {product_count} товаров на проверке.",
            "en": "Hello! Nonbor report. Currently {product_count} products are in checking status.",
            "zh": "您好！Nonbor报告。目前有{product_count}个产品正在审核中。",
        }
        return (prod_only.get(lang) or prod_only[DEFAULT_LANG]).format(product_count=product_count)

    # Ikkalasi ham bor
    template = ADMIN_DAILY_REPORT_MESSAGES.get(lang) or ADMIN_DAILY_REPORT_MESSAGES[DEFAULT_LANG]
    return template.format(biz_count=biz_count, product_count=product_count)


def _order_message_text(count: int, lang: str) -> str:
    """Yangi buyurtma xabari matni"""
    lang = (lang or DEFAULT_LANG).lower()
    template = ORDER_MESSAGES.get(lang) or ORDER_MESSAGES[DEFAULT_LANG]
    return template.format(count=count)


def _planned_message_text(lang: str) -> str:
    """Reja eslatma xabari matni"""
    lang = (lang or DEFAULT_LANG).lower()
    return PLANNED_MESSAGES.get(lang) or PLANNED_MESSAGES[DEFAULT_LANG]


class BaseTTSProvider(ABC):
    """TTS provider uchun asosiy klass"""

    @abstractmethod
    async def synthesize(self, text: str, output_path: Path) -> bool:
        """Matnni ovozga aylantirish"""
        pass


class GoogleTTSProvider(BaseTTSProvider):
    """Google Text-to-Speech (fallback)"""

    def __init__(self, language: str = "uz"):
        self.language = language

    async def synthesize(self, text: str, output_path: Path) -> bool:
        """Google TTS orqali ovoz yaratish"""
        try:
            from gtts import gTTS

            tts = gTTS(text=text, lang=self.language, slow=False)

            mp3_path = output_path.with_suffix(".mp3")
            tts.save(str(mp3_path))

            if not mp3_path.exists() or mp3_path.stat().st_size == 0:
                logger.error(f"Google TTS MP3 yaratilmadi: {mp3_path}")
                return False

            success = await self._convert_to_wav(mp3_path, output_path)
            mp3_path.unlink(missing_ok=True)

            if not success or not output_path.exists() or output_path.stat().st_size == 0:
                logger.error(f"Google TTS WAV yaratilmadi: {output_path}")
                return False

            logger.info(f"Google TTS yaratildi: {output_path} ({output_path.stat().st_size} bayt)")
            return True

        except Exception as e:
            logger.error(f"Google TTS xatosi: {e}", exc_info=True)
            return False

    async def _convert_to_wav(self, mp3_path: Path, wav_path: Path) -> bool:
        """MP3 ni WAV ga convert qilish (8kHz, mono)"""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(mp3_path),
            "-ar", "8000",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            str(wav_path)
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"ffmpeg xatosi (code={process.returncode}): {stderr.decode()[:500]}")
            return False
        return True


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge TTS - Bepul va sifatli"""

    def __init__(self, voice: str = "uz-UZ-MadinaNeural"):
        self.voice = voice

    async def synthesize(self, text: str, output_path: Path) -> bool:
        """Edge TTS orqali ovoz yaratish"""
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, self.voice)

            mp3_path = output_path.with_suffix(".mp3")
            await communicate.save(str(mp3_path))

            if not mp3_path.exists() or mp3_path.stat().st_size == 0:
                logger.error(f"Edge TTS MP3 yaratilmadi yoki bo'sh: {mp3_path}")
                return False

            success = await self._convert_to_wav(mp3_path, output_path)
            mp3_path.unlink(missing_ok=True)

            if not success or not output_path.exists() or output_path.stat().st_size == 0:
                logger.error(f"Edge TTS WAV yaratilmadi: {output_path}")
                return False

            logger.info(f"Edge TTS yaratildi: {output_path} ({output_path.stat().st_size} bayt)")
            return True

        except Exception as e:
            logger.error(f"Edge TTS xatosi: {e}", exc_info=True)
            return False

    async def _convert_to_wav(self, mp3_path: Path, wav_path: Path) -> bool:
        """MP3 ni WAV ga convert qilish (8kHz, mono, Asterisk uchun)"""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(mp3_path),
            "-ar", "8000",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            str(wav_path)
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"ffmpeg xatosi (code={process.returncode}): {stderr.decode()[:500]}")
            return False
        return True


class TTSService:
    """
    TTS Servisi - Buyurtma xabarlarini ovozga aylantirish

    3 til: uz, ru, en
    Startup da 93 ta audio fayl oldindan yaratiladi:
      - 3 til x 30 buyurtma = 90 ta
      - 3 til x 1 reja = 3 ta
    """

    def __init__(self, audio_dir: Path, provider: str = "edge"):
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.audio_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.provider_type = provider
        self._providers: dict = {}

        logger.info(f"TTS servisi ishga tushdi: provider={provider}, tillar={PRIMARY_LANGS}")

    def _get_provider(self, lang: str) -> BaseTTSProvider:
        """Tilga mos provider olish"""
        lang = lang.lower() if lang else DEFAULT_LANG
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        if lang not in self._providers:
            if self.provider_type == "google":
                self._providers[lang] = GoogleTTSProvider(language=lang)
            else:
                self._providers[lang] = EdgeTTSProvider(voice=LANG_VOICES[lang])
            logger.info(f"TTS provider yaratildi: lang={lang}, voice={LANG_VOICES[lang]}")
        return self._providers[lang]

    def _get_cache_path(self, text: str, lang: str = DEFAULT_LANG) -> Path:
        """Matn va til uchun cache fayl yo'lini olish"""
        key = f"{lang}_{text}"
        text_hash = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{text_hash}.wav"

    async def _synthesize_with_cache(self, text: str, lang: str) -> Optional[Path]:
        """Matnni cache bilan synthesize qilish"""
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        cache_path = self._get_cache_path(text, lang)
        # Bo'sh yoki buzilgan cache faylni qayta yaratish
        if cache_path.exists() and cache_path.stat().st_size == 0:
            logger.warning(f"Bo'sh cache fayl, qayta yaratilmoqda: {cache_path}")
            cache_path.unlink()
        if not cache_path.exists():
            logger.info(f"TTS synthesize: lang={lang}, text={text[:60]}...")
            if not await self._get_provider(lang).synthesize(text, cache_path):
                logger.error(f"TTS synthesize muvaffaqiyatsiz: lang={lang}")
                return None
            logger.info(f"TTS cache saqlandi: {cache_path} ({cache_path.stat().st_size} bayt)")
        return cache_path

    async def generate_order_message(self, count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """
        Yangi buyurtma xabarini tilga qarab olish/yaratish

        Args:
            count: Buyurtmalar soni (1-30)
            lang: Til kodi ('uz', 'ru', 'en')
        """
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            logger.warning(f"TTS: til qo'llab-quvvatlanmaydi: {lang!r}, {DEFAULT_LANG} ishlatiladi")
            lang = DEFAULT_LANG
        text = _order_message_text(count, lang)
        logger.info(f"TTS order: lang={lang}, count={count}")
        return await self._synthesize_with_cache(text, lang)

    async def generate_planned_message(self, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Reja eslatma xabarini tilga qarab olish/yaratish"""
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        return await self._synthesize_with_cache(_planned_message_text(lang), lang)

    async def generate_admin_new_business(self, count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Admin: yangi biznes ochildi, N ta restoran tekshiruvda"""
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        text = _admin_new_business_text(count, lang)
        return await self._synthesize_with_cache(text, lang)

    async def generate_admin_morning_report(self, biz_count: int, night_count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Admin: ertalabki hisobot — tunda yangi bizneslar soni"""
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        template = ADMIN_MORNING_REPORT_MESSAGES.get(lang) or ADMIN_MORNING_REPORT_MESSAGES[DEFAULT_LANG]
        text = template.format(biz_count=biz_count, night_count=night_count)
        return await self._synthesize_with_cache(text, lang)

    async def generate_admin_daily_report(self, biz_count: int, product_count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Admin: kunlik hisobot - N biznes, M mahsulot tekshiruvda"""
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        text = _admin_daily_report_text(biz_count, product_count, lang)
        return await self._synthesize_with_cache(text, lang)

    def get_audio_path(self, count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Mavjud audio faylni olish (agar cache da bo'lsa)"""
        lang = (lang or DEFAULT_LANG).lower()
        if lang not in LANG_VOICES:
            lang = DEFAULT_LANG
        text = _order_message_text(count, lang)
        cache_path = self._get_cache_path(text, lang)
        return cache_path if cache_path.exists() else None

    async def pregenerate_messages(self):
        """
        Startup da barcha audio fayllarni oldindan yaratish:
        - 3 til (uz, ru, en) x 30 buyurtma = 90 ta order audio
        - 3 til x 1 reja = 3 ta planned audio
        Jami: 93 ta WAV fayl
        """
        total = len(PRIMARY_LANGS) * (MAX_PREGENERATE + 1)  # +1 planned har til uchun
        created = 0
        skipped = 0

        logger.info(f"TTS oldindan yaratish boshlandi: {len(PRIMARY_LANGS)} til, 1-{MAX_PREGENERATE} buyurtma + reja")

        for lang in PRIMARY_LANGS:
            # Reja audio (1 ta har til uchun)
            result = await self.generate_planned_message(lang=lang)
            if result:
                created += 1
            else:
                logger.error(f"Reja audio yaratilmadi: lang={lang}")

            # Buyurtma audiolari (1-30)
            for i in range(1, MAX_PREGENERATE + 1):
                result = await self.generate_order_message(i, lang=lang)
                if result:
                    created += 1
                else:
                    logger.error(f"Order audio yaratilmadi: lang={lang}, count={i}")

        # Natija hisoboti
        import glob
        wav_files = glob.glob(str(self.cache_dir / "*.wav"))
        total_size = sum(os.path.getsize(f) for f in wav_files)
        logger.info(
            f"TTS oldindan yaratish tugadi: "
            f"{created}/{total} ta audio yaratildi, "
            f"cache da {len(wav_files)} ta WAV fayl, "
            f"jami hajm: {total_size / 1024 / 1024:.1f} MB"
        )

    async def sync_to_wsl(self):
        """WSL development uchun audio sync (production da ishlatilmaydi)"""
        import subprocess
        import shutil

        cache_dir = self.audio_dir / "cache"
        if not cache_dir.exists():
            logger.warning("Cache katalogi topilmadi")
            return

        default_platform = "wsl" if os.name == "nt" else "linux"
        platform = os.getenv("PLATFORM", default_platform).lower()
        default_sounds = "/tmp/autodialer" if os.name == "nt" else "/var/lib/asterisk/sounds/autodialer"
        sounds_path = os.getenv("ASTERISK_SOUNDS_PATH", default_sounds)

        try:
            import glob as gl
            wav_files = gl.glob(str(cache_dir / "*.wav"))

            if not wav_files:
                logger.warning(f"Hech qanday .wav fayl topilmadi: {cache_dir}")
                return

            if platform == "linux":
                os.makedirs(sounds_path, exist_ok=True)
                for wav_file in wav_files:
                    dest = os.path.join(sounds_path, os.path.basename(wav_file))
                    shutil.copy2(wav_file, dest)
                logger.info(f"Audio fayllar ko'chirildi: {len(wav_files)} ta fayl -> {sounds_path}")

            else:
                subprocess.run(
                    ["wsl", "mkdir", "-p", sounds_path],
                    capture_output=True, timeout=10
                )
                for wav_file in wav_files:
                    wav_file_wsl = str(wav_file).replace("\\", "/")
                    if len(wav_file_wsl) > 1 and wav_file_wsl[1] == ":":
                        wav_file_wsl = f"/mnt/{wav_file_wsl[0].lower()}{wav_file_wsl[2:]}"
                    result = subprocess.run(
                        ["wsl", "cp", wav_file_wsl, f"{sounds_path}/"],
                        capture_output=True, timeout=10
                    )
                    if result.returncode != 0:
                        logger.warning(f"Fayl ko'chirishda xato {wav_file}: {result.stderr.decode()}")
                logger.info(f"Audio fayllar WSL ga ko'chirildi: {len(wav_files)} ta fayl")

        except subprocess.TimeoutExpired:
            logger.error("WSL buyrug'i timeout")
        except Exception as e:
            logger.error(f"Audio sync xatosi: {e}")
