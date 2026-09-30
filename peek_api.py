import aiohttp
from urllib.parse import quote
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType
import os

PEEK_URL = "https://server.peek.tg/api/nft/gifts/search"

# Актуальный список моделей подарков (из Telegram)
AVAILABLE_MODELS = [
    "PreciousPeach", "PlushPepe", "HeroicHelmet", "MightyArm", "IonGem",
    "DurovsCap", "NailBracelet", "PerfumeBottle", "MagicPotion", "MiniOscar",
    "AstralShard", "ArtisanBrick", "GemSignet", "GenieLamp", "BondedRing",
    "SharpTongue", "ElectricSkull", "BlingBinky", "WestsideSign", "KissedFrog",
    "LootBag", "SignetRing", "ToyBear", "IceCream", "DeskCalendar",
    "LightSword", "SnowGlobe", "ScaredCat", "JoyfulBundle", "EasterEgg",
    "JellyBunny", "RestlessJar", "PrettyPosy", "SwagBag", "StellarRocket",
    "PetSnake", "GingerCookie", "FreshSocks", "BerryBox", "InputKey",
    "VictoryMedal", "HypnoLollipop", "SnoopCigar", "EvilEye", "SpringBasket",
    "ValentineBox", "LushBouquet", "RecordPlayer", "InstantRamen", "HexPot",
    "SnakeBox", "IonicDryer", "HomemadeCake", "SpicedWine", "SantaHat"
]

# Функция для правильной капитализации (каждое слово с большой буквы, без пробелов)
def capitalize_model_name(text: str) -> str:
    """
    Превращает любой ввод в правильный формат:
    - money pot → MoneyPot
    - MONEY POT → MoneyPot
    - moneypot → MoneyPot
    """
    # Убираем лишние пробелы и подчёркивания
    text = text.strip().replace("_", " ")
    
    # Разделяем на слова
    words = text.split()
    
    # Каждое слово с большой буквы
    capitalized = ''.join(word.capitalize() for word in words)
    
    return capitalized

# Маппинг для известных названий (для быстрого поиска)
MODEL_NAME_MAP = {}

# Автоматически создаём маппинг из списка моделей
for model in AVAILABLE_MODELS:
    # Добавляем все возможные варианты написания
    lowercase = model.lower()
    MODEL_NAME_MAP[lowercase] = model
    
    # Также добавляем варианты с пробелами
    # Например: MoneyPot → money pot
    words = []
    current_word = model[0]
    for char in model[1:]:
        if char.isupper():
            words.append(current_word)
            current_word = char
        else:
            current_word += char
    words.append(current_word)
    
    # Добавляем варианты с пробелами
    with_spaces = ' '.join(words).lower()
    MODEL_NAME_MAP[with_spaces] = model

