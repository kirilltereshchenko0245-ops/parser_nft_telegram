
import logging
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

async def offer_isolation_test(send_func, *, chat_id, text, business_connection_id=None):
    """
    Four controlled Business-send tests. This does not retry PEER_FLOOD.
    Returns a list of (name, ok, detail).
    """
    results = []

    async def run(name, **kwargs):
        try:
            msg = await send_func(chat_id=chat_id, **kwargs)
            results.append((name, True, f"message_id={getattr(msg, 'message_id', '?')}"))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {e}"))

    base = {}
    if business_connection_id:
        base["business_connection_id"] = business_connection_id

    # 1. Plain text: no keyboard, no preview.
    await run("plain_text", text=text, **base)

    # 2. Inline keyboard only.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Тест", callback_data="offer_diag_test")]
    ])
    await run("keyboard_only", text=text, reply_markup=kb, **base)

    # 3. Preview/link only.
    preview_text = text + "\nhttps://t.me/nft/PartySparkler-140864"
    await run("preview_only", text=preview_text, **base)

    # 4. Keyboard + preview.
    await run("keyboard_plus_preview", text=preview_text, reply_markup=kb, **base)

    return results
