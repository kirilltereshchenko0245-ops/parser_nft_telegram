"""Клиент Peek.tg: ретраи, нормализация ответа, прокси."""
import asyncio
import difflib
import logging
import os
import random
from urllib.parse import quote

import aiohttp

try:
    from aiohttp_socks import ProxyConnector
except ImportError:  # прокси необязателен
    ProxyConnector = None

PEEK_URL = 'https://server.peek.tg/api/nft/gifts/search'
log = logging.getLogger(__name__)

# ═══════════ RARITY TIERS ═══════════
RARITY_ICON = {'legendary': '💎', 'rare': '🔥', 'common': '⭐', 'mass': '📦'}
RARITY_NAME = {'legendary': 'Legendary', 'rare': 'Rare', 'common': 'Common', 'mass': 'Mass'}

RARITY_MAP = {
    # 💎 Legendary
    'DurovsCap': 'legendary', 'HeroicHelmet': 'legendary', 'MightyArm': 'legendary',
    'IonGem': 'legendary', 'NailBracelet': 'legendary', 'MagicPotion': 'legendary',
    'MiniOscar': 'legendary', 'AstralShard': 'legendary', 'ArtisanBrick': 'legendary',
    # 🔥 Rare
    'PreciousPeach': 'rare', 'PlushPepe': 'rare', 'PerfumeBottle': 'rare',
    'GemSignet': 'rare', 'GenieLamp': 'rare', 'BondedRing': 'rare',
    'SharpTongue': 'rare', 'ElectricSkull': 'rare', 'BlingBinky': 'rare',
    'KissedFrog': 'rare', 'LootBag': 'rare', 'LightSword': 'rare', 'ScaredCat': 'rare',
    # ⭐ Common
    'WestsideSign': 'common', 'SignetRing': 'common', 'ToyBear': 'common',
    'IceCream': 'common', 'DeskCalendar': 'common', 'SnowGlobe': 'common',
    'JoyfulBundle': 'common', 'EasterEgg': 'common', 'JellyBunny': 'common',
    'RestlessJar': 'common', 'PrettyPosy': 'common', 'SwagBag': 'common',
    'StellarRocket': 'common', 'PetSnake': 'common', 'GingerCookie': 'common',
    'InputKey': 'common', 'VictoryMedal': 'common', 'HypnoLollipop': 'common',
    'SnoopCigar': 'common', 'EvilEye': 'common', 'SpringBasket': 'common',
    'ValentineBox': 'common', 'LushBouquet': 'common', 'RecordPlayer': 'common',
    'InstantRamen': 'common', 'HexPot': 'common', 'SnakeBox': 'common',
    'IonicDryer': 'common', 'HomemadeCake': 'common', 'SpicedWine': 'common',
    'SantaHat': 'common',
    # 📦 Mass
    'FreshSocks': 'mass', 'BerryBox': 'mass',
}

AVAILABLE_MODELS = list(RARITY_MAP.keys())

# Legendary, Rare, Common, Mass — группы для каталога
MODEL_GROUPS = {'legendary': [], 'rare': [], 'common': [], 'mass': []}
for m, r in RARITY_MAP.items():
    MODEL_GROUPS[r].append(m)


def rarity_info(model_name):
    """Вернёт (иконка, название_ранга, эмодзи_ранга) для модели."""
    r = RARITY_MAP.get(model_name, 'common')
    return RARITY_ICON[r], RARITY_NAME[r], r


def rarity_badge(model_name: str) -> str:
    """Иконка + цветовой ранг. Для UI."""
    icon, name, _ = rarity_info(model_name)
    return f'{icon} {name}'


# ───────────── названия моделей ─────────────
def _words(v):
    clean = ''.join(c if c.isalnum() else ' ' for c in v)
    return ''.join(w[:1].upper() + w[1:].lower() for w in clean.split())


def _split_camel(m):
    parts, cur = [], m[0]
    for c in m[1:]:
        if c.isupper():
            parts.append(cur)
            cur = c
        else:
            cur += c
    parts.append(cur)
    return ' '.join(parts)


_MODEL_MAP = {m.lower(): m for m in AVAILABLE_MODELS}
for _m in AVAILABLE_MODELS:
    _MODEL_MAP[_split_camel(_m).lower()] = _m


def normalize_model_name(v):
    """'plush pepe' / 'PLUSHPEPE' → 'PlushPepe'."""
    key = ' '.join(v.strip().split()).lower()
    return _MODEL_MAP.get(key) or _MODEL_MAP.get(key.replace(' ', '')) or _words(v.strip())


def is_known_model(name):
    return name in AVAILABLE_MODELS


def suggest_models(v, limit=3):
    """Похожие названия для опечаток: 'plsh pepe' → ['PlushPepe']."""
    key = ''.join(ch for ch in v.lower() if ch.isalnum())
    pool = {m.lower(): m for m in AVAILABLE_MODELS}
    close = difflib.get_close_matches(key, list(pool), n=limit, cutoff=0.6)
    found = [pool[c] for c in close]
    # подстрока тоже считаем подсказкой ('pepe' → PlushPepe)
    for low, m in pool.items():
        if len(key) >= 3 and key in low and m not in found:
            found.append(m)
    return found[:limit]


# ───────────── нормализация ответа ─────────────
def _first(d, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ''):
            return v
    return default


def _num(d):
    for k in ('giftNumber', 'gift_number', 'num', 'number', 'id'):
        try:
            if d.get(k) is not None:
                return int(d[k])
        except (TypeError, ValueError):
            pass
    return None


