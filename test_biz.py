import asyncio, os, aiohttp
async def test():
    url = os.getenv('NONBOR_BASE_URL', 'https://prod.nonbor.uz/api/v2')
    secret = os.getenv('NONBOR_SECRET', 'nonbor-secret-key')
    async with aiohttp.ClientSession(headers={'X-Telegram-Bot-Secret': secret}) as s:
        async with s.get(url + '/telegram_bot/businesses/accepted/') as r:
            data = await r.json()
            for b in data.get('result', []):
                if b.get('id') == 24:
                    print('TOPILDI:', b.get('title'), b.get('phone_number'))
                    print(b)
asyncio.run(test())
