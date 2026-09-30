"""
Тест API peek.tg с разными заголовками
"""
import asyncio
import aiohttp

async def test_peek():
    url = "https://server.peek.tg/api/nft/gifts/search"
    params = {
        "name": "EternalRose",
        "page": 1,
        "sortOrder": "asc",
        "giftNumber": 13000,
        "limit": 20
    }
    
    # Пробуем разные User-Agent
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]
    
    for i, ua in enumerate(user_agents, 1):
        print(f"\n{'='*60}")
        print(f"Тест #{i}: {ua[:50]}...")
        print('='*60)
        
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://peek.tg",
            "Referer": "https://peek.tg/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as response:
                    print(f"Статус: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        print(f"✅ РАБОТАЕТ! Найдено: {len(data.get('results', []))}")
                        return
                    else:
                        text = await response.text()
                        print(f"❌ Ошибка: {text[:200]}")
        except Exception as e:
            print(f"❌ Exception: {e}")
        
        await asyncio.sleep(2)
    
    print("\n" + "="*60)
    print("❌ Все варианты не работают!")
    print("Возможно IP заблокирован на Render.")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(test_peek())
