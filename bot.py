import asyncio
import logging
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BufferedInputFile, BusinessConnection, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message
from dotenv import load_dotenv

import keyboards as kb
import ui
from database import Database
from peek_api import AVAILABLE_MODELS, PeekAPI, gift_url, is_known_model, normalize_model_name, suggest_models
from parsing import handlers as parser_handlers
from offer_simulator import (parse_offer_command, offer_help_text, keyboard as offer_keyboard, build_offer_text, accepted_alert_text, final_offer_text, declined_text, transfer_keyboard, STATUS_PENDING, STATUS_ACCEPTED, STATUS_DECLINED, STATUS_EXPIRED)

load_dotenv()
os.makedirs('logs', exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', handlers=[logging.FileHandler('logs/giftparser.log', encoding='utf-8'), logging.StreamHandler()])
log = logging.getLogger('giftparser')

TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID') or 0)
CONCURRENCY = int(os.getenv('PEEK_CONCURRENCY') or 1)  # По 1 ID последовательно
if not TOKEN:
    raise RuntimeError('BOT_TOKEN не указан в .env')

MAX_RANGE = 10_000        # максимум ID за один поиск
MAX_LIVE_CARDS = 30       # сколько карточек присылать «вживую» во время поиска
EDIT_INTERVAL = 1.5       # не чаще раза в N секунд обновляем статус (защита от flood)
GIFT_REQUEST_DELAY = float(os.getenv('GIFT_REQUEST_DELAY', '5'))
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '-1003864916255'))
API_REQUEST_LOCK = asyncio.Lock()
NEXT_API_REQUEST_AT = 0.0

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
db_path = os.getenv('DATABASE_PATH') or (os.path.join(os.getenv('RAILWAY_VOLUME_MOUNT_PATH'), 'bot_database.db') if os.getenv('RAILWAY_VOLUME_MOUNT_PATH') else 'bot_database.db')
db = Database(db_path)
if OWNER_ID:
    db.offer_access_add(OWNER_ID)
    db.admin_add(OWNER_ID)
api = PeekAPI(CONCURRENCY)
ACTIVE: set[int] = set()  # пользователи с запущенным поиском
OFFER_ACTION_LOCKS: dict[int, asyncio.Lock] = {}
OFFER_CHAT_LOCKS: dict[int, asyncio.Lock] = {}
OFFER_CHAT_LAST_EDIT_AT: dict[int, float] = {}
OFFER_CHAT_SEND_LOCKS: dict[int, asyncio.Lock] = {}
BUSINESS_SELLER_NAME_CACHE: dict[str, str] = {}
BUSINESS_OWNER_ID_CACHE: dict[str, int] = {}

# Подключаем router парсера
dp.include_router(parser_handlers.router)

# Middleware для передачи db в handlers
@dp.message.middleware()
@dp.business_message.middleware()
@dp.callback_query.middleware()
async def inject_db(handler, event, data):
    data['db'] = db
    return await handler(event, data)


class S(StatesGroup):
    model = State()
    range = State()
    id_model = State()
    id_number = State()
    random_model = State()
    random_count = State()
    admin_user_search = State()
    admin_add = State()
    offer_access_add = State()
    offer_access_username_add = State()
    offer_profile_add_name = State()
    offer_profile_add_url = State()


# ───────────────────────── помощники ─────────────────────────
def is_admin(uid):
    return db.admin_allowed(uid, OWNER_ID)


def is_unlimited(uid):
    return is_admin(uid) or bool((db.get_user(uid) or {}).get('is_premium'))


def user_menu(uid):
    return kb.main_menu(is_admin(uid), bool((db.get_user(uid) or {}).get('is_premium')), (not db.parser_admin_only()) or is_admin(uid))


def trial_left(uid):
    return int((db.get_user(uid) or {}).get('trial_searches_left', ui.TRIAL_DEFAULT))


def allowed(uid, count):
    return is_unlimited(uid) or count <= trial_left(uid)


async def safe(fn, *args, **kwargs):
    """Вызов Telegram API без блокировки обработчика на длинный FloodWait.

    Короткие серверные паузы повторяем автоматически. Длинные (например 121s)
    не заставляют офер "висеть" внутри handler: ошибка логируется и
    возвращается вызывающему коду. Основная защита от таких 429 — не спамить
    один чат и не писать аудит на каждое входящее сообщение.
    """
    for attempt in range(2):
        try:
            return await fn(*args, **kwargs)
        except TelegramRetryAfter as e:
            retry_after = int(getattr(e, 'retry_after', 0) or 0)
            if retry_after <= 3 and attempt == 0:
                await asyncio.sleep(retry_after + 0.15)
                continue
            raise


async def peek_get_gift(model: str, number: int):
    """Запрос к Peek: между стартами запросов выдерживается 5 секунд."""
    global NEXT_API_REQUEST_AT
    async with API_REQUEST_LOCK:
        loop = asyncio.get_running_loop()
        wait = max(0.0, NEXT_API_REQUEST_AT - loop.time())
        if wait:
            await asyncio.sleep(wait)
        started = loop.time()
        try:
            return await api.get_gift(model, number)
        finally:
            NEXT_API_REQUEST_AT = max(loop.time(), started) + GIFT_REQUEST_DELAY


def resolve_model(raw: str):
    """Находит каноническое имя модели независимо от регистра/пробелов."""
    value = normalize_model_name(raw)
    if is_known_model(value):
        return value
    key = re.sub(r'[^a-z0-9а-яё]', '', raw.lower())
    for candidate in AVAILABLE_MODELS:
        ck = re.sub(r'[^a-z0-9а-яё]', '', str(candidate).lower())
        if ck == key:
            return candidate
    return value


async def edit(msg: Message, text, markup=None):
    try:
        await safe(msg.edit_text, text, reply_markup=markup)
    except TelegramBadRequest as e:
        if 'not modified' not in str(e):
            log.warning('edit failed: %s', e)


def parse_range(text: str):
    t = text.strip().replace('—', '-').replace('–', '-').replace('→', '-')
    if '-' in t:
        a, b = (p.replace(' ', '') for p in t.split('-', 1))
    else:
        parts = t.split()
        if len(parts) == 1:
            a = b = parts[0]
        elif len(parts) == 2:
            a, b = parts
        else:
            raise ValueError
    a, b = int(a), int(b)
    if a < 1 or b < a or b - a + 1 > MAX_RANGE:
        raise ValueError
    return a, b


async def send_card(target: Message, uid: int, g: dict, large=False):
    """Карточка подарка. Telegram подтягивает превью t.me/nft — виден сам подарок."""
    url = gift_url(g)
    name = (g.get('giftName') or '').replace(' ', '')
    if url:
        lp = LinkPreviewOptions(url=url, show_above_text=True,
                                prefer_large_media=large, prefer_small_media=not large)
    else:
        lp = LinkPreviewOptions(is_disabled=True)
    fav = db.is_favorite(uid, g.get('giftNumber'), name)
    return await safe(target.answer, ui.gift_card(g), reply_markup=kb.gift_kb(g, fav), link_preview_options=lp)


def no_preview():
    return LinkPreviewOptions(is_disabled=True)


def _offer_lock(offer_id: int) -> asyncio.Lock:
    lock = OFFER_ACTION_LOCKS.get(offer_id)
    if lock is None:
        lock = asyncio.Lock()
        OFFER_ACTION_LOCKS[offer_id] = lock
    return lock


