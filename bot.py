import asyncio
import logging
import os
import random
import re
import time

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message
from dotenv import load_dotenv

import keyboards as kb
import ui
from database import Database
from peek_api import AVAILABLE_MODELS, PeekAPI, gift_url, is_known_model, normalize_model_name, suggest_models
from parsing import handlers as parser_handlers
from offer_simulator import (parse_offer_command, offer_help_text, keyboard as offer_keyboard, build_offer_text, accepted_text, declined_text, transfer_keyboard, STATUS_PENDING, STATUS_ACCEPTED, STATUS_DECLINED, STATUS_EXPIRED)

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
api = PeekAPI(CONCURRENCY)
ACTIVE: set[int] = set()  # пользователи с запущенным поиском

# Подключаем router парсера
dp.include_router(parser_handlers.router)

# Middleware для передачи db в handlers
@dp.message.middleware()
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


# ───────────────────────── помощники ─────────────────────────
def is_admin(uid):
    return uid == OWNER_ID


def is_unlimited(uid):
    return is_admin(uid) or bool((db.get_user(uid) or {}).get('is_premium'))


def user_menu(uid):
    return kb.main_menu(is_admin(uid), bool((db.get_user(uid) or {}).get('is_premium')), (not db.parser_admin_only()) or is_admin(uid))


def trial_left(uid):
    return int((db.get_user(uid) or {}).get('trial_searches_left', ui.TRIAL_DEFAULT))


def allowed(uid, count):
    return is_unlimited(uid) or count <= trial_left(uid)


