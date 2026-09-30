import asyncio
import aiohttp
import json

async def test_peek_api():
    """Тест различных вариантов запросов к Peek API"""
    
    url = "https://server.peek.tg/api/nft/gifts/search"
    
    # Параметры запроса
    params = {
        "name": "EternalRose",
        "page": 1,
        "sortOrder": "asc",
        "giftNumber": 13000,
        "limit": 20
    }
    
    # Варианты заголовков для тестирования
    headers_variants = [
        # Вариант 1: Минимальные заголовки
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        # Вариант 2: Как с браузера
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://peek.tg",
            "Referer": "https://peek.tg/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site"
        },
        # Вариант 3: Простой
        {
            "User-Agent": "TelegramGiftParser/1.0",
            "Accept": "application/json"
        }
    ]
    
    for i, headers in enumerate(headers_variants, 1):
        print(f"\n{'='*60}")
        print(f"Вариант {i}: Тестируем заголовки")
        print(f"{'='*60}")
        for key, value in headers.items():
            print(f"{key}: {value[:50]}...")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    print(f"\n✅ Статус: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        print(f"✅ Успех! Получен JSON")
                        print(f"Ключи ответа: {list(data.keys())}")
                        
                        if 'results' in data:
                            print(f"Найдено результатов: {len(data.get('results', []))}")
                            if data['results']:
                                print("\nПример первого результата:")
                                print(json.dumps(data['results'][0], indent=2, ensure_ascii=False))
                        
                        return data  # Возвращаем первый успешный результат
                    else:
                        text = await response.text()
                        print(f"❌ Ошибка: {text[:200]}")
                        
        except Exception as e:
            print(f"❌ Исключение: {type(e).__name__}: {e}")
    
    print("\n❌ Все варианты не сработали!")
    return None

if __name__ == "__main__":
    result = asyncio.run(test_peek_api())
    
    if result:
        print("\n" + "="*60)
        print("🎉 API РАБОТАЕТ! Можно использовать эти заголовки.")
        print("="*60)