async def _edit_offer_message(message: Message, text: str, markup=None, business_connection_id=None):
    """Edit one offer message, serialized per chat to avoid edit bursts."""
    chat_id = int(message.chat.id)
    lock = OFFER_CHAT_LOCKS.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        OFFER_CHAT_LOCKS[chat_id] = lock
    async with lock:
        loop = asyncio.get_running_loop()
        wait = 0.75 - (loop.time() - OFFER_CHAT_LAST_EDIT_AT.get(chat_id, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        kwargs = dict(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(show_above_text=True, prefer_large_media=True),
        )
        bc_id = business_connection_id or getattr(message, 'business_connection_id', None)
        if bc_id:
            kwargs['business_connection_id'] = bc_id
        result = await safe(bot.edit_message_text, **kwargs)
        OFFER_CHAT_LAST_EDIT_AT[chat_id] = loop.time()
        return result


async def _send_offer_message(message: Message, text: str, markup=None, business_connection_id=None):
    """Send the offer as a new message, without editMessageText.

    Business chats were hitting Telegram's edit flood limit. Sending the final
    offer avoids that edit path completely. Different chats can send in parallel;
    only repeated sends to the same chat are serialized.
    """
    chat_id = int(message.chat.id)
    lock = OFFER_CHAT_SEND_LOCKS.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        OFFER_CHAT_SEND_LOCKS[chat_id] = lock
    async with lock:
        kwargs = dict(
            chat_id=message.chat.id,
            text=text,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(show_above_text=True, prefer_large_media=True),
        )
        bc_id = business_connection_id or getattr(message, 'business_connection_id', None)
        if bc_id:
            kwargs['business_connection_id'] = bc_id
        return await safe(bot.send_message, **kwargs)


async def _delete_offer_command_message(message: Message, business_connection_id=None):
    """Delete the original .офер message after the offer is safely sent."""
    bc_id = business_connection_id or getattr(message, 'business_connection_id', None)
    if bc_id:
        return await safe(bot.delete_business_messages, business_connection_id=bc_id, message_ids=[message.message_id])
    return await safe(bot.delete_message, chat_id=message.chat.id, message_id=message.message_id)


async def _business_seller_display_name(business_connection_id):
    """Resolve and cache the connected account's profile display name.

    Never use the @username for the offer text.
    """
    if business_connection_id in BUSINESS_SELLER_NAME_CACHE:
        return BUSINESS_SELLER_NAME_CACHE[business_connection_id]
    try:
        bc = await safe(bot.get_business_connection, business_connection_id)
        owner = getattr(bc, 'user', None) if bc else None
        owner_id = int(getattr(owner, 'id', 0) or 0)
        if owner_id:
            BUSINESS_OWNER_ID_CACHE[business_connection_id] = owner_id
        first = (getattr(owner, 'first_name', None) or '').strip()
        last = (getattr(owner, 'last_name', None) or '').strip()
        name = ' '.join(x for x in (first, last) if x).strip()
        if not name and owner:
            try:
                chat = await safe(bot.get_chat, int(owner.id))
                first = (getattr(chat, 'first_name', None) or '').strip()
                last = (getattr(chat, 'last_name', None) or '').strip()
                name = ' '.join(x for x in (first, last) if x).strip()
            except Exception:
                pass
        if name:
            BUSINESS_SELLER_NAME_CACHE[business_connection_id] = name
        return name or None
    except Exception:
        return None


# ───────────────────────── аудит / логи ─────────────────────────
async def audit(action: str, uid: int | None = None, details: str = ''):
    actor = f'<code>{uid}</code>' if uid is not None else '<code>system</code>'
    text = f'🧾 <b>{ui.esc(action)}</b>\n👤 {actor}'
    if details:
        text += f'\n{details}'
    try:
        await safe(bot.send_message, LOG_CHANNEL_ID, text, disable_web_page_preview=True)
    except Exception as e:
        log.error('LOG CHANNEL FAILED channel=%s action=%s error=%r', LOG_CHANNEL_ID, action, e)


# ───────────────────────── доступ ─────────────────────────
@dp.message.middleware()
async def access_message(handler, event: Message, data):
    if event.from_user:
        uid = event.from_user.id
        db.upsert_user(uid, event.from_user.username, event.from_user.first_name)
        if db.is_banned(uid):
            await audit('Заблокированный пользователь попытался выполнить действие', uid, f'Текст: <code>{ui.esc(event.text or "")}</code>')
            await event.answer('🚫 <b>Доступ ограничен.</b>')
            return
    return await handler(event, data)


@dp.callback_query.middleware()
async def access_callback(handler, event: CallbackQuery, data):
    if db.is_banned(event.from_user.id):
        await audit('Заблокированный пользователь нажал кнопку', event.from_user.id, f'Callback: <code>{ui.esc(event.data or "")}</code>')
        await event.answer('🚫 Доступ ограничен', show_alert=True)
        return
    return await handler(event, data)


# ───────────────────────── старт / общее ─────────────────────────
@dp.message(Command('start'))
@dp.message(Command('menu'))
async def start(m: Message, state: FSMContext):
    await audit('Запуск /start', m.from_user.id)
    await state.clear()
    u = db.get_user(m.from_user.id) or {}
    await m.answer(ui.welcome(u, is_admin(m.from_user.id)), reply_markup=user_menu(m.from_user.id))


@dp.message(F.text == kb.BTN_CANCEL)
async def cancel_action(m: Message, state: FSMContext):
    await state.clear()
    await m.answer('↩️ Действие отменено.', reply_markup=user_menu(m.from_user.id))


@dp.message(Command('stop'))
@dp.message(F.text == kb.BTN_STOP)
async def stop_cmd(m: Message):
    await audit('Остановка поиска', m.from_user.id)
    if m.from_user.id not in ACTIVE:
        return await m.answer('Сейчас нет активного поиска.', reply_markup=user_menu(m.from_user.id))
    db.stop(m.from_user.id)
    await m.answer('⏹ <b>Останавливаю…</b> Текущие запросы завершатся, прогресс сохранится.')


@dp.callback_query(F.data == 'stop')
async def stop_btn(c: CallbackQuery):
    if c.from_user.id not in ACTIVE:
        return await c.answer('Поиск уже не выполняется')
    db.stop(c.from_user.id)
    await c.answer('⏹ Останавливаю…')


@dp.callback_query(F.data == 'noop')
async def noop(c: CallbackQuery):
    await c.answer()


@dp.message(F.text == kb.BTN_HELP)
async def help_cmd(m: Message):
    await m.answer(ui.help_text())


@dp.message(Command('help'))
async def help_command(m: Message):
    await m.answer(ui.help_text())


@dp.message(F.text == kb.BTN_PROFILE)
async def profile(m: Message):
    uid = m.from_user.id
    await m.answer(ui.profile(db.get_user(uid) or {}, uid, m.from_user.username, db.stats(), is_admin(uid)))


@dp.message(F.text == kb.BTN_PREMIUM)
async def premium(m: Message):
    await m.answer(ui.premium_text())


# ───────────────────────── выбор модели ─────────────────────────
async def ask_range(target: Message, state: FSMContext, model: str):
    await state.update_data(model=model)
    await state.set_state(S.range)
    await target.answer(
        f'🎁 Модель · <b>{ui.esc(ui.pretty(model))}</b>\n\n'
        'Введите диапазон ID, например <code>13000-13500</code>\n'
        f'<i>Максимум {ui.num(MAX_RANGE)} ID за один поиск.</i>',
        reply_markup=kb.cancel())


@dp.message(F.text == kb.BTN_SEARCH)
async def new_search(m: Message, state: FSMContext):
    await audit('Открыт новый поиск', m.from_user.id)
    if not allowed(m.from_user.id, 1):
        return await m.answer('🆓 <b>Лимит Trial закончился.</b>\n\n💎 Подключите Premium для продолжения.')
    await state.set_state(S.model)
    last = db.last_resumable_search(m.from_user.id)
    last_line = ''
    if last:
        last_line = f'\n\n<i>Последний парсинг: #{last["id"]} · последний проверенный ID <code>{last["last_checked_id"]}</code></i>'
    await m.answer(
        '🔎 <b>Новый поиск</b>\n\nВведите название модели:\n'
        + ui.block('<code>Plush Pepe</code>', '<code>Eternal Rose</code>') +
        '\n<i>Или выберите модель через «🎁 Модели».</i>' + last_line,
        reply_markup=kb.cancel())


@dp.message(S.model)
async def model_step(m: Message, state: FSMContext):
    raw = (m.text or '').strip()
    if len(raw) < 2:
        return await m.answer('❌ Введите название модели.')
    name = resolve_model(raw)
    if is_known_model(name):
        return await ask_range(m, state, name)
    options = suggest_models(raw)
    if options:
        return await m.answer(
            f'🤔 Не нашёл «<b>{ui.esc(raw)}</b>» в каталоге. Возможно, вы имели в виду:',
            reply_markup=kb.model_suggest_kb(options, name))
    await ask_range(m, state, name)


@dp.callback_query(F.data.startswith('pm:'))
async def pick_model(c: CallbackQuery, state: FSMContext):
    """Выбор модели кнопкой — из каталога или из подсказок."""
    model = c.data.split(':', 1)[1]
    await c.answer()
    if not allowed(c.from_user.id, 1):
        return await c.message.answer('🆓 <b>Лимит Trial закончился.</b>\n\n💎 Подключите Premium для продолжения.')
    current_state = await state.get_state()
    if current_state == S.random_model.state:
        await state.update_data(model=model)
        await state.set_state(S.random_count)
        return await c.message.answer(f'🎲 Модель · <b>{ui.esc(ui.pretty(model))}</b>\n\nСколько случайных ID проверить? <code>1–20</code>', reply_markup=kb.cancel())
    if current_state == S.id_model.state:
        await state.update_data(model=model)
        await state.set_state(S.id_number)
        return await c.message.answer(f'🎁 Модель · <b>{ui.esc(ui.pretty(model))}</b>\n\nВведите номер подарка:', reply_markup=kb.cancel())
    await ask_range(c.message, state, model)


@dp.message(S.range)
async def range_step(m: Message, state: FSMContext):
    try:
        a, b = parse_range(m.text or '')
    except ValueError:
        return await m.answer(
            f'❌ Формат: <code>начало-конец</code>, не больше {ui.num(MAX_RANGE)} ID.\nНапример: <code>13000-13500</code>')
    count = b - a + 1
    if not allowed(m.from_user.id, count):
        return await m.answer(
            f'🆓 Осталось <b>{trial_left(m.from_user.id)}</b> проверок, а в диапазоне <b>{ui.num(count)}</b> ID.\n'
            'Уменьшите диапазон или подключите 💎 Premium.')
    model = (await state.get_data())['model']
    await state.clear()
    await m.answer('🚀 Запускаю…', reply_markup=user_menu(m.from_user.id))
    await run_search(m, m.from_user.id, model, a, b)


# ───────────────────────── поиск по диапазону ─────────────────────────
async def run_search(chat: Message, uid: int, model: str, start: int, end: int, sid=None):
    if uid in ACTIVE:
        return await chat.answer('⏳ У вас уже идёт поиск. Остановите его кнопкой под статусом или командой /stop.')

    total = end - start + 1
    if sid is None:
        sid = db.create_search(uid, model, start, end)
        checked = found = 0
        current = start
    else:
        x = db.get_search(sid, uid)
        checked, found, current = x['checked_count'], x['found_count'], x['last_checked_id'] + 1
        if current > end:
            return await chat.answer('✅ Этот поиск уже полностью обработан.')

    ACTIVE.add(uid)
    db.clear_stop(uid)
    unlimited = is_unlimited(uid)
    t0 = time.monotonic()
    session_checked = skipped = shown = fail_streak = 0
    last_edit = 0.0
    batch_size = 1  # По 1 ID = показывает результаты каждые 5 секунд

    def card(state, **kw):
        elapsed = time.monotonic() - t0
        rate = session_checked / elapsed if elapsed > 0 else 0
        eta = (total - checked) / rate if rate else 0
        return ui.search_card(state, model, start, end, checked, total, found,
                              rate=rate, eta=eta, elapsed=elapsed, skipped=skipped,
                              shown_cards=MAX_LIVE_CARDS, **kw)

    msg = await chat.answer(card('running'), reply_markup=kb.running_kb())
    try:
        while current <= end:
            # ── остановка пользователем
            if db.should_stop(uid):
                db.update_search(sid, current - 1, checked, found, 'stopped')
                await edit(msg, ui.search_card('running', model, start, end, checked, total, found,
                                               shown_cards=MAX_LIVE_CARDS))
                await chat.answer(card('stopped') + f'\n\n🆔 Последний проверенный ID: <code>{current - 1}</code>',
                                       reply_markup=kb.search_done_kb(sid, found, resumable=True))
                return

            nums = list(range(current, min(end, current + batch_size - 1) + 1))
            results = dict(zip(nums, await asyncio.gather(*(peek_get_gift(model, n) for n in nums),
                                                          return_exceptions=True)))
            # ── повторная попытка для сбойных ID
            failed = [n for n, r in results.items() if isinstance(r, Exception)]
            if failed:
                await asyncio.sleep(2)
                again = await asyncio.gather(*(peek_get_gift(model, n) for n in failed), return_exceptions=True)
                results.update(zip(failed, again))
                failed = [n for n, r in results.items() if isinstance(r, Exception)]

            # Не продвигаем checkpoint, если хотя бы один ID не удалось проверить.
            # Иначе при resume этот ID был бы навсегда пропущен как «не найденный».
            if failed:
                fail_streak += 1
                sample = results[failed[0]]
                log.warning('Batch %s-%s has %s failed IDs (%s/3): %s',
                            nums[0], nums[-1], len(failed), fail_streak, sample)
                if fail_streak >= 3:
                    raise RuntimeError(f'Peek не отвечает; не проверен ID #{failed[0]}')
                await asyncio.sleep(5 * fail_streak)
                continue
            fail_streak = 0

            for n in nums:
                r = results[n]
                if isinstance(r, Exception):
                    skipped += 1
                    continue
                if r:
                    found += 1
                    db.add_gift(sid, uid, r)
                    if shown < MAX_LIVE_CARDS:
                        shown += 1
                        await send_card(chat, uid, r)
            checked += len(nums)
            session_checked += len(nums)
            current = nums[-1] + 1
            db.update_search(sid, nums[-1], checked, found)

            # ── Trial списывается по ходу поиска, а не в конце
            if not unlimited:
                db.consume_trial(uid, len(nums))
                if trial_left(uid) <= 0 and current <= end:
                    db.update_search(sid, nums[-1], checked, found, 'stopped')
                    await edit(msg, ui.search_card('running', model, start, end, checked, total, found,
                                                   shown_cards=MAX_LIVE_CARDS))
                    await chat.answer(card('limit') + f'\n\n🆔 Последний проверенный ID: <code>{nums[-1]}</code>',
                                           reply_markup=kb.search_done_kb(sid, found, resumable=True))
                    return

            if time.monotonic() - last_edit >= EDIT_INTERVAL:
                last_edit = time.monotonic()
                await edit(msg, card('running'), kb.running_kb())

        db.update_search(sid, end, checked, found, 'completed')
        await edit(msg, ui.search_card('running', model, start, end, checked, total, found,
                                       shown_cards=MAX_LIVE_CARDS))
        await chat.answer(card('done') + f'\n\n🆔 Последний проверенный ID: <code>{end}</code>',
                               reply_markup=kb.search_done_kb(sid, found))
    except Exception:
        log.exception('Search failed uid=%s sid=%s', uid, sid)
        last_id = max(start - 1, current - 1)
        db.update_search(sid, last_id, checked, found, 'error')
        await edit(msg, ui.search_card('running', model, start, end, checked, total, found,
                                       shown_cards=MAX_LIVE_CARDS))
        await chat.answer(card('error') + f'\n\n🆔 Последний проверенный ID: <code>{last_id}</code>',
                               reply_markup=kb.search_done_kb(sid, found, resumable=True))
    finally:
        ACTIVE.discard(uid)
        db.clear_stop(uid)


# ───────────────────────── поиск по одному ID ─────────────────────────
@dp.message(F.text == kb.BTN_ID)
async def id_search(m: Message, state: FSMContext):
    await audit('Поиск одного ID', m.from_user.id)
    await state.set_state(S.id_model)
    await m.answer('🆔 <b>Поиск по ID</b>\n\nВведите название модели:', reply_markup=kb.cancel())


@dp.message(S.id_model)
async def id_model(m: Message, state: FSMContext):
    raw = (m.text or '').strip()
    if len(raw) < 2:
        return await m.answer('❌ Введите название модели.')
    name = resolve_model(raw)
    if not is_known_model(name):
        options = suggest_models(raw)
        if options:
            return await m.answer('🤔 Выберите модель:', reply_markup=kb.model_suggest_kb(options, name))
        return await m.answer('❌ Такой модели нет. Например: <code>Eternal Rose</code>.')
    await state.update_data(model=name)
    await state.set_state(S.id_number)
    await m.answer(f'🎁 Модель · <b>{ui.esc(ui.pretty(name))}</b>\n\nВведите номер подарка:', reply_markup=kb.cancel())


@dp.message(S.id_number)
async def id_number(m: Message, state: FSMContext):
    text = (m.text or '').strip().lstrip('#').replace(' ', '')
    if not text.isdigit() or int(text) <= 0:
        return await m.answer('❌ Номер должен быть положительным числом.')
    n = int(text)
    uid = m.from_user.id
    if not allowed(uid, 1):
        return await m.answer('🆓 <b>Лимит Trial закончился.</b>\n\n💎 Подключите Premium для продолжения.')
    if uid in ACTIVE:
        return await m.answer('⏳ Сначала завершите или остановите текущую проверку.')
    model = (await state.get_data())['model']
    await state.clear()
    ACTIVE.add(uid)
    await m.answer('🔎 Проверяю…', reply_markup=user_menu(uid))
    try:
        g = await peek_get_gift(model, n)
        if not is_unlimited(uid):
            db.consume_trial(uid, 1)
        if not g:
            return await m.answer(f'🔎 {ui.esc(ui.pretty(model))} <code>#{n}</code> — не найден.')
        await send_card(m, uid, g, large=True)
    except RuntimeError as e:
        await m.answer(f'⚠️ {ui.esc(e)}\nПроверка не засчитана в Trial.')
    finally:
        ACTIVE.discard(uid)


# ───────────────────────── случайный поиск ─────────────────────────
@dp.message(F.text == kb.BTN_RANDOM)
async def random_search(m: Message, state: FSMContext):
    await audit('Случайный поиск', m.from_user.id)
    if not allowed(m.from_user.id, 1):
        return await m.answer('🆓 <b>Лимит Trial закончился.</b>')
    await state.set_state(S.random_model)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔎 Найти модель', callback_data='random:model_search')],
        [InlineKeyboardButton(text='🎁 Выбрать из моделей', callback_data='models:1')],
        [InlineKeyboardButton(text='↩️ Отмена', callback_data='noop')],
    ])
    await m.answer('🎲 <b>Случайный подарок</b>\n\nВыберите модель или нажмите «Найти модель».', reply_markup=markup)


