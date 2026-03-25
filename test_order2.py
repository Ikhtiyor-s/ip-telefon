import asyncio, os, aiohttp
async def test():
    url = os.getenv('NONBOR_BASE_URL', 'https://prod.nonbor.uz/api/v2')
    secret = os.getenv('NONBOR_SECRET', 'nonbor-secret-key')
    async with aiohttp.ClientSession(headers={'X-Telegram-Bot-Secret': secret}) as s:
        async with s.get(url + '/telegram_bot/get-order-for-courier/') as r:
            data = await r.json()
            results = data.get('result', {}).get('results', [])
            print('Jami buyurtmalar:', len(results))
            for o in results[:5]:
                biz = o.get('business', {})
                print('---')
                print('Order ID:', o.get('id'), 'State:', o.get('state'))
                print('Business keys:', list(biz.keys()))
                print('Business:', biz)
asyncio.run(test())
