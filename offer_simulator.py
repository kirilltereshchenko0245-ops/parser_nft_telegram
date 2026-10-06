"""Safe Telegram Gift offer simulator.

This module intentionally never calls Telegram payment/gift-offer methods.
It creates an ordinary bot message with inline buttons and stores state in SQLite.
The message is clearly marked as DEMO so it cannot be mistaken for a real sale.
"""
import os
import re
from urllib.parse import unquote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ui import esc, num, pretty

GIFT_RE = re.compile(r"(?:https?://)?t\.me/nft/([^/?#\s]+?)-(\d+)(?:[/?#\s]|$)", re.I)
AMOUNT_RE = re.compile(r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<currency>грам(?:м|s)?|gram(?:s)?|зв(?:езд|ёзд)?|stars?|⭐)", re.I)
LOCALE_RU = 'ru'
LOCALE_EN = 'en'
STATUS_PENDING = 'pending'
STATUS_ACCEPTED = 'accepted'
STATUS_DECLINED = 'declined'
STATUS_EXPIRED = 'expired'


def _currency(raw: str):
    x = raw.lower().replace('ё', 'е')
    if x.startswith(('грам', 'gram')):
        return 'gram'
    return 'stars'


def parse_offer_command(text: str):
    """Parse .офер [ру|англ] <gift url> <amount> <currency>."""
    text = (text or '').strip()
    m = re.match(r'^\.офер(?:\s+(.+))?$', text, re.I)
    if not m:
        return None
    tail = (m.group(1) or '').strip()
    locale = LOCALE_RU
    if re.match(r'^ру(?:с)?\b', tail, re.I):
        locale = LOCALE_RU
        tail = re.sub(r'^ру(?:с)?\s*', '', tail, flags=re.I)
    elif re.match(r'^англ(?:ийский)?\b', tail, re.I):
        locale = LOCALE_EN
        tail = re.sub(r'^англ(?:ийский)?\s*', '', tail, flags=re.I)
    gift = GIFT_RE.search(tail)
    if not gift:
        return {'locale': locale, 'error': 'gift_url'}
    gift_name = unquote(gift.group(1))
    gift_number = int(gift.group(2))
    after = tail[gift.end():].strip()
    amount = AMOUNT_RE.search(after)
    if not amount:
        return {'locale': locale, 'gift_name': gift_name, 'gift_number': gift_number, 'error': 'amount'}
    value = float(amount.group('amount').replace(',', '.'))
    if value <= 0:
        return {'locale': locale, 'gift_name': gift_name, 'gift_number': gift_number, 'error': 'amount'}
    return {
        'locale': locale,
        'gift_name': gift_name,
        'gift_number': gift_number,
        'amount': value,
        'currency': _currency(amount.group('currency')),
        'gift_url': gift.group(0).rstrip('.,)')
    }


def format_amount(amount, currency, locale):
    value = f'{amount:g}'
    if locale == LOCALE_EN:
        return f'{value} Gram' if currency == 'gram' else f'{value} Stars'
    return f'{value} Gram' if currency == 'gram' else f'{value} звёзд'


def gift_title(name, number):
    return f'{pretty(name)} #{num(number)}'


def offer_help_text(locale=LOCALE_RU):
    if locale == LOCALE_EN:
        return (
            '<b>Demo offer simulator</b>\n\n'
            'Usage:\n'
            '<code>.офер https://t.me/nft/PartySparkler-140864 150 gram</code>\n'
            '<code>.офер ру https://t.me/nft/PartySparkler-140864 4500 зв</code>\n'
            '<code>.офер англ https://t.me/nft/PartySparkler-140864 4500 stars</code>\n\n'
            'This simulator never transfers Stars or Gram.'
        )
    return (
        '<b>🧪 DEMO — симулятор оферов</b>\n\n'
        'Использование:\n'
        '<code>.офер https://t.me/nft/PartySparkler-140864 150 грам</code>\n'
        '<code>.офер ру https://t.me/nft/PartySparkler-140864 4500 зв</code>\n'
        '<code>.офер англ https://t.me/nft/PartySparkler-140864 4500 stars</code>\n\n'
        'Stars/Gram автоматически не списываются и не передаются.'
    )


