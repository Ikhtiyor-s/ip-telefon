"""
TTS fayllarni oldindan yaratish skripti
uz, ru, en tillari uchun:
  - 1-30 ta buyurtma audio
  - 1 ta reja audio har til uchun
Jami: 93 ta WAV fayl
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from services.tts_service import TTSService, PRIMARY_LANGS, MAX_PREGENERATE

AUDIO_DIR = Path(__file__).parent / "audio"


async def main():
    tts = TTSService(audio_dir=AUDIO_DIR, provider="edge")

    total_expected = len(PRIMARY_LANGS) * (MAX_PREGENERATE + 1)
    print(f"TTS generatsiya boshlanmoqda...")
    print(f"Tillar: {PRIMARY_LANGS}")
    print(f"Buyurtma soni: 1-{MAX_PREGENERATE}")
    print(f"Jami kutilmoqda: {total_expected} ta fayl\n")

    total = 0
    for lang in PRIMARY_LANGS:
        # Reja audio (1 ta har til uchun)
        path = await tts.generate_planned_message(lang=lang)
        if path:
            total += 1
            print(f"  OK planned [{lang}] -> {path.name}")
        else:
            print(f"  !! planned [{lang}] - XATO!")

        # Buyurtma audiolari (1-30)
        for i in range(1, MAX_PREGENERATE + 1):
            path = await tts.generate_order_message(i, lang=lang)
            if path:
                total += 1
                print(f"  OK order   [{lang}] {i:>2} ta -> {path.name}")
            else:
                print(f"  !! order   [{lang}] {i:>2} ta - XATO!")

        print(f"\n[{lang}] tugadi\n")

    print(f"\nJami yaratildi: {total}/{total_expected} ta fayl")
    print(f"Joylashuv: {AUDIO_DIR / 'cache'}")


if __name__ == "__main__":
    asyncio.run(main())
