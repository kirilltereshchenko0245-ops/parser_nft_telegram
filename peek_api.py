import aiohttp
from urllib.parse import quote
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType
import os

PEEK_URL = "https://server.peek.tg/api/nft/gifts/search"

class PeekAPI:
    def __init__(self):
        # Получаем настройки прокси из переменных окружения
        self.proxy_url = os.getenv("PROXY_URL")  # например: socks5://192.168.0.100:10808
        self.session = None
        
    async def _ensure_session(self):
        """Создаёт сессию, если её ещё нет"""
        if self.session is None or self.session.closed:
            connector = None
            if self.proxy_url:
                # Используем rdns=True для remote DNS resolution
                connector = ProxyConnector.from_url(self.proxy_url, rdns=True)
            
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={
                    "User-Agent": "TelegramGiftParser/1.0"
                }
            )

    async def get_gift(self, model: str, gift_number: int):
        await self._ensure_session()
        
        params = {
            "name": model,
            "page": 1,
            "sortOrder": "asc",
            "giftNumber": gift_number,
            "limit": 20,
        }

        async with self.session.get(PEEK_URL, params=params) as response:
            if response.status == 404:
                return None

            if response.status == 429:
                # Простой retry при rate limit.
                await __import__("asyncio").sleep(2)
                async with self.session.get(PEEK_URL, params=params) as retry:
                    if retry.status != 200:
                        return None
                    data = await retry.json(content_type=None)
            elif response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Peek HTTP {response.status}: {text[:300]}")
            else:
                data = await response.json(content_type=None)

        return self._extract(data, gift_number)

    def _extract(self, data, gift_number):
        if not isinstance(data, dict):
            return None

        results = data.get("results")
        if not isinstance(results, list):
            # На случай небольшого изменения структуры API.
            for key in ("data", "items", "gifts"):
                if isinstance(data.get(key), list):
                    results = data[key]
                    break

        if not results:
            return None

        # Сначала ищем именно нужный номер.
        candidates = []
        for item in results:
            if not isinstance(item, dict):
                continue
            num = item.get("giftNumber", item.get("num", item.get("number")))
            try:
                if int(num) == int(gift_number):
                    return item
            except (TypeError, ValueError):
                pass
            candidates.append(item)

        # Если API уже отфильтровал giftNumber, первый результат подходит.
        if len(candidates) == 1:
            return candidates[0]

        return None

    @staticmethod
    def format_gift(gift: dict) -> str:
        number = gift.get("giftNumber", gift.get("num", "?"))
        username = gift.get("username")
        user_id = gift.get("userId")
        owner = gift.get("owner")
        model = gift.get("model", "?")
        pattern = gift.get("pattern", "?")
        backdrop = gift.get("backdrop", "?")

        lines = [
            "🎁 <b>Найден подарок!</b>",
            f"🆔 ID: <code>{number}</code>",
            f"🎨 Модель: <code>{model}</code>",
            f"✨ Узор: <code>{pattern}</code>",
            f"🌈 Фон: <code>{backdrop}</code>",
        ]

        if username:
            clean = str(username).lstrip("@")
            lines.append(f"👤 Владелец: @{clean}")
            lines.append(f"🔗 https://t.me/{clean}")
        elif user_id:
            lines.append(f"👤 Telegram ID: <code>{user_id}</code>")
        elif owner:
            lines.append(f"👤 Владелец: <code>{owner}</code>")
        else:
            lines.append("👤 Владелец: не указан")

        # Пытаемся построить стандартную collectible-ссылку.
        # Не используем её как гарантию: настоящий slug может отличаться.
        if model and number not in ("?", None):
            safe_model = str(model).replace(" ", "")
            lines.append(f"🎁 https://t.me/nft/{quote(safe_model)}-{number}")

        return "\n".join(lines)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
