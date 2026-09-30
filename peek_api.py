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
            
            # Заголовки для обхода защиты peek.tg API
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Origin": "https://peek.tg",
                "Referer": "https://peek.tg/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site"
            }
            
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=20),
                headers=headers
            )

    async def get_gift(self, model: str, gift_number: int):
        await self._ensure_session()
        
        import logging
        
        params = {
            "name": model,
            "page": 1,
            "sortOrder": "asc",
            "giftNumber": gift_number,
            "limit": 20,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with self.session.get(PEEK_URL, params=params) as response:
                    logging.info(f"Gift {gift_number}: HTTP {response.status}")
                    
                    if response.status == 404:
                        return None

                    if response.status == 429 or response.status == 403:
                        # Rate limit или блокировка - ждём дольше
                        wait_time = 3 + (attempt * 2)  # 3, 5, 7 секунд
                        logging.warning(f"Gift {gift_number}: Rate limit, waiting {wait_time}s")
                        await __import__("asyncio").sleep(wait_time)
                        continue

                    if response.status != 200:
                        text = await response.text()
                        logging.error(f"Gift {gift_number}: HTTP {response.status}: {text[:100]}")
                        if attempt < max_retries - 1:
                            await __import__("asyncio").sleep(2)
                            continue
                        raise RuntimeError(f"Peek HTTP {response.status}: {text[:300]}")
                    
                    data = await response.json(content_type=None)
                    result = self._extract(data, gift_number)
                    
                    if result:
                        logging.info(f"Gift {gift_number}: FOUND!")
                    else:
                        logging.info(f"Gift {gift_number}: not found (no owner)")
                    
                    return result
            except Exception as e:
                logging.error(f"Gift {gift_number} attempt {attempt+1} exception: {type(e).__name__}: {e}")
                if attempt < max_retries - 1:
                    await __import__("asyncio").sleep(2)
                else:
                    raise
        
        # Если все попытки провалились
        return None

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
        username = gift.get("username", "").replace("t.me/", "").lstrip("@")
        user_id = gift.get("userId")
        owner = gift.get("owner")
        model = gift.get("model", "?")
        pattern = gift.get("pattern", "?")
        backdrop = gift.get("backdrop", "?")
        gift_name = gift.get("giftName", gift.get("title", "?"))
        
        # Информация о редкости
        rarity_model = gift.get("rarityModel", 0)
        rarity_pattern = gift.get("rarityPattern", 0)
        rarity_backdrop = gift.get("rarityBackdrop", 0)
        
        # Информация о рынке (может быть None)
        market = gift.get("market") or {}
        price = market.get("price")
        market_name = market.get("market", "").title()

        lines = [
            "🎁 <b>Найден подарок!</b>",
            f"🆔 ID: <code>{number}</code>",
            f"🎨 Название: <b>{gift_name}</b>",
            f"✨ Модель: <code>{model}</code> (редкость: {rarity_model}%)",
            f"🌸 Узор: <code>{pattern}</code> (редкость: {rarity_pattern}%)",
            f"🌈 Фон: <code>{backdrop}</code> (редкость: {rarity_backdrop}%)",
        ]

        if username:
            lines.append(f"👤 Владелец: @{username}")
            lines.append(f"🔗 https://t.me/{username}")
        elif user_id:
            lines.append(f"👤 Telegram ID: <code>{user_id}</code>")
        elif owner:
            lines.append(f"👤 Владелец: <code>{owner}</code>")
        else:
            lines.append("👤 Владелец: не указан")
        
        # Информация о цене на маркетплейсе
        if price and market_name:
            lines.append(f"💰 Цена на {market_name}: <b>{price} TON</b>")

        # Ссылка на подарок
        if gift_name and number not in ("?", None):
            from urllib.parse import quote
            safe_name = str(gift_name).replace(" ", "")
            lines.append(f"🎁 https://t.me/nft/{quote(safe_name)}-{number}")

        return "\n".join(lines)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