def normalize_model_name(user_input: str) -> str:
    """
    Нормализует название модели введённое пользователем
    
    Примеры:
    - "money pot" → "MoneyPot"
    - "MONEY POT" → "MoneyPot"
    - "moneypot" → "MoneyPot"
    - "evil eye" → "EvilEye"
    - "MoneyPot-5000" → "MoneyPot" (для поиска по ID)
    """
    # Убираем лишние пробелы
    user_input = user_input.strip()
    
    # Если это формат "модель-номер", берём только модель
    if "-" in user_input:
        user_input = user_input.split("-")[0].strip()
    
    # Приводим к lowercase для поиска
    search_key = user_input.lower().replace("_", " ")
    
    # Ищем в маппинге
    if search_key in MODEL_NAME_MAP:
        return MODEL_NAME_MAP[search_key]
    
    # Если не нашли, пробуем убрать все пробелы
    search_key_no_space = search_key.replace(" ", "")
    if search_key_no_space in MODEL_NAME_MAP:
        return MODEL_NAME_MAP[search_key_no_space]
    
    # Если всё равно не нашли, применяем стандартную капитализацию
    return capitalize_model_name(user_input)


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
        
        # Нормализуем название модели - убираем пробелы для API
        # API принимает слитные названия: EvilEye, BlueStar
        model_for_api = model.replace(" ", "")
        
        params = {
            "name": model_for_api,
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
    
    @staticmethod
    def format_summary(gifts: list) -> str:
        """Форматирует итоговый список найденных подарков"""
        if not gifts:
            return "Подарки не найдены."
        
        lines = [
            "📋 <b>ИТОГОВЫЙ СПИСОК НАЙДЕННЫХ ПОДАРКОВ</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        
        for i, gift in enumerate(gifts, 1):
            number = gift.get("giftNumber", "?")
            username = gift.get("username", "").replace("t.me/", "").lstrip("@")
            owner = gift.get("owner", "Неизвестно")
            gift_name = gift.get("giftName", gift.get("title", "?"))
            
            # Проверяем скрыт ли подарок
            # Если есть previousOwner, значит был передан/продан
            previous_owner = gift.get("previousOwner")
            is_visible = gift.get("isVisible", True)  # По умолчанию видим
            
            visibility_status = "👁️ Открыт" if is_visible else "🔒 Скрыт"
            
            # Ссылка на профиль
            if username:
                profile_link = f"https://t.me/{username}"
                owner_text = f"@{username}"
            else:
                profile_link = f"tg://user?id={gift.get('userId', '')}" if gift.get('userId') else None
                owner_text = owner
            
            # Ссылка на подарок
            from urllib.parse import quote
            safe_name = str(gift_name).replace(" ", "")
            gift_link = f"https://t.me/nft/{quote(safe_name)}-{number}"
            
            lines.append(f"{i}. 🎁 <b>{gift_name}</b> #{number}")
            lines.append(f"   👤 {owner_text}")
            if profile_link:
                lines.append(f"   🔗 {profile_link}")
            lines.append(f"   {visibility_status}")
            lines.append(f"   🎁 {gift_link}\n")
        
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>Всего найдено:</b> {len(gifts)}")
        
        return "\n".join(lines)


# Глобальный экземпляр API
api = PeekAPI()

async def peek_gift_info(gift_id: int, model: str) -> dict:
    """Обёртка для получения информации о подарке"""
    gift = await api.get_gift(model, gift_id)
    if gift:
        username = gift.get('username', '').replace('t.me/', '').lstrip('@')
        
        # Правильное определение видимости:
        # Если username пустой - подарок скрыт в профиле
        is_actually_visible = bool(username)
        
        return {
            'found': True,
            'giftNumber': gift.get('giftNumber', gift_id),
            'username': username,
            'owner': gift.get('owner'),
            'user_id': gift.get('userId'),  # ID владельца
            'is_visible': is_actually_visible,  # Реальная видимость!
            'is_upgraded': gift.get('isUpgraded', False),  # Улучшен ли
            'rarity': f"{gift.get('rarityModel', 0)}%",
            'attributes': gift,
            'giftName': gift.get('giftName', model),
            'title': gift.get('title', model)
        }
    else:
        return {
            'found': False
        }

def format_summary(gifts: list, model: str) -> list:
    """
    Форматирует итоговый список найденных подарков
    Возвращает список строк (для разбиения длинных списков на несколько сообщений)
    """
    if not gifts:
        return ["Подарки не найдены."]
    
    messages = []
    current_message = (
        "📋 <b>ИТОГОВЫЙ СПИСОК НАЙДЕННЫХ ПОДАРКОВ</b>\n"
        f"🎁 Модель: <b>{model}</b>\n"
        f"✅ Найдено: <b>{len(gifts)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, gift_data in enumerate(gifts, 1):
        number = gift_data.get("giftNumber", "?")
        username = gift_data.get("username", "")
        
        visibility_status = "👁️ Открыт" if gift_data.get('is_visible', True) else "🔒 Скрыт"
        
        # Ссылка на профиль
        if username:
            profile_link = f"https://t.me/{username}"
            owner_text = f"@{username}"
        else:
            profile_link = "-"
            owner_text = "Неизвестно"
        
        # Ссылка на подарок
        from urllib.parse import quote
        safe_name = model.replace(" ", "")
        gift_link = f"https://t.me/nft/{quote(safe_name)}-{number}"
        
        gift_text = (
            f"{i}. 🎁 <b>{model}</b> #{number}\n"
            f"   👤 {owner_text}\n"
            f"   🔗 {profile_link}\n"
            f"   {visibility_status}\n"
            f"   🎁 {gift_link}\n\n"
        )
        
        # Проверка лимита Telegram (4096 символов)
        # Оставляем запас для финальной статистики
        if len(current_message + gift_text) > 3800:
            messages.append(current_message)
            current_message = f"📋 <b>Продолжение ({len(messages) + 1})</b>\n\n" + gift_text
        else:
            current_message += gift_text
    
    # Добавить последнее сообщение
    if current_message:
        messages.append(current_message)
    
    # Добавить финальную статистику к последнему сообщению
    if messages:
        messages[-1] += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n<b>Всего найдено:</b> {len(gifts)}"
    
    return messages
