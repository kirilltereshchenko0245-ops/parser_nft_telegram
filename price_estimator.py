"""Price formatting fallback. No hard-coded model prices.
Live Telegram valuation is performed by live_gift_price.py when a user session is available.
"""

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def estimate_stars(g: dict):
    market = g.get('market') or {}
    for key in ('price', 'stars', 'starsPrice', 'priceStars', 'resaleStars', 'floorStars'):
        n = _num(market.get(key) if isinstance(market, dict) else None)
        if n and n > 0:
            return int(round(n)), 'market'
    raw = g.get('raw') or {}
    for key in ('starsPrice', 'resaleStars', 'resale_price_stars', 'valueStars', 'value_amount'):
        n = _num(raw.get(key))
        if n and n > 0:
            return int(round(n)), 'market'
    return None, 'unavailable'
