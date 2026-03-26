"""
TTS (Text-to-Speech) Servisi
Matnni ovozga aylantirish - Ko'p til qo'llab-quvvatlanadi

Yangi til qo'shish uchun:
  1. LANG_VOICES ga ovoz nomi qo'shing
  2. ORDER_MESSAGES ga xabar qo'shing
  3. PLANNED_MESSAGES ga xabar qo'shing
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
# TILLAR KONFIGURATSIYASI
# Yangi til qo'shish: faqat quyidagi 3 ta diktga qator qo'shing
# ─────────────────────────────────────────────────────────────

# Edge TTS ovozlari (https://bit.ly/edge-tts-voices)
LANG_VOICES = {
    "uz": "uz-UZ-MadinaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "en": "en-US-JennyNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    # Qo'shimcha tillar:
    "kk": "kk-KZ-AigulNeural",
}
DEFAULT_LANG = "uz"

# Yangi buyurtma xabarlari: (1 ta buyurtma, ko'p buyurtma)
# {count} joy egasi - songa almashtiriladi
# Tabiiy operator uslubi — "bot" so'zi ishlatilmaydi
ORDER_MESSAGES = {
    "uz": "Assalomu alaykum! Bu Nonbor xizmati. Sizda {count} ta yangi buyurtma keldi, iltimos ilovani tekshiring.",
    "ru": "Здравствуйте! Звонит сервис Нонбор. У вас {count} новых заказа, пожалуйста проверьте приложение.",
    "en": "Hello! This is Nonbor calling. You have {count} new orders, please check your app.",
    "zh": "您好！Nonbor来电通知。您有{count}个新订单，请查看您的应用。",
    "kk": "Сәлеметсіз бе! Nonbor хабарлайды. Сізде {count} жаңа тапсырыс бар, қолданбаны тексеріңіз.",
}

# Reja (scheduled) eslatma xabarlari: faqat 1 ta xabar har bir til uchun
PLANNED_MESSAGES = {
    "uz": "Assalomu alaykum! Bu Nonbor xizmati. Sizda rejalashtirilgan buyurtma bor. Buyurtmangizni tayyorlang.",
    "ru": "Здравствуйте! Звонит сервис Нонбо́р. У вас запланированный заказ. Пожалуйста, подготовьте ваш заказ.",
    "en": "Hello! This is Nonbor calling. You have a scheduled order. Please prepare your order.",
    "zh": "您好！Nonbor来电提醒。您有一个计划订单，请准备好您的订单。",
}

# Asosiy tillar - oldindan generate qilinadi (startup da)
PRIMARY_LANGS = ["uz", "ru", "en", "zh"]


def _order_message_text(count: int, lang: str) -> str:
    """Yangi buyurtma xabari matni"""
    lang = (lang or DEFAULT_LANG).lower()
    template = ORDER_MESSAGES.get(lang) or ORDER_MESSAGES[DEFAULT_LANG]
    return template.format(count=count)


def _planned_message_text(lang: str) -> str:
    """Reja eslatma xabari matni"""
    lang = (lang or DEFAULT_LANG).lower()
    return PLANNED_MESSAGES.get(lang) or PLANNED_MESSAGES.get(DEFAULT_LANG)


class BaseTTSProvider(ABC):
    """TTS provider uchun asosiy klass"""

    @abstractmethod
    async def synthesize(self, text: str, output_path: Path) -> bool:
        """Matnni ovozga aylantirish"""
        pass


class GoogleTTSProvider(BaseTTSProvider):
    """Google Text-to-Speech"""

    def __init__(self, language: str = "uz"):
        self.language = language

    async def synthesize(self, text: str, output_path: Path) -> bool:
        """Google TTS orqali ovoz yaratish"""
        try:
            from gtts import gTTS

            tts = gTTS(text=text, lang=self.language, slow=False)

            # MP3 ga saqlash
            mp3_path = output_path.with_suffix(".mp3")
            tts.save(str(mp3_path))

            # WAV ga convert qilish (Asterisk uchun 8kHz)
            await self._convert_to_wav(mp3_path, output_path)

            # MP3 ni o'chirish
            mp3_path.unlink(missing_ok=True)

            logger.info(f"TTS yaratildi: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Google TTS xatosi: {e}")
            return False

    async def _convert_to_wav(self, mp3_path: Path, wav_path: Path):
        """MP3 ni WAV ga convert qilish (8kHz, mono)"""
        import asyncio

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
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await process.wait()


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge TTS - Bepul va sifatli"""

    def __init__(self, voice: str = "uz-UZ-MadinaNeural"):
        self.voice = voice

    async def synthesize(self, text: str, output_path: Path) -> bool:
        """Edge TTS orqali ovoz yaratish"""
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, self.voice)

            # MP3 ga saqlash
            mp3_path = output_path.with_suffix(".mp3")
            await communicate.save(str(mp3_path))

            # WAV ga convert qilish
            await self._convert_to_wav(mp3_path, output_path)

            # MP3 ni o'chirish
            mp3_path.unlink(missing_ok=True)

            logger.info(f"Edge TTS yaratildi: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Edge TTS xatosi: {e}")
            return False

    async def _convert_to_wav(self, mp3_path: Path, wav_path: Path):
        """MP3 ni WAV ga convert qilish"""
        import asyncio

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
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await process.wait()


