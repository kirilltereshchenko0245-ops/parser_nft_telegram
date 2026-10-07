"""Live Telegram collectible-gift valuation.
Uses MTProto gift data instead of hard-coded model floors.
"""
import asyncio
import logging
import re
from typing import Any

from telethon import functions

log = logging.getLogger('giftparser.price')
_CACHE = {}
_LOCK = asyncio.Lock()
_TTL = 90


def _stars_amount(value: Any):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    amount = getattr(value, 'amount', None)
    if amount is not None:
        try:
            return int(amount)
        except Exception:
            return None
    return None


def _slug(gift: dict):
    slug = gift.get('gift_slug') or gift.get('slug')
    if slug:
        return str(slug)
    model = gift.get('model') or gift.get('giftName')
    number = gift.get('gift_num') or gift.get('giftNumber')
    if model and number:
        return f'{model}-{number}'
    return None


def _from_unique(unique):
    # Exact current resale price when this specific collectible is listed.
    resale = getattr(unique, 'resell_amount', None)
    if resale:
        if isinstance(resale, (list, tuple)):
            for item in resale:
                n = _stars_amount(item)
                if n:
                    return n, 'telegram_resale'
        n = _stars_amount(resale)
        if n:
            return n, 'telegram_resale'

    # Telegram's per-gift estimated value.
    value = getattr(unique, 'value_amount', None)
    currency = getattr(unique, 'value_currency', None)
    if value is not None and currency:
        if str(currency).upper() == 'STARS':
            return int(value), 'telegram_value'

    return None, None


async def live_price(parser_client, gift: dict):
    """Return (stars, source, details). Never raises to a parser result."""
    slug = _slug(gift)
    if not slug or not parser_client or not parser_client.client:
        return None, 'unavailable', {}

    key = slug.lower()
    now = asyncio.get_running_loop().time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1], cached[2], cached[3]

    async with _LOCK:
        cached = _CACHE.get(key)
        now = asyncio.get_running_loop().time()
        if cached and now - cached[0] < _TTL:
            return cached[1], cached[2], cached[3]
        try:
            unique_result = await parser_client.client(functions.payments.GetUniqueStarGiftRequest(slug=slug))
            unique = getattr(unique_result, 'gift', None) or unique_result
            stars, source = _from_unique(unique)
            details = {
                'slug': slug,
                'floor': None,
                'average': None,
                'listed': None,
            }
            if stars:
                _CACHE[key] = (now, stars, source, details)
                return stars, source, details

            info = await parser_client.client(functions.payments.GetUniqueStarGiftValueInfoRequest(slug=slug))
            currency = str(getattr(info, 'currency', '') or '').upper()
            value = getattr(info, 'value', None)
            floor = getattr(info, 'floor_price', None)
            average = getattr(info, 'average_price', None)
            details.update({'floor': floor, 'average': average, 'listed': getattr(info, 'listed_count', None)})
            if currency == 'STARS':
                # Prefer current floor, then Telegram estimated value, then average.
                chosen = floor or value or average
                if chosen:
                    _CACHE[key] = (now, int(chosen), 'telegram_market', details)
                    return int(chosen), 'telegram_market', details
        except Exception as exc:
            log.debug('Live gift price failed slug=%s: %s', slug, exc)

    _CACHE[key] = (now, None, 'unavailable', {})
    return None, 'unavailable', {}
