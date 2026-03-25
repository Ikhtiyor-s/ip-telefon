import asyncio, os, aiohttp
async def test():
    url = os.getenv('NONBOR_BASE_URL', 'https://prod.nonbor.uz/api/v2')
    secret = os.getenv('NONBOR_SECRET', 'nonbor-secret-key')
    async with aiohttp.ClientSession(headers={'X-Telegram-Bot-Secret': secret}) as s:
        async with s.get(url + '/telegram_bot/get-order-for-courier/') as r:
            data = await r.json()
            for o in data.get('result', {}).get('results', []):
                if o.get('id') == 35009:
                    biz = o.get('business', {})
                    print('ORDER business:', biz)
                    print('business id:', biz.get('id'))
                    print('business title:', biz.get('title'))
asyncio.run(test())
