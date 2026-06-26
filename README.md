# Autodialer Pro

Nonbor oshxona buyurtma tizimi uchun avtomatik qo'ng'iroq va Telegram xabar yuborish servisi.

## Arxitektura

```
                    +-------------------+
                    |   Nonbor API      |
                    | (buyurtmalar)     |
                    +--------+----------+
                             |
                    +--------v----------+
                    |  Autodialer Pro   |
                    |  (Python 3.11)    |
                    |                   |
                    | - Polling         |
                    | - TTS (Edge)      |
                    | - Call logic      |
                    | - REST API :8585  |
                    +---+----------+----+
                        |          |
              +---------v--+  +----v-----------+
              |  Asterisk  |  |  Telegram Bot  |
              |  (PJSIP)   |  |  (guruh xabar) |
              +-----+------+  +----------------+
                    |
              +-----v------+
              | Sarkor SIP |
              | (trunk)    |
              +-----------+
```

## Texnologiyalar

| Komponent | Texnologiya |
|-----------|-------------|
| Dasturlash tili | Python 3.11+ (async/await) |
| API framework | aiohttp |
| TTS (ovoz) | Microsoft Edge TTS (ko'p tilli) |
| IP telefoniya | Asterisk + PJSIP |
| SIP provayder | Sarkor Telecom |
| Xabar almashish | Telegram Bot API |
| Konteyner | Docker + docker-compose |
| Buyurtma API | Nonbor API v2 |

## Loyiha tuzilishi

```
ip-telefon/
├── .env.example              # Sozlamalar namunasi
├── .env.production           # Production sozlamalar (gitda YO'Q)
├── docker-compose.yml        # Docker orchestration
├── Dockerfile                # Autodialer image
├── requirements.txt          # Python dependencies
├── docker-entrypoint.sh      # Container entrypoint
│
├── src/
│   ├── autodialer.py         # Asosiy engine (polling, call logic, state)
│   ├── api_server.py         # REST API server (:8585)
│   ├── models/
│   │   └── order.py          # Buyurtma modeli
│   └── services/
│       ├── asterisk_service.py   # Asterisk AMI client
│       ├── nonbor_service.py     # Nonbor API client
│       ├── telegram_service.py   # Telegram bot + guruh xabarlari
│       ├── tts_service.py        # Edge TTS ovoz yaratish
│       └── stats_service.py      # Statistika to'plash
│
├── config/
│   └── asterisk/
│       ├── pjsip.conf        # SIP trunk konfiguratsiya
│       ├── extensions.conf   # Dialplan (call flow)
│       └── manager.conf      # AMI sozlamalari
│
├── audio/
│   └── cache/                # TTS audio kesh (*.wav)
├── data/                     # Persistent data (JSON)
└── logs/                     # Log fayllar
```

## Ishlash jarayoni

### Yangi buyurtma

```
1. Nonbor API dan yangi buyurtma keldi (state=CHECKING)
2. Biznes Telegram guruhiga xabar yuborildi
3. 90 soniya kutildi (sotuvchi ilovada qabul qilishi uchun)
4. Hali CHECKING bo'lsa -> TTS audio yaratildi -> Asterisk orqali qo'ng'iroq
5. Javob berdi -> buyurtma kommunikatsiya qilindi deb belgilandi
6. Javob bermadi -> unanswered_sellers ga qo'shildi -> keyingi siklda qayta qo'ng'iroq
7. 180 soniyadan keyin hali CHECKING -> Telegram support guruhga ogohlantirish
```

### Reja buyurtma

```
1. Reja buyurtma qabul qilindi (is_planned=true)
2. planned_group_alert_time daqiqa oldin -> Telegram guruhga eslatma
3. planned_reminder_time daqiqa oldin -> Asterisk orqali qo'ng'iroq
```

### Qo'ng'iroq jarayoni

```
Autodialer                  Asterisk                    Telefon
    |                          |                          |
    |-- AMI Originate -------->|                          |
    |   (PJSIP/phone@sarkor)  |-- SIP INVITE ----------->|
    |                          |                          |
    |                          |<-- 200 OK (javob) -------|
    |                          |-- Answer() ------------->|
    |                          |-- Wait(1) -------------->|
    |                          |-- Playback(audio) ------>|  "Sizda 3 ta buyurtma..."
    |                          |-- Wait(2) -------------->|
    |                          |-- Hangup() ------------->|
    |                          |                          |
    |<-- OriginateResponse ----|                          |
    |   (Success/Failure)      |                          |
```

## O'rnatish

### 1. Repo klonlash

```bash
git clone https://github.com/Ikhtiyor-s/ip-telefon.git
cd ip-telefon
```

### 2. Sozlamalarni tayorlash

```bash
cp .env.example .env.production
nano .env.production  # Haqiqiy qiymatlarni yozing
```

**Muhim:** `.env.production` da inline comment (`KEY=value # comment`) ishlatmang. Docker `--env-file` qo'llab-quvvatlamaydi.

### 3. Docker bilan ishga tushirish

```bash
# Barcha konteynerlarni build va ishga tushirish
docker-compose up -d --build

# Loglarni kuzatish
docker logs -f autodialer-pro
```

### 4. Tekshirish

```bash
# Konteynerlar ishlayaptimi
docker ps

# Health check
curl http://localhost:8585/api/autodialer/health

# Statistika
curl -H "X-API-Key: YOUR_KEY" http://localhost:8585/api/autodialer/stats
```

## Sozlamalar (.env.production)

| Parametr | Tavsif | Default |
|----------|--------|---------|
| `NONBOR_BASE_URL` | Nonbor API manzili | - |
| `NONBOR_SECRET` | Nonbor API kaliti | - |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | - |
| `TELEGRAM_CHAT_ID` | Support guruh ID | - |
| `AMI_HOST` | Asterisk AMI host | `127.0.0.1` |
| `AMI_PORT` | Asterisk AMI port | `5038` |
| `AMI_USERNAME` | AMI foydalanuvchi | `autodialer` |
| `AMI_PASSWORD` | AMI parol | - |
| `API_SECRET_KEY` | REST API kaliti | - |
| `WAIT_BEFORE_CALL` | Qo'ng'iroqdan oldin kutish (sek) | `90` |
| `TELEGRAM_ALERT_TIME` | Telegram ogohlantirish vaqti (sek) | `180` |
| `MAX_CALL_ATTEMPTS` | Maksimum qo'ng'iroq urinishi | `2` |
| `RETRY_INTERVAL` | Qayta urinish oralig'i (sek) | `30` |
| `PLANNED_REMINDER_TIME` | Reja qo'ng'iroq vaqti (daq) | `60` |
| `PLANNED_GROUP_ALERT_TIME` | Reja guruh xabari vaqti (daq) | `60` |

## Docker arxitekturasi

```
docker-compose.yml
├── autodialer-asterisk     (andrius/asterisk:20.5.0)
│   ├── network: host
│   ├── volumes:
│   │   ├── config/asterisk/*.conf -> /etc/asterisk/ (ro)
│   │   └── autodialer-audio -> /var/lib/asterisk/sounds/autodialer
│   └── healthcheck: asterisk -rx "core show channels"
│
├── autodialer-pro          (autodialer-pro:latest)
│   ├── network: host
│   ├── depends_on: asterisk (healthy)
│   ├── volumes:
│   │   ├── autodialer-audio -> /app/audio
│   │   ├── ./logs -> /app/logs
│   │   └── ./data -> /app/data
│   ├── env_file: .env.production
│   └── healthcheck: http://localhost:8585/api/autodialer/health
│
└── volumes:
    └── autodialer-audio    (named, shared between containers)
```

**Audio volume:** TTS `audio/cache/*.wav` fayllarni yaratadi. Asterisk xuddi shu fayllarni `Playback()` orqali o'ynatadi.

## API Endpoints

Barcha endpointlar `X-API-Key` header talab qiladi (`/health` dan tashqari).

| Method | Endpoint | Tavsif |
|--------|----------|--------|
| GET | `/api/autodialer/health` | Health check (ochiq) |
| GET | `/api/autodialer/stats` | Umumiy statistika |
| GET | `/api/autodialer/calls?page=1` | Qo'ng'iroqlar ro'yxati |
| GET | `/api/autodialer/orders?page=1` | Buyurtmalar ro'yxati |
| GET | `/api/autodialer/daily-trend?days=7` | Kunlik trend |
| GET | `/api/autodialer/live-orders` | Hozirgi buyurtmalar |
| GET | `/api/autodialer/businesses` | Bizneslar ro'yxati |
| GET | `/api/autodialer/config` | Joriy sozlamalar |
| POST | `/api/autodialer/config` | Sozlamalarni yangilash |
| POST | `/api/autodialer/call-business` | Qo'lda qo'ng'iroq |
| GET | `/api/autodialer/logs?lines=100` | Oxirgi loglar |

## TTS (Text-to-Speech)

Qo'llab-quvvatlanadigan tillar:

| Til | Ovoz | Kod |
|-----|------|-----|
| O'zbekcha | uz-UZ-MadinaNeural | `uz` |
| Ruscha | ru-RU-SvetlanaNeural | `ru` |
| Inglizcha | en-US-JennyNeural | `en` |
| Xitoycha | zh-CN-XiaoxiaoNeural | `zh` |
| Qozoqcha | kk-KZ-AigulNeural | `kk` |

Yangi til qo'shish: `src/services/tts_service.py` da 3 ta dict ga qator qo'shing (`LANG_VOICES`, `ORDER_MESSAGES`, `PLANNED_MESSAGES`).

Audio kesh: matn + til MD5 hash bilan keshlanadi. Matn o'zgarsa eski keshni tozalang:
```bash
docker exec autodialer-pro rm -rf /app/audio/cache/*
docker restart autodialer-pro
```

## Deploy (yangilash)

```bash
cd /opt/autodialer-pro

# 1. Yangi kod
git pull origin master

# 2. Rebuild va restart
docker stop autodialer-pro && docker rm autodialer-pro
docker build -t autodialer-pro .
docker run -d \
  --name autodialer-pro \
  --network host \
  --restart unless-stopped \
  --env-file .env.production \
  -v autodialer-audio:/app/audio \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/data:/app/data \
  autodialer-pro

# 3. Log tekshirish
docker logs -f autodialer-pro
```

**Agar TTS matnlari o'zgargan bo'lsa** (yangi audio kerak):
```bash
docker exec autodialer-pro rm -rf /app/audio/cache/*
docker restart autodialer-pro
```

## Xatoliklarni tuzatish

| Muammo | Sabab | Yechim |
|--------|-------|--------|
| `planned_telegram_time` TypeError | Eski parametr nomi kodda qolgan | `git pull` va rebuild |
| `invalid literal for int()` | `.env` da inline comment (`60 # ...`) | Inline commentlarni olib tashlang |
| Audio gapirmayapti | TTS kesh eskirgan (matn o'zgargan) | `rm -rf audio/cache/*` va restart |
| AMI connection refused | Asterisk container ishlamayapti | `docker restart autodialer-asterisk` |
| SIP registration failed | Sarkor credentials noto'g'ri | `config/asterisk/pjsip.conf` tekshiring |
| Telegram xabar yuborilmayapti | Bot token noto'g'ri yoki guruhga qo'shilmagan | Token va chat_id tekshiring |
| Container crash loop | `.env.production` xatosi yoki kod bug | `docker logs autodialer-pro` tekshiring |

## Xavfsizlik

- `.env.production` gitda kuzatilmaydi (`.gitignore` da)
- API endpointlar `X-API-Key` bilan himoyalangan
- Docker container non-root user (`appuser`) bilan ishlaydi
- Input validation: barcha query parametrlar chegaralangan
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`
- Request size limit: 1MB

**Muhim:** Sirlarni (token, parol) hech qachon kodga yozmang. Faqat `.env.production` da saqlang.

## Muallif

WellTech Team | Versiya 2.0.0