def keyboard(offer_id: int, locale: str, status: str = STATUS_PENDING):
    if status == STATUS_PENDING:
        if locale == LOCALE_EN:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='✕ Decline', callback_data=f'of:decline:{offer_id}'),
                InlineKeyboardButton(text='✓ Accept', callback_data=f'of:accept:{offer_id}'),
            ]])
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='❌ Отклонить', callback_data=f'of:decline:{offer_id}'),
            InlineKeyboardButton(text='✅ Принять', callback_data=f'of:accept:{offer_id}'),
        ]])
    return None


def transfer_keyboard(offer: dict, locale: str):
    buyer_url = offer.get('buyer_url')
    help_url = os.getenv('OFFER_HELP_URL', '').strip()
    rows = []
    if buyer_url:
        rows.append([InlineKeyboardButton(text='Передать подарок' if locale == LOCALE_RU else 'Transfer gift', url=buyer_url)])
    if help_url:
        rows.append([InlineKeyboardButton(text='Как передать?' if locale == LOCALE_RU else 'How to transfer?', url=help_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def build_offer_text(offer: dict, locale: str, custom_emoji_id: str | None = None, custom_emoji_alt: str = '🎁'):
    name = gift_title(offer['gift_name'], offer['gift_number'])
    amount = format_amount(offer['amount'], offer['currency'], locale)
    sender = offer.get('seller_username') or str(offer.get('seller_id'))
    if sender and not str(sender).startswith('@') and not str(sender).isdigit():
        sender = '@' + str(sender)

    # The custom emoji entity is applied to the leading regular emoji.
    lead = '🎁'
    if locale == LOCALE_EN:
        text = (
            f'{lead} <b>Demo Telegram Gift Offer</b>\n'
            f'<b>{esc(name)}</b>\n\n'
            f'<b>DEMO — NOT A REAL TELEGRAM TRANSACTION</b>\n\n'
            f'User {esc(sender)} offers you <b>{esc(amount)}</b> for the gift <b>{esc(name)}</b>.\n\n'
            'This simulator does not charge, reserve, or transfer Stars/Gram.'
        )
    else:
        text = (
            f'{lead} <b>Telegram Gift Offer · DEMO</b>\n'
            f'<b>{esc(name)}</b>\n\n'
            f'⚠️ <b>ДЕМО — НЕ РЕАЛЬНАЯ СДЕЛКА TELEGRAM</b>\n\n'
            f'Пользователь {esc(sender)} предлагает Вам <b>{esc(amount)}</b> за подарок <b>{esc(name)}</b>.\n\n'
            'Stars/Gram не списываются, не резервируются и автоматически не передаются.'
        )

    if custom_emoji_id:
        # Bot API HTML supports <tg-emoji ...> and keeps normal HTML formatting intact.
        alt = custom_emoji_alt or lead
        text = text.replace(lead, f'<tg-emoji emoji-id="{esc(custom_emoji_id)}">{esc(alt)}</tg-emoji>', 1)
    return text, None


def accepted_text(offer: dict, locale: str):
    name = gift_title(offer['gift_name'], offer['gift_number'])
    amount = format_amount(offer['amount'], offer['currency'], locale)
    if locale == LOCALE_EN:
        return (
            f'🧪 <b>Demo offer for {esc(name)} accepted.</b>\n\n'
            f'Buyer agreed to <b>{esc(amount)}</b>.\n\n'
            '<b>No Stars or Gram were transferred.</b>\n'
            'The seller must complete any real transfer manually.'
        )
    return (
        f'🧪 <b>ДЕМО-офер на {esc(name)} принят.</b>\n\n'
        f'Покупатель согласился на <b>{esc(amount)}</b>.\n\n'
        '<b>Stars/Gram не передавались.</b>\n'
        'Любую реальную передачу продавец выполняет вручную.'
    )


def declined_text(offer: dict, locale: str):
    name = gift_title(offer['gift_name'], offer['gift_number'])
    return (f'🧪 <b>Demo offer for {esc(name)} declined.</b>' if locale == LOCALE_EN
            else f'🧪 <b>ДЕМО-офер на {esc(name)} отклонён.</b>')