@dp.callback_query(F.data == 'random:model_search')
async def random_model_search(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(S.random_model)
    await c.message.answer('🔎 <b>Поиск модели</b>\n\nВведите название модели:', reply_markup=kb.cancel())


@dp.message(S.random_model)
async def random_model(m: Message, state: FSMContext):
    raw = (m.text or '').strip()
    if len(raw) < 2:
        return await m.answer('❌ Введите название модели.')
    name = resolve_model(raw)
    if not is_known_model(name):
        options = suggest_models(raw)
        if options:
            return await m.answer('🤔 Выберите модель:', reply_markup=kb.model_suggest_kb(options, name))
        return await m.answer('❌ Модель не найдена. Попробуйте другое название.')
    await state.update_data(model=name)
    await state.set_state(S.random_count)
    await m.answer(f'🎲 Модель · <b>{ui.esc(ui.pretty(name))}</b>\n\nСколько случайных ID проверить? <code>1–20</code>', reply_markup=kb.cancel())


@dp.message(S.random_count)
async def random_count(m: Message, state: FSMContext):
    text = (m.text or '').strip()
    if not text.isdigit() or not 1 <= int(text) <= 20:
        return await m.answer('❌ Введите число от 1 до 20.')
    count, uid = int(text), m.from_user.id
    if not allowed(uid, count):
        return await m.answer(f'🆓 Недостаточно проверок: осталось <b>{trial_left(uid)}</b>.')
    model = (await state.get_data()).get('model')
    await state.clear()

    if uid in ACTIVE:
        return await m.answer('⏳ У вас уже идёт проверка. Дождитесь её завершения или остановите.')
    model = model or random.choice(AVAILABLE_MODELS)
    numbers = random.sample(range(1, 100_000), count)
    ACTIVE.add(uid)
    db.clear_stop(uid)
    status = await m.answer(f'🎲 <b>{ui.esc(ui.pretty(model))}</b>\n🔢 Проверяю {count} случайных ID…',
                            reply_markup=kb.running_kb())
    found = checked = errors = 0
    stopped = False
    try:
        for n in numbers:
            if db.should_stop(uid):
                stopped = True
                break
            try:
                gift = await peek_get_gift(model, n)
            except Exception as exc:
                errors += 1
                log.warning('Random lookup failed for #%s: %s', n, exc)
            else:
                checked += 1
                if gift:
                    found += 1
                    await send_card(m, uid, gift)
            if not is_unlimited(uid):
                db.consume_trial(uid, 1)
        tail = f'\n⚠️ Ошибок сети: {errors}' if errors else ''
        state_label = '⏹ <b>Остановлено</b>' if stopped else '🎲 <b>Готово</b>'
        processed = checked + errors
        await edit(status, f'{state_label}\n{ui.block(ui.kv("🎁", "Модель", ui.esc(ui.pretty(model))), ui.kv("🔢", "Проверено", f"{processed} из {count}"), ui.kv("✨", "Найдено", found))}{tail}', user_menu(uid))
    finally:
        ACTIVE.discard(uid)
        db.clear_stop(uid)


# ───────────────────────── карточки / избранное ─────────────────────────
def gift_from_row(uid, collection, number):
    """Собираем подарок для карточки: из находок, иначе из избранного."""
    row = db.find_gift(uid, collection, number)
    if row:
        return ui.row_to_gift(row)
    return {'giftNumber': number, 'giftName': collection, 'model': None}


@dp.callback_query(F.data.startswith('fav:'))
async def favorite_toggle(c: CallbackQuery):
    _, name, n = c.data.split(':', 2)
    n, uid = int(n), c.from_user.id
    if db.is_favorite(uid, n, name):
        db.remove_favorite(uid, n, name)
        now_fav, note = False, '☆ Убрано из избранного'
    else:
        row = db.find_gift(uid, name, n) or {}
        db.add_favorite(uid, n, name, row.get('model'), row.get('username'))
        now_fav, note = True, '⭐ Добавлено в избранное'
    await c.answer(note)
    if c.message and c.message.reply_markup:
        try:
            await c.message.edit_reply_markup(reply_markup=kb.with_fav_state(c.message.reply_markup, now_fav))
        except TelegramBadRequest:
            pass


@dp.callback_query(F.data.startswith('gc:'))
async def open_card(c: CallbackQuery):
    _, name, n = c.data.split(':', 2)
    await c.answer()
    g = gift_from_row(c.from_user.id, name, int(n))
    await send_card(c.message, c.from_user.id, g, large=True)


# ───────────────────────── списки с пагинацией ─────────────────────────
def render_list(kind, arg, uid, page):
    """Возвращает (текст, клавиатура) для списка результатов или избранного."""
    per = kb.LIST_PER_PAGE
    if kind == 'res':
        sid = int(arg)
        x = db.get_search(sid, uid)
        if not x:
            return None
        count = db.count_gifts(sid)
        pages = max(1, -(-count // per))
        page = max(1, min(page, pages))
        gifts = [ui.row_to_gift(r) for r in db.gifts_page(sid, (page - 1) * per, per)]
        title = f'📋 <b>Результаты · поиск #{sid}</b>\n🎁 {ui.esc(ui.pretty(x["model"]))}'
        return (ui.list_header(title, count, page, pages),
                kb.gifts_list_kb(gifts, 'res', sid, page, pages, export_sid=sid, back=f'hv:{sid}'))
    # избранное
    count = db.count_favorites(uid)
    pages = max(1, -(-count // per))
    page = max(1, min(page, pages))
    gifts = []
    for r in db.favorites_page(uid, (page - 1) * per, per):
        g = gift_from_row(uid, r['gift_name'] or r['model'], r['gift_number'])
        g['model'] = g.get('model') or r['model']
        g['username'] = g.get('username') or r['username']
        gifts.append(g)
    return (ui.list_header('⭐ <b>Избранное</b>', count, page, pages),
            kb.gifts_list_kb(gifts, 'fav', 0, page, pages))


@dp.callback_query(F.data.startswith('pg:'))
async def pager(c: CallbackQuery):
    _, kind, arg, page = c.data.split(':')
    out = render_list(kind, arg, c.from_user.id, int(page))
    if not out:
        return await c.answer('Не найдено', show_alert=True)
    await c.answer()
    await edit(c.message, *out)


@dp.message(F.text == kb.BTN_FAVS)
async def favorites(m: Message):
    if not db.count_favorites(m.from_user.id):
        return await m.answer('⭐ <b>Избранное пусто</b>\n\nНажимайте «☆ В избранное» под найденными подарками.')
    text, markup = render_list('fav', 0, m.from_user.id, 1)
    await m.answer(text, reply_markup=markup)


# ───────────────────────── история ─────────────────────────
@dp.message(F.text == kb.BTN_HISTORY)
async def history(m: Message):
    rows = db.history(m.from_user.id)
    if not rows:
        return await m.answer('📚 <b>История пуста</b>\n\nЗапустите первый поиск — «🔎 Новый поиск».')
    await m.answer(ui.history_list(rows), reply_markup=kb.history_list_kb(rows))


@dp.callback_query(F.data == 'hl')
async def history_back(c: CallbackQuery):
    rows = db.history(c.from_user.id)
    await c.answer()
    await edit(c.message, ui.history_list(rows), kb.history_list_kb(rows))


@dp.callback_query(F.data.startswith('hv:'))
async def history_view(c: CallbackQuery):
    x = db.get_search(int(c.data.split(':')[1]), c.from_user.id)
    if not x:
        return await c.answer('Поиск не найден', show_alert=True)
    await c.answer()
    await edit(c.message, ui.search_detail(x), kb.history_detail_kb(x))


@dp.callback_query(F.data.startswith('resume:'))
async def resume(c: CallbackQuery):
    uid = c.from_user.id
    x = db.get_search(int(c.data.split(':')[1]), uid)
    if not x:
        return await c.answer('Поиск не найден', show_alert=True)
    if x['status'] not in ('stopped', 'error'):
        return await c.answer('Этот поиск уже завершён.')
    remaining = x['end_id'] - x['last_checked_id']
    if not allowed(uid, min(remaining, 1)):
        return await c.answer('🆓 Лимит Trial закончился — нужен Premium.', show_alert=True)
    await c.answer('▶️ Продолжаю…')
    # раньше здесь передавалось c.message, и uid брался у бота, а не у пользователя
    await run_search(c.message, uid, x['model'], x['start_id'], x['end_id'], x['id'])


# ───────────────────────── экспорт TXT ─────────────────────────
@dp.callback_query(F.data.startswith('exp:'))
async def export_txt(c: CallbackQuery):
    sid = int(c.data.split(':')[1])
    x = db.get_search(sid, c.from_user.id)
    if not x:
        return await c.answer('Поиск не найден', show_alert=True)
    gifts = db.get_gifts(sid)
    if not gifts:
        return await c.answer('В этом поиске нет находок', show_alert=True)
    await c.answer('📄 Готовлю файл…')
    lines = [f'GiftParser v{ui.VERSION} — экспорт',
             f'Поиск #{sid} · {x["model"]} · {x["start_id"]}-{x["end_id"]}',
             f'Найдено: {len(gifts)}', '']
    for r in gifts:
        g = ui.row_to_gift(r)
        owner = f'@{r["username"]}' if r.get('username') else (str(r['user_id_owner']) if r.get('user_id_owner') else '—')
        lines.append(' | '.join([
            f'#{r["gift_number"]}', r.get('model') or '—', r.get('pattern') or '—',
            r.get('backdrop') or '—', owner, gift_url(g) or '—']))
    filename = re.sub(r'[^\w.-]+', '_', f'{x["model"]}_{x["start_id"]}-{x["end_id"]}') + '.txt'
    doc = BufferedInputFile('\n'.join(lines).encode('utf-8'), filename=filename)
    await c.message.answer_document(
        doc, caption=f'📄 <b>{ui.esc(ui.pretty(x["model"]))}</b> · {len(gifts)} находок\n'
                     '<i>Формат: № | модель | узор | фон | владелец | ссылка</i>')


# ───────────────────────── каталог моделей ─────────────────────────
async def show_models(target: Message, page: int, edit_mode=False):
    markup, pages = kb.models_kb(page)
    text = ui.models_text(page, pages)
    if edit_mode:
        await edit(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


@dp.message(F.text == kb.BTN_MODELS)
async def models(m: Message):
    await show_models(m, 1)


@dp.callback_query(F.data.startswith('models:'))
async def models_page(c: CallbackQuery):
    await c.answer()
    await show_models(c.message, int(c.data.split(':')[1]), edit_mode=True)


# ───────────────────────── админка ─────────────────────────
def admin_home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Статистика', callback_data='adm:stats')],
        [InlineKeyboardButton(text='👥 Управление пользователями', callback_data='adm:users')],
        [InlineKeyboardButton(text='🛡 Администраторы', callback_data='adm:admins')],
        [InlineKeyboardButton(text='🔎 Найти пользователя', callback_data='adm:userfind')],
        [InlineKeyboardButton(text='🔎 Последние поиски', callback_data='adm:searches')],
        [InlineKeyboardButton(text='📜 Логи', callback_data='adm:logs')],
        [InlineKeyboardButton(text='🔐 Доступ к парсеру', callback_data='adm:parser_access')],
        [InlineKeyboardButton(text='🎁 Доступ к оферам', callback_data='adm:offer_access')],
        [InlineKeyboardButton(text='🔗 Профили передачи', callback_data='adm:offer_profiles')],
        [InlineKeyboardButton(text='🔄 Обновить', callback_data='adm:home')],
    ])


def admin_users_kb(rows):
    buttons = []
    for u in rows:
        uid = u['user_id']
        name = '@' + u['username'] if u.get('username') else str(u.get('first_name') or uid)
        tag = '💎' if u['is_premium'] else ('🚫' if u['is_banned'] else '👤')
        buttons.append([InlineKeyboardButton(text=f'{tag} {name[:24]}', callback_data=f'adm:user:{uid}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Панель', callback_data='adm:home')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(F.text == kb.BTN_ADMIN)
async def admin(m: Message):
    await audit('Открыта админ-панель', m.from_user.id)
    if is_admin(m.from_user.id):
        await m.answer('⚙️ <b>Админ-панель</b>\n\nУправление пользователями, поисками и логами.', reply_markup=admin_home_kb())


def admin_admins_kb():
    buttons = [[InlineKeyboardButton(text='➕ Добавить администратора', callback_data='adm:admins:add')]]
    for row in db.admin_list():
        uid = int(row['user_id'])
        if uid == OWNER_ID:
            continue
        buttons.append([InlineKeyboardButton(text=f'🗑 Удалить ID {uid}', callback_data=f'adm:admins:remove:{uid}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Панель', callback_data='adm:home')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_admins_text():
    rows = db.admin_list()
    lines = ['🛡 <b>Администраторы</b>', '', f'👑 Владелец: <code>{OWNER_ID}</code>']
    extra = [r for r in rows if int(r['user_id']) != int(OWNER_ID)]
    lines.append('')
    lines.append('<b>Дополнительные администраторы:</b>')
    lines.extend([f'• <code>{int(r["user_id"])}</code>' for r in extra] or ['• нет'])
    lines.append('')
    lines.append('Администраторы получают полный доступ к админке, поиску по чатам, пользователям, логам, оферам и профилям.')
    return '\n'.join(lines)

@dp.callback_query(F.data == 'adm:admins')
async def adm_admins(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await state.clear()
    await c.answer()
    await c.message.edit_text(admin_admins_text(), reply_markup=admin_admins_kb())

@dp.callback_query(F.data == 'adm:admins:add')
async def adm_admins_add_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await state.set_state(S.admin_add)
    await c.message.answer('➕ <b>Добавить администратора</b>\n\nОтправь Telegram ID пользователя, например:\n<code>123456789</code>')

@dp.message(S.admin_add)
async def adm_admins_add_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    raw = (m.text or '').strip()
    if not raw.lstrip('-').isdigit() or int(raw) <= 0:
        return await m.answer('❌ Нужен корректный Telegram ID.')
    uid = int(raw)
    db.admin_add(uid)
    await state.clear()
    await audit('Добавлен администратор', m.from_user.id, f'ID: <code>{uid}</code>')
    await m.answer('✅ <b>Администратор добавлен.</b>', reply_markup=admin_admins_kb())

@dp.callback_query(F.data.regexp(r'^adm:admins:remove:-?\d+$'))
async def adm_admins_remove(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    uid = int(c.data.rsplit(':', 1)[1])
    if uid == OWNER_ID:
        return await c.answer('Владельца удалить нельзя.', show_alert=True)
    removed = db.admin_remove(uid)
    await audit('Удалён администратор', c.from_user.id, f'ID: <code>{uid}</code>')
    await c.answer('Администратор удалён' if removed else 'Такого администратора нет')
    await c.message.edit_text(admin_admins_text(), reply_markup=admin_admins_kb())

def stats_text():
    s = db.stats()
    return (f'📈 <b>{ui.small("Stats")} · Статистика</b>\n\n' + ui.block(
        ui.kv('👥', 'Пользователей', ui.num(s['users'])),
        ui.kv('💎', 'Premium', ui.num(s['premium'])),
        ui.kv('🚫', 'Банов', ui.num(s['banned'])),
        ui.kv('🔎', 'Поисков', ui.num(s['searches'])),
        ui.kv('🎁', 'Находок', ui.num(s['found'])),
        ui.kv('⚡', 'Активных поисков', len(ACTIVE))))


@dp.callback_query(F.data == 'adm:parser_access')
async def adm_parser_access(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    enabled = db.parser_admin_only()
    db.set_parser_admin_only(not enabled)
    state = '🔒 Только администратор' if not enabled else '🌐 Доступ разрешён пользователям'
    await audit('Изменён доступ к парсеру', c.from_user.id, f'Режим: <b>{state}</b>')
    await c.answer(state, show_alert=True)
    await c.message.edit_reply_markup(reply_markup=admin_home_kb())

def offer_access_kb():
    mode = db.offer_access_mode()
    rows = db.offer_access_list()
    usernames = db.offer_access_username_list()
    buttons = [
        [InlineKeyboardButton(text=('👑 Только владелец' if mode == 'owner' else '👑 Только владелец'), callback_data='adm:offer_access:mode:owner')],
        [InlineKeyboardButton(text=('🌐 Все пользователи' if mode != 'all' else '🌐 Все пользователи ✅'), callback_data='adm:offer_access:mode:all')],
        [InlineKeyboardButton(text='➕ Исключение по Telegram ID', callback_data='adm:offer_access:add')],
        [InlineKeyboardButton(text='➕ Исключение по @username', callback_data='adm:offer_access:add_username')],
        [InlineKeyboardButton(text='👤 Подключённые Business-профили', callback_data='adm:offer_business_connections')],
    ]
    for row in rows:
        uid = int(row['user_id'])
        if uid == OWNER_ID:
            continue
        buttons.append([InlineKeyboardButton(text=f'🗑 ID {uid}', callback_data=f'adm:offer_access:remove:{uid}')])
    for row in usernames:
        username = str(row['username'])
        buttons.append([InlineKeyboardButton(text=f'🗑 @{username}', callback_data=f'adm:offer_access:remove_username:{username}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Панель', callback_data='adm:home')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def offer_access_text():
    mode = db.offer_access_mode()
    mode_text = '👑 Только владелец + исключения' if mode == 'owner' else '🌐 Все пользователи'
    rows = db.offer_access_list()
    usernames = db.offer_access_username_list()
    lines = [f'Режим: <b>{mode_text}</b>', '']
    if mode == 'owner':
        lines.append('Владелец может использовать <code>.офер</code> всегда. Остальным доступ даётся через исключение по ID или @username.')
    else:
        lines.append('Любой пользователь может использовать <code>.офер</code>. Список исключений при этом сохраняется для режима владельца.')
    lines.append('')
    exceptions = []
    for row in rows:
        uid = int(row['user_id'])
        if uid != OWNER_ID:
            exceptions.append(f'• ID <code>{uid}</code>')
    for row in usernames:
        exceptions.append(f'• @{ui.esc(str(row["username"]))}')
    lines.append('<b>Исключения:</b>')
    lines.extend(exceptions or ['• нет'])
    return '🎁 <b>Доступ к оферам</b>\n\n' + '\n'.join(lines)


def offer_user_allowed(user_id, username=None):
    return db.offer_access_allowed(user_id, username=username, owner_id=OWNER_ID)


@dp.callback_query(F.data == 'adm:offer_access')
async def adm_offer_access(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await state.clear()
    await c.answer()
    await c.message.edit_text(offer_access_text(), reply_markup=offer_access_kb())


@dp.callback_query(F.data.regexp(r'^adm:offer_access:mode:(owner|all)$'))
async def adm_offer_access_mode(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    mode = c.data.rsplit(':', 1)[1]
    db.set_offer_access_mode(mode)
    text = '👑 Режим «Только владелец + исключения» включён.' if mode == 'owner' else '🌐 Режим «Все пользователи» включён.'
    await c.answer(text, show_alert=True)
    await c.message.edit_text(offer_access_text(), reply_markup=offer_access_kb())


@dp.callback_query(F.data == 'adm:offer_access:add')
async def adm_offer_access_add_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await state.set_state(S.offer_access_add)
    await c.message.answer('➕ <b>Исключение по Telegram ID</b>\n\nОтправь Telegram ID пользователя, например:\n<code>123456789</code>', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data='adm:offer_access')]]))


@dp.message(S.offer_access_add)
async def adm_offer_access_add_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    raw = (m.text or '').strip()
    if not raw.lstrip('-').isdigit():
        return await m.answer('❌ Нужен именно Telegram ID, например <code>123456789</code>.')
    uid = int(raw)
    if uid <= 0:
        return await m.answer('❌ Некорректный Telegram ID.')
    db.offer_access_add(uid)
    await state.clear()
    await audit('Добавлено исключение к оферам', m.from_user.id, f'ID: <code>{uid}</code>')
    await m.answer('✅ <b>Исключение по ID добавлено.</b>\n\n' + offer_access_text(), reply_markup=offer_access_kb())


@dp.callback_query(F.data == 'adm:offer_access:add_username')
async def adm_offer_access_username_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await state.set_state(S.offer_access_username_add)
    await c.message.answer('➕ <b>Исключение по @username</b>\n\nОтправь username, например:\n<code>@username</code>', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data='adm:offer_access')]]))


@dp.message(S.offer_access_username_add)
async def adm_offer_access_username_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    username = (m.text or '').strip().lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        return await m.answer('❌ Некорректный username. Пример: <code>@username</code>.')
    db.offer_access_username_add(username)
    await state.clear()
    await audit('Добавлено исключение к оферам', m.from_user.id, f'Username: <code>@{ui.esc(username)}</code>')
    await m.answer('✅ <b>Исключение по @username добавлено.</b>\n\n' + offer_access_text(), reply_markup=offer_access_kb())


@dp.callback_query(F.data.regexp(r'^adm:offer_access:remove:-?\d+$'))
async def adm_offer_access_remove(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    uid = int(c.data.rsplit(':', 1)[1])
    if uid == OWNER_ID:
        return await c.answer('Владельца удалить нельзя.', show_alert=True)
    removed = db.offer_access_remove(uid)
    await audit('Удалено исключение к оферам', c.from_user.id, f'ID: <code>{uid}</code>')
    await c.answer('Исключение удалено' if removed else 'Такого ID нет')
    await c.message.edit_text(offer_access_text(), reply_markup=offer_access_kb())


@dp.callback_query(F.data.regexp(r'^adm:offer_access:remove_username:.+$'))
async def adm_offer_access_username_remove(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    username = c.data.split(':', 3)[3]
    removed = db.offer_access_username_remove(username)
    await audit('Удалено исключение к оферам', c.from_user.id, f'Username: <code>@{ui.esc(username)}</code>')
    await c.answer('Исключение удалено' if removed else 'Такого username нет')
    await c.message.edit_text(offer_access_text(), reply_markup=offer_access_kb())

def offer_profiles_text():
    rows = db.offer_profile_list()
    if not rows:
        return (
            '🔗 <b>Профили передачи</b>\n\n'
            'Профилей пока нет. Добавь профиль — он появится здесь и его можно будет выбрать для следующих оферов.'
        )
    lines = []
    for row in rows:
        mark = '✅' if row['is_active'] else '▫️'
        lines.append(f"{mark} <b>{ui.esc(row['name'])}</b>\n<code>{ui.esc(row['url'])}</code>")
    active = db.offer_profile_active()
    active_line = f"\n\nТекущий профиль: <b>{ui.esc(active['name'])}</b>" if active else '\n\nТекущий профиль: <b>не выбран</b>'
    return '🔗 <b>Профили передачи</b>\n\n' + '\n\n'.join(lines) + active_line


def offer_profiles_kb():
    rows = db.offer_profile_list()
    buttons = []
    for row in rows:
        rid = int(row['id'])
        mark = '✅' if row['is_active'] else '▫️'
        buttons.append([InlineKeyboardButton(text=f"{mark} {str(row['name'])[:28]}", callback_data=f'adm:offer_profiles:select:{rid}')])
        buttons.append([InlineKeyboardButton(text='🗑 Удалить', callback_data=f'adm:offer_profiles:remove:{rid}')])
    buttons.append([InlineKeyboardButton(text='➕ Добавить профиль', callback_data='adm:offer_profiles:add')])
    buttons.append([InlineKeyboardButton(text='⬅️ Панель', callback_data='adm:home')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def offer_business_connections_text():
    rows = db.business_connections_list()
    if not rows:
        return '👤 <b>Подключённые Business-профили</b>\n\nПока нет сохранённых подключений.'
    lines = ['👤 <b>Подключённые Business-профили</b>', '']
    for row in rows:
        name = ' '.join(x for x in (row.get('first_name') or '', row.get('last_name') or '') if x).strip() or 'Без имени'
        username = f" @{row['username']}" if row.get('username') else ''
        status = '🟢 подключён' if row.get('is_enabled') else '🔴 отключён'
        lines.append(f"• <b>{ui.esc(name)}</b>{ui.esc(username)} — <code>{row['user_id']}</code> — {status}")
    return '\n'.join(lines)


@dp.callback_query(F.data == 'adm:offer_business_connections')
async def adm_offer_business_connections(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await c.message.edit_text(offer_business_connections_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='adm:offer_access')]]))


@dp.callback_query(F.data == 'adm:offer_profiles')
async def adm_offer_profiles(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await state.clear()
    await c.answer()
    await c.message.edit_text(offer_profiles_text(), reply_markup=offer_profiles_kb())


@dp.callback_query(F.data == 'adm:offer_profiles:add')
async def adm_offer_profiles_add_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await state.set_state(S.offer_profile_add_name)
    await c.message.answer(
        '➕ <b>Добавление профиля передачи</b>\n\n'
        'Отправь название профиля, например:\n<code>Основной</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data='adm:offer_profiles')]])
    )


@dp.message(S.offer_profile_add_name)
async def adm_offer_profiles_add_name(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    name = (m.text or '').strip()
    if not name or len(name) > 64:
        return await m.answer('❌ Название должно быть от 1 до 64 символов.')
    await state.update_data(offer_profile_name=name)
    await state.set_state(S.offer_profile_add_url)
    await m.answer(
        '🔗 Теперь отправь ссылку на профиль, куда передавать подарок.\n\n'
        'Например: <code>https://t.me/username</code> или <code>tg://user?id=123456789</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data='adm:offer_profiles')]])
    )


@dp.message(S.offer_profile_add_url)
async def adm_offer_profiles_add_url(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    url = (m.text or '').strip()
    if not re.match(r'^(?:https?://t\.me/[^\s]+|tg://user\?id=-?\d+)$', url, re.I):
        return await m.answer('❌ Нужна ссылка Telegram-профиля: <code>https://t.me/...</code> или <code>tg://user?id=...</code>.')
    data = await state.get_data()
    name = data.get('offer_profile_name') or 'Профиль'
    profile_id = db.offer_profile_add(name, url)
    await state.clear()
    await audit('Добавлен профиль передачи', m.from_user.id, f'Профиль: <b>{ui.esc(name)}</b> · <code>{ui.esc(url)}</code>')
    await m.answer('✅ <b>Профиль добавлен.</b>\n\nВыбери его кнопкой ниже — новые оферы сразу будут вести на этот профиль.', reply_markup=offer_profiles_kb())


@dp.callback_query(F.data.regexp(r'^adm:offer_profiles:select:\d+$'))
async def adm_offer_profiles_select(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    rid = int(c.data.rsplit(':', 1)[1])
    if not db.offer_profile_set_active(rid):
        return await c.answer('Профиль не найден', show_alert=True)
    profile = db.offer_profile_get(rid)
    await audit('Выбран профиль передачи', c.from_user.id, f"Профиль: <b>{ui.esc(profile['name'])}</b> · <code>{ui.esc(profile['url'])}</code>")
    await c.answer('Профиль выбран. Следующие оферы сразу используют его.', show_alert=True)
    await c.message.edit_text(offer_profiles_text(), reply_markup=offer_profiles_kb())


@dp.callback_query(F.data.regexp(r'^adm:offer_profiles:remove:\d+$'))
async def adm_offer_profiles_remove(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    rid = int(c.data.rsplit(':', 1)[1])
    profile = db.offer_profile_get(rid)
    if not profile:
        return await c.answer('Профиль уже удалён.', show_alert=True)
    removed = db.offer_profile_remove(rid)
    await audit('Удалён профиль передачи', c.from_user.id, f"Профиль: <b>{ui.esc(profile['name'])}</b> · <code>{ui.esc(profile['url'])}</code>")
    await c.answer('Профиль удалён' if removed else 'Профиль не найден')
    await c.message.edit_text(offer_profiles_text(), reply_markup=offer_profiles_kb())


@dp.callback_query(F.data == 'adm:stats')
async def adm_stats(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await c.message.answer(stats_text())


@dp.callback_query(F.data == 'adm:userfind')
async def adm_userfind_start(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await state.set_state(S.admin_user_search)
    await c.message.answer('🔎 <b>Поиск пользователя</b>\n\nВведите Telegram ID или username, например <code>123456789</code> или <code>@kod30</code>.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Отмена', callback_data='adm:home')]]))


@dp.message(S.admin_user_search)
async def adm_userfind_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    rows = db.search_users(m.text or '', 10)
    await state.clear()
    await audit('Поиск пользователя администратором', m.from_user.id, f'Запрос: <code>{ui.esc(m.text or "")}</code> · найдено: <b>{len(rows)}</b>')
    if not rows:
        return await m.answer('❌ Пользователь не найден.', reply_markup=admin_home_kb())
    lines = []
    for u in rows:
        name = '@' + u['username'] if u.get('username') else (u.get('first_name') or '—')
        tag = '💎' if u['is_premium'] else ('🚫' if u['is_banned'] else '👤')
        lines.append(f'{tag} <code>{u["user_id"]}</code> · {ui.esc(name)}')
    await m.answer('🔎 <b>Результаты поиска</b>\n\n' + ui.block(*lines), reply_markup=admin_users_kb(rows))


@dp.callback_query(F.data == 'adm:users')
async def adm_users(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    rows = db.recent_users(10)
    lines = []
    for u in rows:
        tag = '💎' if u['is_premium'] else ('🚫' if u['is_banned'] else '•')
        name = f'@{u["username"]}' if u['username'] else (u['first_name'] or '—')
        lines.append(f'{tag} <code>{u["user_id"]}</code> · {ui.esc(name)}')
    text = '👥 <b>Пользователи</b>\n\n' + (ui.block(*lines) if lines else 'Пользователей пока нет.') + '\n\n<i>Управление — кнопками ниже.</i>'
    await c.message.answer(text, reply_markup=admin_users_kb(rows))



@dp.callback_query(F.data == 'adm:searches')
async def adm_searches(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    rows = db.recent_searches(12)
    lines = []
    for x in rows:
        lines.append(f"{'✅' if x['status']=='completed' else '⏹' if x['status']=='stopped' else '🔎' if x['status']=='in_progress' else '⚠️'} <b>#{x['id']}</b> · <code>{x['user_id']}</code> · {ui.esc(ui.pretty(x['model']))} · <code>{x['last_checked_id']}</code>")
    text = '🔎 <b>Последние поиски</b>\n\n' + (ui.block(*lines) if lines else 'Поисков пока нет.')
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Панель', callback_data='adm:home')]]))


@dp.callback_query(F.data == 'adm:logs')
async def adm_logs(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    path = 'logs/giftparser.log'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-25:]
    except FileNotFoundError:
        lines = []
    body = ''.join(lines).strip() or 'Лог пока пуст.'
    if len(body) > 3500:
        body = body[-3500:]
    text = '📜 <b>Последние логи</b>\n\n<code>' + ui.esc(body) + '</code>'
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 Обновить', callback_data='adm:logs')],
        [InlineKeyboardButton(text='⬅️ Панель', callback_data='adm:home')]
    ]))


@dp.callback_query(F.data == 'adm:home')
async def adm_home(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await c.message.answer('⚙️ <b>Админ-панель</b>', reply_markup=admin_home_kb())


@dp.callback_query(F.data.startswith('adm:user:'))
async def adm_user(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    uid = int(c.data.split(':')[2])
    u = db.get_user(uid)
    if not u:
        return await c.answer('Пользователь не найден', show_alert=True)
    await c.answer()
    text = (f'👤 <b>Пользователь</b>\n\n'
            f'🆔 <code>{uid}</code>\n'
            f'👤 {ui.esc("@" + u["username"] if u.get("username") else u.get("first_name") or "—")}\n'
            f'💎 Premium: <b>{"да" if u["is_premium"] else "нет"}</b>\n'
            f'🚫 Бан: <b>{"да" if u["is_banned"] else "нет"}</b>\n'
            f'🔎 Поисков: <b>{u.get("total_searches", 0)}</b>\n'
            f'🎁 Находок: <b>{u.get("total_found", 0)}</b>')
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➖ Снять Premium' if u['is_premium'] else '💎 Выдать Premium', callback_data=f'adm:premium:{uid}')],
        [InlineKeyboardButton(text='✅ Разбанить' if u['is_banned'] else '🚫 Заблокировать', callback_data=f'adm:ban:{uid}')],
        [InlineKeyboardButton(text='🔎 История поисков', callback_data=f'adm:usersearch:{uid}')],
        [InlineKeyboardButton(text='⬅️ Пользователи', callback_data='adm:users')],
    ])
    await c.message.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith('adm:usersearch:'))
async def adm_usersearch(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    uid = int(c.data.split(':')[2])
    rows = db.user_searches(uid, 10) if hasattr(db, 'user_searches') else []
    await c.answer()
    lines = [f"#{x['id']} · {ui.esc(ui.pretty(x['model']))} · <code>{x['start_id']}-{x['end_id']}</code> · {x['status']}" for x in rows]
    text = f'🔎 <b>Поиски пользователя {uid}</b>\n\n' + (ui.block(*lines) if lines else 'Поисков нет.')
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Пользователь', callback_data=f'adm:user:{uid}')]]))


@dp.callback_query(F.data.startswith('adm:premium:'))
async def adm_premium(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    uid = int(c.data.split(':')[2])
    u = db.get_user(uid)
    if not u:
        return await c.answer('Пользователь не найден', show_alert=True)
    new_value = not bool(u['is_premium'])
    db.set_premium(uid, new_value)
    await audit('Изменён Premium', c.from_user.id, f'Пользователь: <code>{uid}</code> · значение: <b>{"включён" if new_value else "снят"}</b>')
    await c.answer('Premium включён' if new_value else 'Premium снят')
    await c.message.edit_reply_markup(reply_markup=admin_users_kb(db.recent_users(10)))


@dp.callback_query(F.data.startswith('adm:ban:'))
async def adm_ban(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    uid = int(c.data.split(':')[2])
    u = db.get_user(uid)
    if not u:
        return await c.answer('Пользователь не найден', show_alert=True)
    new_value = not bool(u['is_banned'])
    db.set_ban(uid, new_value)
    await audit('Изменён бан', c.from_user.id, f'Пользователь: <code>{uid}</code> · значение: <b>{"заблокирован" if new_value else "разблокирован"}</b>')
    await c.answer('Пользователь заблокирован' if new_value else 'Пользователь разблокирован')
    await c.message.edit_reply_markup(reply_markup=admin_users_kb(db.recent_users(10)))

def _admin_target(m: Message):
    parts = (m.text or '').split(maxsplit=1)
    return int(parts[1]) if len(parts) == 2 and parts[1].strip().lstrip('-').isdigit() else None


async def _admin_cmd(m: Message, name: str, action, done_text: str):
    if not is_admin(m.from_user.id):
        return
    uid = _admin_target(m)
    if uid is None:
        return await m.answer(f'Использование: <code>/{name} 123456789</code>')
    if action(uid) is False:
        return await m.answer('❌ Пользователь не найден в базе.')
    await m.answer(done_text.format(uid=uid))


@dp.message(Command('ban'))
async def ban(m: Message):
    await _admin_cmd(m, 'ban', lambda u: db.set_ban(u, True), '🚫 Пользователь <code>{uid}</code> заблокирован.')


@dp.message(Command('unban'))
async def unban(m: Message):
    await _admin_cmd(m, 'unban', lambda u: db.set_ban(u, False), '✅ Пользователь <code>{uid}</code> разблокирован.')


@dp.message(Command('grant'))
async def grant(m: Message):
    await _admin_cmd(m, 'grant', lambda u: db.set_premium(u, True), '💎 Premium выдан: <code>{uid}</code>.')


@dp.message(Command('revoke'))
async def revoke(m: Message):
    await _admin_cmd(m, 'revoke', lambda u: db.set_premium(u, False), '➖ Premium снят: <code>{uid}</code>.')



# ───────────────────────── DEMO gift offers ─────────────────────────
def _offer_custom_emoji_id():
    return db.get_setting('offer_custom_emoji_id') or os.getenv('OFFER_CUSTOM_EMOJI_ID', '').strip() or None


def _offer_target_url(user):
    if user.username:
        return f'https://t.me/{user.username}'
    return f'tg://user?id={user.id}'


def _offer_sender_name(user):
    return user.username or user.first_name or str(user.id)


@dp.message(F.text.regexp(r'^\s*\.эмодзи\b'))
@dp.business_message(F.text.regexp(r'^\s*\.эмодзи\b'))
async def offer_emoji_helper(m: Message):
    if not is_admin(m.from_user.id):
        return
    entities = m.entities or []
    found = [e for e in entities if e.type == 'custom_emoji' and e.custom_emoji_id]
    if not found:
        return await m.answer(
            '🧩 <b>Как получить Premium Emoji ID</b>\n\n'
            'Отправьте мне сообщение, содержащее нужный Telegram Premium emoji, '
            'с командой <code>.эмодзи</code> перед ним.\n\n'
            'Пример: <code>.эмодзи 🎁</code> (где 🎁 — именно custom emoji, а не обычный emoji).'
        )
    entity = found[0]
    emoji_id = str(entity.custom_emoji_id)
    # MessageEntity offsets are UTF-16 code units, not Python code points.
    raw = m.text or ''
    def _utf16_slice(value, start, length):
        data = value.encode('utf-16-le')
        part = data[start * 2:(start + length) * 2]
        return part.decode('utf-16-le', errors='ignore') or '🎁'
    alt = _utf16_slice(raw, entity.offset, entity.length)
    db.set_setting('offer_custom_emoji_id', emoji_id)
    db.set_setting('offer_custom_emoji_alt', alt)
    await m.answer(
        '✅ <b>Custom Emoji сохранён.</b>\n\n'
        f'ID: <code>{emoji_id}</code>\n'
        f'Альтернатива: {ui.esc(alt)}\n\n'
        'Теперь он будет использоваться в DEMO-оферах.'
    )


@dp.business_connection()
async def offer_business_connection_update(c: BusinessConnection):
    try:
        owner = getattr(c, 'user', None)
        owner_id = int(getattr(owner, 'id', 0) or 0)
        connection_id = str(getattr(c, 'id', '') or '')
        if not connection_id or not owner_id:
            return
        db.save_business_connection(
            connection_id, owner_id,
            getattr(owner, 'username', None),
            getattr(owner, 'first_name', None),
            getattr(owner, 'last_name', None),
            bool(getattr(c, 'is_enabled', True)),
        )
        if getattr(c, 'is_enabled', True):
            BUSINESS_OWNER_ID_CACHE[connection_id] = owner_id
            name = ' '.join(x for x in ((getattr(owner, 'first_name', None) or '').strip(), (getattr(owner, 'last_name', None) or '').strip()) if x)
            if name:
                BUSINESS_SELLER_NAME_CACHE[connection_id] = name
            log.info('Business connection saved: owner=%s connection=%s', owner_id, connection_id)
        else:
            BUSINESS_OWNER_ID_CACHE.pop(connection_id, None)
            BUSINESS_SELLER_NAME_CACHE.pop(connection_id, None)
            log.info('Business connection disabled: owner=%s connection=%s', owner_id, connection_id)
    except Exception:
        log.exception('Failed to save Business connection')


@dp.message(F.text.regexp(r'^\s*\.офер(?:\s|$)'))
@dp.business_message(F.text.regexp(r'^\s*\.офер(?:\s|$)'))
async def demo_offer_command(m: Message):
    parsed = parse_offer_command(m.text or '')
    if not parsed:
        return

    sender_id = int(m.from_user.id) if m.from_user else 0
    sender_username = getattr(m.from_user, 'username', None) if m.from_user else None
    business_connection_id = getattr(m, 'business_connection_id', None)

    # A Business message already carries the exact connection that owns the chat.
    # Never replace it with OWNER_ID: every connected Business account must send
    # its own offers.
    seller_id = sender_id
    seller_display_name = None
    if business_connection_id:
        bc_row = db.get_business_connection(business_connection_id)
        if not bc_row:
            # The connection update can be missed after a restart; fetch the
            # connection through Bot API and cache it from the current message.
            try:
                bc = await safe(bot.get_business_connection, business_connection_id)
                owner = getattr(bc, 'user', None) if bc else None
                owner_id = int(getattr(owner, 'id', 0) or 0)
                if owner_id:
                    db.save_business_connection(
                        business_connection_id, owner_id,
                        getattr(owner, 'username', None), getattr(owner, 'first_name', None), getattr(owner, 'last_name', None),
                        bool(getattr(bc, 'is_enabled', True)),
                    )
                    bc_row = db.get_business_connection(business_connection_id)
            except Exception:
                log.exception('Could not refresh Business connection %s', business_connection_id)
        business_owner_id = int((bc_row or {}).get('user_id') or 0)
        if not business_owner_id or not bool((bc_row or {}).get('is_enabled', 1)):
            log.warning('Ignoring .офер: Business connection unavailable: %s', business_connection_id)
            return
        # The sender of a business message is not necessarily the business owner.
        # The offer must belong to the connected business account, while access
        # can be granted to the sender separately.
        if not offer_user_allowed(sender_id, sender_username) and not offer_user_allowed(business_owner_id, (bc_row or {}).get('username')):
            log.info('Ignoring unauthorized Business .офер sender=%s owner=%s', sender_id, business_owner_id)
            return
        seller_id = business_owner_id
        seller_display_name = await _business_seller_display_name(business_connection_id)
    else:
        if not offer_user_allowed(sender_id, sender_username):
            log.info('Ignoring .офер from unauthorized user %s', sender_id)
            return
        # For a normal bot PM, use the sender's own saved Business connection.
        # This avoids ever falling back to OWNER_ID for another authorized user.
        saved_bc = db.get_business_connection_for_user(sender_id)
        if saved_bc:
            business_connection_id = saved_bc['connection_id']
            seller_id = sender_id
            seller_display_name = await _business_seller_display_name(business_connection_id)
        else:
            log.warning('Authorized user %s has no active Business connection; .офер is not sent as OWNER', sender_id)
            return

    if not seller_display_name:
        seller_display_name = os.getenv('OFFER_SELLER_NAME', '').strip() or 'Пользователь'

    # Invalid syntax: do not edit the user's message. Editing is the operation
    # that caused the long Telegram flood waits. Just leave the original text.
    if parsed.get('error'):
        return

    buyer = m.chat
    if not buyer or not buyer.id:
        return
    buyer_id = int(buyer.id)
    buyer_username = getattr(buyer, 'username', None)
    buyer_url = f'https://t.me/{buyer_username}' if buyer_username else f'tg://user?id={buyer_id}'
    # Offer lifetime is intentionally unlimited in this DEMO.
    # The text shows a static "6ч", but there is no countdown and no auto-expiry.
    expires_at = None

    try:
        active_profile = db.offer_profile_active()
        transfer_profile_url = active_profile['url'] if active_profile else buyer_url
        offer_id = db.create_offer(
            seller_id=seller_id, seller_username=seller_display_name,
            buyer_id=buyer_id, buyer_username=buyer_username, buyer_url=buyer_url,
            gift_name=parsed['gift_name'], gift_number=parsed['gift_number'], gift_url=parsed['gift_url'],
            amount=parsed['amount'], currency=parsed['currency'], locale=parsed['locale'], expires_at=expires_at,
            transfer_profile_url=transfer_profile_url,
        )
        offer = db.get_offer(offer_id)
        text, _ = build_offer_text(offer, parsed['locale'])
        markup = offer_keyboard(offer_id, parsed['locale'])

        # IMPORTANT: send first, then delete the command. We completely avoid
        # editMessageText for offers, because this chat was being rate-limited on
        # that method and Telegram showed the outgoing command as a spinning clock.
        sent = await _send_offer_message(m, text, markup, business_connection_id=business_connection_id)
        sent_chat_id = int(getattr(getattr(sent, 'chat', None), 'id', m.chat.id))
        sent_message_id = int(getattr(sent, 'message_id', 0) or 0)
        db.update_offer_message(offer_id, sent_chat_id, sent_message_id, business_connection_id)
        try:
            await _delete_offer_command_message(m, business_connection_id=business_connection_id)
        except Exception as delete_error:
            log.warning('Could not delete offer command %s after sending offer %s: %s', getattr(m, 'message_id', '?'), offer_id, delete_error)
        log.info('Created DEMO offer %s by sending new message; source command=%s', offer_id, getattr(m, 'message_id', '?'))
    except TelegramRetryAfter as e:
        if 'offer_id' in locals():
            db.set_offer_status(offer_id, STATUS_EXPIRED)
        log.warning('Offer %s was not sent: Telegram flood control retry_after=%s', locals().get('offer_id'), getattr(e, 'retry_after', '?'))
    except Exception:
        if 'offer_id' in locals():
            db.set_offer_status(offer_id, STATUS_EXPIRED)
        log.exception('Failed to create/send demo offer')


@dp.callback_query(F.data.regexp(r'^of:(accept|decline):\d+$'))
async def demo_offer_action(c: CallbackQuery):
    try:
        _, action, raw_id = c.data.split(':', 2)
        offer_id = int(raw_id)
    except Exception:
        return await c.answer('Некорректный офер', show_alert=True)

    lock = _offer_lock(offer_id)
    async with lock:
        offer = db.get_offer(offer_id)
        if not offer:
            return await c.answer('Офер не найден', show_alert=True)
        if offer['status'] != STATUS_PENDING:
            return await c.answer('Офер уже обработан.', show_alert=True)
        if not c.message:
            return await c.answer('Сообщение офера недоступно.', show_alert=True)

        locale = offer['locale'] or 'ru'
        old_message = c.message
        bc_id = offer.get('business_connection_id') or getattr(old_message, 'business_connection_id', None)

        if action == 'accept':
            new_text = final_offer_text(offer, locale)
            new_markup = transfer_keyboard(offer, locale)
            alert = accepted_alert_text(offer, locale)
            new_status = STATUS_ACCEPTED
        else:
            new_text = declined_text(offer, locale)
            new_markup = None
            alert = None
            new_status = STATUS_DECLINED

        # Close the callback immediately. The state change below is done by
        # sending a NEW message, never by editMessageText. This prevents the
        # Telegram client from showing the "edited/изменено" marker.
        try:
            await c.answer(alert, show_alert=True) if alert else await c.answer()
        except Exception:
            pass

        try:
            # Send the final state as a fresh message first, so the offer is
            # never lost if Telegram rejects the subsequent deletion.
            sent = await _send_offer_message(
                old_message,
                new_text,
                new_markup,
                business_connection_id=bc_id,
            )
            sent_chat_id = int(getattr(getattr(sent, 'chat', None), 'id', old_message.chat.id))
            sent_message_id = int(getattr(sent, 'message_id', 0) or 0)

            # Only after the replacement exists, remove the old interactive
            # offer. The resulting message is therefore completely new and
            # has no Telegram "изменено" label.
            try:
                if bc_id:
                    await safe(
                        bot.delete_business_messages,
                        business_connection_id=bc_id,
                        message_ids=[old_message.message_id],
                    )
                else:
                    await safe(
                        bot.delete_message,
                        chat_id=old_message.chat.id,
                        message_id=old_message.message_id,
                    )
            except Exception as delete_error:
                # Keeping the old message is preferable to losing the final
                # state. Its buttons will still be guarded by DB status.
                log.warning(
                    'Could not delete old offer %s after replacement: %s',
                    offer_id,
                    delete_error,
                )

            db.update_offer_message(offer_id, sent_chat_id, sent_message_id, bc_id)
            db.set_offer_status(offer_id, new_status)
            log.info(
                'Offer %s moved to %s using a new message (no edit)',
                offer_id,
                new_status,
            )
        except TelegramRetryAfter as e:
            log.warning(
                'Could not create replacement for offer %s: flood retry_after=%s',
                offer_id,
                getattr(e, 'retry_after', '?'),
            )
        except Exception:
            log.exception('Could not replace offer %s after %s', offer_id, action)


def _parse_expiry(value):
    if not value:
        return None
    try:
        s = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None



# No offer expiry worker: offers are intentionally persistent in the DEMO.


# ───────────────────────── запуск ─────────────────────────
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    # Восстанавливаем сохранённую MTProto StringSession без повторного запроса кода.
    try:
        restored = await parser_handlers.restore_saved_parser_session(db)
        log.info('Saved parser session restore: %s', restored)
        if restored:
            resumed = await parser_handlers.resume_interrupted_scan(db)
            log.info('Interrupted parser scan resume: %s', resumed)
    except Exception:
        log.exception('Failed to restore saved parser session')

    log.info('Log channel configured: %s (startup audit disabled to avoid unnecessary SendMessage)', LOG_CHANNEL_ID)
    await bot.set_my_commands([
        BotCommand(command='start', description='Главное меню'),
        BotCommand(command='stop', description='Остановить текущий поиск'),
        BotCommand(command='help', description='Как пользоваться'),
    ])
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await api.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
