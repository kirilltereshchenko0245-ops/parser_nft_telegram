"""Approximate Telegram gift valuation for result cards.
Uses provider market data when available, then rarity/value fields, then a
conservative model floor table. It is explicitly an estimate, not a sale quote.
"""
import math

# Coarse Stars floors for cases where the upstream result has no market price.
# Kept intentionally conservative; admins can replace/extend these values later.
MODEL_FLOORS = {
    'PlushPepe': 4500, 'SnoopDogg': 3200, 'SnoopCigar': 2200,
    'AstralShard': 1800, 'RareBird': 1700, 'DurovsCap': 1500,
    'DiamondRing': 1400, 'PartySparkler': 900, 'EternalRose': 700,
    'VictoryMedal': 650, 'SkyStilettos': 600, 'SwagBag': 550,
}
DEFAULT_FLOOR = 350

def _num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def _rarity_factor(v):
    n = _num(v)
    if n is None: return 1.0
    # Percent rarity: lower percentage => higher multiplier.
    if n <= 0: return 1.0
    return max(1.0, min(4.0, math.sqrt(100.0 / n)))

def estimate_stars(g: dict):
    market = g.get('market') or {}
    for key in ('stars', 'starsPrice', 'priceStars', 'resaleStars', 'floorStars'):
        n = _num(market.get(key) if isinstance(market, dict) else None)
        if n and n > 0:
            return int(round(n)), 'market'
    raw = g.get('raw') or {}
    for key in ('starsPrice', 'resaleStars', 'resale_price_stars', 'valueStars'):
        n = _num(raw.get(key))
        if n and n > 0:
            return int(round(n)), 'market'
    base = MODEL_FLOORS.get(g.get('model'), DEFAULT_FLOOR)
    factor = 1.0
    factor *= _rarity_factor(g.get('rarityModel'))
    factor *= max(1.0, min(1.8, _rarity_factor(g.get('rarityPattern'))))
    factor *= max(1.0, min(1.8, _rarity_factor(g.get('rarityBackdrop'))))
    n = int(round(base * factor / 50) * 50)
    return max(50, n), 'estimate'