async def safe(fn, *args, **kwargs):
    """Вызов Telegram API с обработкой flood-ограничения."""
    for _ in range(4):
        try:
            return await fn(*args, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
    return await fn(*args, **kwargs)


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
        if event.text and not event.text.startswith('/'):
            await audit('Сообщение пользователя', uid, f'Текст: <code>{ui.esc(event.text[:700])}</code>')
    return await handler(event, data)


@dp.callback_query.middleware()
async def access_callback(handler, event: CallbackQuery, data):
    if db.is_banned(event.from_user.id):
        await audit('Заблокированный пользователь нажал кнопку', event.from_user.id, f'Callback: <code>{ui.esc(event.data or "")}</code>')
        await event.answer('🚫 Доступ ограничен', show_alert=True)
        return
    await audit('Нажата кнопка', event.from_user.id, f'Callback: <code>{ui.esc(event.data or "")}</code>')
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
        [InlineKeyboardButton(text='🔎 Найти пользователя', callback_data='adm:userfind')],
        [InlineKeyboardButton(text='🔎 Последние поиски', callback_data='adm:searches')],
        [InlineKeyboardButton(text='📜 Логи', callback_data='adm:logs')],
        [InlineKeyboardButton(text='🔐 Доступ к парсеру', callback_data='adm:parser_access')],
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


@dp.message(F.text.regexp(r'^\s*\.офер(?:\s|$)'))
async def demo_offer_command(m: Message):
    parsed = parse_offer_command(m.text or '')
    if not parsed:
        return

    # In a Telegram Business chat, from_user is the customer. Verify that the
    # Business Connection belongs to OWNER_ID before allowing offer creation.
    seller_username = os.getenv('OFFER_SELLER_USERNAME', '').strip().lstrip('@') or None
    seller_id = OWNER_ID
    if getattr(m, 'business_connection_id', None):
        try:
            bc = await safe(bot.get_business_connection, m.business_connection_id)
            if int(bc.user.id) != OWNER_ID:
                return await m.answer('❌ Эта Business-связь не принадлежит владельцу бота.')
            seller_username = seller_username or bc.user.username
        except Exception as e:
            log.exception('Business connection lookup failed')
            return await m.answer(f'❌ Не удалось проверить Business-связь: <code>{ui.esc(str(e)[:220])}</code>')
    elif not is_admin(m.from_user.id):
        return await m.answer('❌ Для DEMO-оферов используй подключённый Telegram Business-чат владельца.')

    if parsed.get('error'):
        return await m.answer(offer_help_text(parsed.get('locale', 'ru')))

    # In a Business chat, the chat itself is the customer peer. This also works
    # when the command is sent by the business owner as an outgoing message: from_user
    # may then be the owner, while m.chat identifies the buyer.
    buyer = m.chat
    if not buyer or not buyer.id:
        return await m.answer('❌ Не удалось определить получателя офера.')
    buyer_id = int(buyer.id)
    buyer_username = getattr(buyer, 'username', None)
    buyer_url = f'https://t.me/{buyer_username}' if buyer_username else f'tg://user?id={buyer_id}'
    try:
        offer_id = db.create_offer(
            seller_id=seller_id,
            seller_username=seller_username,
            buyer_id=buyer_id,
            buyer_username=buyer_username,
            buyer_url=buyer_url,
            gift_name=parsed['gift_name'],
            gift_number=parsed['gift_number'],
            gift_url=parsed['gift_url'],
            amount=parsed['amount'],
            currency=parsed['currency'],
            locale=parsed['locale'],
        )
        offer = db.get_offer(offer_id)
        text, entities = build_offer_text(offer, parsed['locale'], _offer_custom_emoji_id(), db.get_setting('offer_custom_emoji_alt') or os.getenv('OFFER_CUSTOM_EMOJI_ALT', '🎁'))
        lp = LinkPreviewOptions(url=parsed['gift_url'], show_above_text=True, prefer_large_media=True)
        kwargs = dict(chat_id=m.chat.id, text=text, reply_markup=offer_keyboard(offer_id, parsed['locale']),
                      link_preview_options=lp, entities=entities)
        if getattr(m, 'business_connection_id', None):
            kwargs['business_connection_id'] = m.business_connection_id
        sent = await safe(bot.send_message, **kwargs)
        db.update_offer_message(offer_id, sent.chat.id, sent.message_id)
        await audit('Создан DEMO-офер', seller_id,
                    f'Offer: <code>{offer_id}</code> · Buyer: <code>{buyer_id}</code> · Gift: <code>{ui.esc(parsed["gift_name"])}-{parsed["gift_number"]}</code>')
    except Exception as e:
        log.exception('Failed to create demo offer')
        await m.answer(f'❌ Не удалось создать DEMO-офер: <code>{ui.esc(str(e)[:300])}</code>')


@dp.callback_query(F.data.regexp(r'^of:(accept|decline):\d+$'))
async def demo_offer_action(c: CallbackQuery):
    try:
        _, action, raw_id = c.data.split(':', 2)
        offer_id = int(raw_id)
    except Exception:
        return await c.answer('Некорректный офер', show_alert=True)
    offer = db.get_offer(offer_id)
    if not offer:
        return await c.answer('Офер не найден', show_alert=True)
    if int(offer['buyer_id']) != int(c.from_user.id):
        return await c.answer('Этот офер предназначен другому пользователю.', show_alert=True)
    if offer['status'] != STATUS_PENDING:
        return await c.answer('Офер уже обработан.', show_alert=True)
    new_status = STATUS_ACCEPTED if action == 'accept' else STATUS_DECLINED
    db.set_offer_status(offer_id, new_status)
    locale = offer['locale'] or 'ru'
    if new_status == STATUS_ACCEPTED:
        text = accepted_text(offer, locale)
        markup = transfer_keyboard(offer, locale)
    else:
        text = declined_text(offer, locale)
        markup = None
    try:
        kwargs = dict(chat_id=c.message.chat.id, message_id=c.message.message_id, text=text, reply_markup=markup)
        if getattr(c, 'business_connection_id', None):
            kwargs['business_connection_id'] = c.business_connection_id
        await safe(bot.edit_message_text, **kwargs)
    except Exception:
        # If the message cannot be edited, keep the state in DB and send a visible result.
        kwargs = dict(chat_id=c.message.chat.id, text=text, reply_markup=markup)
        if getattr(c, 'business_connection_id', None):
            kwargs['business_connection_id'] = c.business_connection_id
        await safe(bot.send_message, **kwargs)
    await c.answer('Принято' if action == 'accept' else 'Отклонено')


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

    try:
        await audit('Логирование подключено', None, f'Канал: <code>{LOG_CHANNEL_ID}</code>')
        log.info('Log channel configured: %s', LOG_CHANNEL_ID)
    except Exception as e:
        log.error('Cannot write to log channel %s. Add bot as channel admin with permission to post messages. Error: %r', LOG_CHANNEL_ID, e)
    await bot.set_my_commands([
        BotCommand(command='start', description='Главное меню'),
        BotCommand(command='stop', description='Остановить текущий поиск'),
        BotCommand(command='help', description='Как пользоваться'),
    ])
    try:
        await dp.start_polling(bot)
    finally:
        await api.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
