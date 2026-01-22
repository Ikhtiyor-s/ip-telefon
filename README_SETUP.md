# AmoCRM IP Telefoniya - Qayta Tiklash Qo'llanmasi

## Tizim Arxitekturasi

```
AmoCRM (welltech.amocrm.ru)
    ↓ (Polling har 30 sek)
Autodialer (Python - port 8080)
    ↓
Asterisk PBX (PJSIP - WSL Ubuntu)
    ↓
Zadarma VoIP (pbx.zadarma.com:5061 TLS)
    ↓
Sotuvchi Telefoni
```

## Yangilangan Fayllar

| Fayl | Joylashuv | Maqsad |
|------|-----------|--------|
| `pjsip.conf` | `C:\Users\Asus\` | Zadarma PJSIP konfiguratsiyasi |
| `extensions_full.conf` | `C:\Users\Asus\` | Asterisk dialplan (IVR bilan) |
| `autodialer_v2.py` | `C:\Users\Asus\autodialer\` | Yangilangan autodialer (IVR callback) |
| `setup_asterisk_wsl.sh` | `C:\Users\Asus\autodialer\` | WSL Asterisk setup skripti |

## Qadam-Qadamlik O'rnatish

### 1. WSL Ubuntu da Asterisk O'rnatish

```bash
# WSL ni oching
wsl

# Setup skriptni ishga tushiring
cd /mnt/c/Users/Asus/autodialer
chmod +x setup_asterisk_wsl.sh
sudo ./setup_asterisk_wsl.sh
```

### 2. Asterisk Holatini Tekshirish

```bash
# WSL ichida
sudo asterisk -rvvv

# Asterisk CLI dan:
pjsip show registrations
pjsip show endpoints
```

Kutilgan natija:
```
Registration:  zadarma/sip:545235-103@pbx.zadarma.com   Registered
```

### 3. Autodialer ni Ishga Tushirish

```bash
# WSL ichida
cd /mnt/c/Users/Asus/autodialer

# Eski versiya emas, yangi versiya!
python3 autodialer_v2.py
```

### 4. Test Qilish

#### 4.1 Autodialer API Test
```bash
# Autodialer ishlayaptimi?
curl http://localhost:8080/test

# Telegram test
curl http://localhost:8080/test-telegram

# amoCRM leads tekshirish
curl http://localhost:8080/check-leads
```

#### 4.2 Test Qo'ng'iroq (IVR bilan)
```bash
# POST request bilan
curl -X POST http://localhost:8080/test-call \
  -H "Content-Type: application/json" \
  -d '{"phone": "+998901234567", "order_id": 12345}'
```

#### 4.3 Asterisk orqali to'g'ridan-to'g'ri
```bash
# WSL Asterisk CLI dan:
sudo asterisk -rx 'channel originate PJSIP/zadarma-endpoint/sip:+998901234567@pbx.zadarma.com extension s@order-notification,1" "ORDER_ID=12345,SELLER_PHONE=+998901234567"'
```

## IVR Ishlash Mantigi

1. **Qo'ng'iroq boshlanadi** - Asterisk PJSIP orqali Zadarma'ga ulanadi
2. **Sotuvchi javob beradi** - `order-notification` konteksti ishlaydi
3. **Buyurtma raqami aytiladi** - `SayDigits(${ORDER_ID})`
4. **DTMF kutiladi**:
   - `1` - Qabul qilish (accepted)
   - `2` - Rad etish (rejected)
   - Timeout - Javob yo'q
5. **Callback yuboriladi** - `curl POST http://localhost:8080/call-result`
6. **amoCRM yangilanadi** - Status o'zgartiriladi

## Sozlamalar

### AmoCRM Status ID lari
```python
AMOCRM_STATUS_TEKSHIRILMOQDA = 80442678  # Tekshirilmoqda
AMOCRM_STATUS_QABUL_QILINDI = 80442682   # Qabul qilindi
AMOCRM_STATUS_RAD_ETILDI = 80442686      # Rad etildi
```

**Muhim:** Status ID larni amoCRM admin paneldan tekshiring va to'g'ri qiymatlarga o'zgartiring!

### Zadarma Credentials
```
Username: 545235-103
Password: 73yfvDzyX7
Server: pbx.zadarma.com:5061 (TLS)
```

### Telegram Bot
```
Token: 7683981246:AAFCH2u26L1ohHEOddFY8I26o4k_uXptb08
Admin Chat ID: 1154426667
```

## Xatolarni Tuzatish

### 1. PJSIP Registration Failed
```bash
# Asterisk log
sudo asterisk -rx 'pjsip show registrations'

# Agar "Unregistered" ko'rsatsa:
sudo asterisk -rx 'pjsip reload'
```

### 2. Qo'ng'iroq Ishlamayapti
```bash
# Asterisk SIP debug
sudo asterisk -rx 'pjsip set logger on'

# Log ko'rish
sudo tail -f /var/log/asterisk/messages
```

### 3. IVR Callback Kelmayapti
```bash
# extensions.conf dagi curl buyrug'ini tekshiring
# WSL ichidan Windows localhost ga ulanish uchun:
# http://localhost:8080 yoki http://host.docker.internal:8080
```

### 4. amoCRM 401 Unauthorized
```bash
# Token muddati tugagan, yangi token olish kerak
# welltech.amocrm.ru -> Settings -> Integrations -> Refresh Token
```

## Foydalanuvchi Stsenariylari

### Stsenariy 1: Yangi Buyurtma
1. amoCRM'da yangi lead yaratiladi (Tekshirilmoqda status)
2. Autodialer 30 sek ichida uni ko'radi
3. 2 daqiqa kutadi
4. Sotuvchiga 2 marta qo'ng'iroq qiladi (IVR)
5. Sotuvchi 1 bosadi = Qabul qilindi
6. amoCRM status yangilanadi
7. Telegram xabar yuboriladi

### Stsenariy 2: Sotuvchi Javob Bermasa
1. 2 marta qo'ng'iroq - javob yo'q
2. 4 daqiqa o'tadi
3. Telegram alert yuboriladi
4. Admin ko'radi va qo'ng'iroq qiladi

## Systemd Service (Ixtiyoriy)

WSL da autodialer'ni avtomatik ishga tushirish:

```bash
sudo nano /etc/systemd/system/autodialer.service
```

```ini
[Unit]
Description=AmoCRM Autodialer v2
After=network.target asterisk.service

[Service]
Type=simple
User=root
WorkingDirectory=/mnt/c/Users/Asus/autodialer
ExecStart=/usr/bin/python3 /mnt/c/Users/Asus/autodialer/autodialer_v2.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable autodialer
sudo systemctl start autodialer
```

## Kontaktlar

Muammo bo'lsa:
- Telegram Admin: @admin (Chat ID: 1154426667)
- amoCRM: welltech.amocrm.ru