class TTSService:
    """
    TTS Servisi - Buyurtma xabarlarini ovozga aylantirish

    Qo'llab-quvvatlanadigan tillar: uz, ru, en, zh (va boshqalar)
    Foydalanish:
        tts = TTSService(audio_dir="/path/to/audio")
        audio_path = await tts.generate_order_message(count=5, lang='ru')
        audio_path = await tts.generate_planned_message(count=3, lang='zh')
    """

    def __init__(self, audio_dir: Path, provider: str = "edge"):
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.audio_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.provider_type = provider

        # Tilga qarab provider cache: {lang: EdgeTTSProvider}
        self._providers: dict = {}

        logger.info(f"TTS servisi ishga tushdi: provider={provider}, tillar={list(LANG_VOICES.keys())}")

    def _get_provider(self, lang: str) -> BaseTTSProvider:
        """Tilga mos provider olish (cache da saqlash)"""
        lang = lang.lower() if lang else DEFAULT_LANG
        if lang not in self._providers:
            if self.provider_type == "google":
                # Google TTS faqat uz/ru ni to'g'ri qo'llab-quvvatlaydi
                self._providers[lang] = GoogleTTSProvider(language=lang if lang in ("uz", "ru") else "uz")
            else:
                voice = LANG_VOICES.get(lang, LANG_VOICES[DEFAULT_LANG])
                self._providers[lang] = EdgeTTSProvider(voice=voice)
            logger.info(f"TTS provider yaratildi: lang={lang}, voice={LANG_VOICES.get(lang, 'default')}")
        return self._providers[lang]

    def _get_cache_path(self, text: str, lang: str = DEFAULT_LANG) -> Path:
        """Matn va til uchun cache fayl yo'lini olish"""
        key = f"{lang}_{text}"
        text_hash = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{text_hash}.wav"

    async def _synthesize_with_cache(self, text: str, lang: str) -> Optional[Path]:
        """Matnni cache bilan synthesize qilish (ichki yordamchi)"""
        lang = (lang or DEFAULT_LANG).lower()
        cache_path = self._get_cache_path(text, lang)
        if not cache_path.exists():
            if not await self._get_provider(lang).synthesize(text, cache_path):
                return None
        return cache_path

    async def generate_order_message(self, count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """
        Yangi buyurtma xabarini tilga qarab yaratish

        Args:
            count: Buyurtmalar soni
            lang: Til kodi ('uz', 'ru', 'en', 'zh', ...)
        """
        lang = (lang or DEFAULT_LANG).lower()
        text = _order_message_text(count, lang)
        logger.info(f"TTS order: lang={lang}, count={count}")
        return await self._synthesize_with_cache(text, lang)

    async def generate_planned_message(self, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Reja eslatma xabarini tilga qarab yaratish"""
        lang = (lang or DEFAULT_LANG).lower()
        return await self._synthesize_with_cache(_planned_message_text(lang), lang)

    def get_audio_path(self, count: int, lang: str = DEFAULT_LANG) -> Optional[Path]:
        """Mavjud audio faylni olish (agar cache da bo'lsa)"""
        lang = (lang or DEFAULT_LANG).lower()
        text = _order_message_text(count, lang)
        cache_path = self._get_cache_path(text, lang)
        return cache_path if cache_path.exists() else None

    async def pregenerate_messages(self, max_count: int = 20):
        """Asosiy tillar uchun oldindan xabarlar yaratish (startup da chaqiriladi)"""
        logger.info(f"TTS oldindan yaratish: 1-{max_count} buyurtma, tillar={PRIMARY_LANGS}")
        for lang in PRIMARY_LANGS:
            await self.generate_planned_message(lang=lang)
            for i in range(1, max_count + 1):
                await self.generate_order_message(i, lang=lang)
        logger.info("TTS oldindan yaratish tugadi")

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
            import glob
            wav_files = glob.glob(str(cache_dir / "*.wav"))

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