def normalize_gift(item, requested_number=None, requested_model=None):
    if not isinstance(item, dict):
        return None
    number = _num(item)
    if requested_number is not None and number is not None and number != requested_number:
        return None
    username = _first(item, 'username', 'ownerUsername', 'owner_username')
    if isinstance(username, str):
        username = username.replace('https://t.me/', '').replace('t.me/', '').lstrip('@').strip() or None
    market = item.get('market') if isinstance(item.get('market'), dict) else {}
    return {
        'giftNumber': number if number is not None else requested_number,
        'giftName': _first(item, 'giftName', 'gift_name', 'title', 'name', default=requested_model),
        'model': _first(item, 'model', 'modelName', 'model_name', default=requested_model),
        'pattern': _first(item, 'pattern', 'patternName', 'pattern_name'),
        'backdrop': _first(item, 'backdrop', 'backdropName', 'backdrop_name'),
        'rarityModel': _first(item, 'rarityModel', 'rarity_model'),
        'rarityPattern': _first(item, 'rarityPattern', 'rarity_pattern'),
        'rarityBackdrop': _first(item, 'rarityBackdrop', 'rarity_backdrop'),
        'username': username,
        'userId': _first(item, 'userId', 'user_id', 'ownerId', 'owner_id'),
        'owner': _first(item, 'owner'),
        'market': market,
        'raw': item,
    }


class PeekAPI:
    def __init__(self, concurrency=5, timeout=25):
        self.concurrency = max(1, min(int(concurrency), 20))
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.proxy_url = os.getenv('PROXY_URL') or None
        self.session = None
        self.sem = asyncio.Semaphore(self.concurrency)

    async def _ensure_session(self):
        if self.session and not self.session.closed:
            return
        connector = None
        if self.proxy_url:
            if ProxyConnector is None:
                raise RuntimeError('PROXY_URL задан, но aiohttp-socks не установлен')
            connector = ProxyConnector.from_url(self.proxy_url, rdns=True)
        self.session = aiohttp.ClientSession(
            connector=connector, timeout=self.timeout,
            headers={'User-Agent': 'GiftParser/5.1', 'Accept': 'application/json, text/plain, */*',
                     'Origin': 'https://peek.tg', 'Referer': 'https://peek.tg/'})

    async def get_gift(self, model, number, retries=4):
        """Вернёт подарок, None (если его нет) или бросит RuntimeError (если не удалось проверить)."""
        await self._ensure_session()
        params = {'name': model.replace(' ', ''), 'page': 1, 'sortOrder': 'asc',
                  'giftNumber': number, 'limit': 20}
        last_error = 'неизвестная ошибка'
        async with self.sem:
            for attempt in range(retries):
                try:
                    async with self.session.get(PEEK_URL, params=params) as r:
                        if r.status == 404:
                            return None
                        if r.status in (403, 429):
                            last_error = f'HTTP {r.status} (лимит запросов)'
                            await asyncio.sleep(min(15, 2 ** attempt + random.random()))
                            continue
                        if r.status >= 500:
                            last_error = f'HTTP {r.status}'
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                        if r.status != 200:
                            raise RuntimeError(f'Peek HTTP {r.status}: {(await r.text())[:250]}')
                        return self._extract(await r.json(content_type=None), number, model)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = f'сеть: {e}'
                    await asyncio.sleep(1.5 * (attempt + 1))
        # раньше здесь молча возвращался None — и подарок считался «не найденным»
        raise RuntimeError(f'Не удалось проверить #{number}: {last_error}')

    def _extract(self, data, number, model):
        if not isinstance(data, dict):
            return None
        results = data.get('results')
        if not isinstance(results, list):
            for k in ('data', 'items', 'gifts'):
                if isinstance(data.get(k), list):
                    results = data[k]
                    break
        if not results:
            return None
        for item in results:
            if isinstance(item, dict) and _num(item) == number:
                return normalize_gift(item, number, model)
        if len(results) == 1 and isinstance(results[0], dict):
            return normalize_gift(results[0], number, model)
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


def gift_url(g):
    name = (g.get('giftName') or g.get('model') or '').replace(' ', '')
    n = g.get('giftNumber')
    return f'https://t.me/nft/{quote(name)}-{n}' if name and n is not None else None


def owner_url(g):
    return f'https://t.me/{g["username"]}' if g.get('username') else None


# ───────────── утилиты для каталога по редкости ─────────────
def models_by_rarity(rarity: str) -> list[str]:
    """Вернёт список моделей заданной редкости."""
    return MODEL_GROUPS.get(rarity, [])


def rarity_distribution() -> dict:
    """Вернёт {rarity: количество_моделей}."""
    return {r: len(v) for r, v in MODEL_GROUPS.items()}


# ───────────── помощь для радара ─────────────
def radar_range(center: int, radius: int = 10) -> tuple[int, int]:
    """Вернёт (start, end) для сканирования вокруг центра."""
    start = max(1, center - radius)
    end = center + radius
    return start, end


def format_radar_result(found: list[dict], center: int, radius: int) -> str:
    """Красивое оформление результатов радара."""
    from ui import pretty, esc, num, block, kv
    density = len(found)
    text = (
        f'📡 <b>Gift Radar</b>\n'
        f'🎯 Центр: <code>#{num(center)}</code>\n'
        f'📡 Радиус: ±{radius} ID\n\n'
        f'<b>Плотность:</b> {density} находок в {2*radius+1} ID '
        f'({"🔥 высокая" if density > 3 else "⚡ средняя" if density else "💨 пусто"})'
    )
    if found:
        text += '\n\n📋 <b>Найдено:</b>'
        for g in sorted(found, key=lambda x: x.get('giftNumber', 0)):
            n = g.get('giftNumber')
            m = pretty(g.get('giftName') or g.get('model') or '')
            u = g.get('username')
            text += f'\n• <code>#{num(n)}</code> {esc(m)} {"👤 @" + esc(u) if u else "🔒 скрыт"}'
    return text
