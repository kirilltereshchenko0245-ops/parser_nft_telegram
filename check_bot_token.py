"""
Скрипт для проверки статуса токена бота
"""
import asyncio
import os
from dotenv import load_dotenv
import aiohttp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def check_token():
    """Проверяет токен и показывает информацию о боте"""
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден в .env")
        return
    
    print(f"🔑 Токен: {BOT_TOKEN[:10]}...{BOT_TOKEN[-10:]}")
    print("\n🔍 Проверяю статус бота...\n")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['ok']:
                        bot = data['result']
                        print("✅ Токен валидный!")
                        print(f"🤖 Имя бота: @{bot['username']}")
                        print(f"📝 ID: {bot['id']}")
                        print(f"👤 Имя: {bot['first_name']}")
                        print(f"🔓 Can join groups: {bot.get('can_join_groups', False)}")
                        print(f"📢 Can read messages: {bot.get('can_read_all_group_messages', False)}")
                    else:
                        print(f"❌ Ошибка: {data.get('description')}")
                else:
                    print(f"❌ HTTP {response.status}")
                    text = await response.text()
                    print(text)
    except Exception as e:
        print(f"❌ Ошибка: {type(e).__name__}: {e}")
    
    print("\n" + "="*50)
    print("💡 ВАЖНО:")
    print("="*50)
    print("""
Если видишь Conflict ошибку, это значит что бот
запущен в ДВУХ местах одновременно с одним токеном.

Проверь:
1. Render Dashboard - есть ли другие сервисы?
2. Локальный компьютер - не запущен ли python bot.py?
3. Другие хостинги (Heroku, Railway, VPS)?

РЕШЕНИЕ:
- Останови все старые экземпляры
- Оставь только ОДИН запущенный бот
""")

if __name__ == "__main__":
    asyncio.run(check_token())
