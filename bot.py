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
from aiogram.types import BotCommand, BufferedInputFile, CallbackQuery, LinkPreviewOptions, Message
from dotenv import load_dotenv

import keyboards as kb
import ui
from database import Database
from peek_api import AVAILABLE_MODELS, PeekAPI, gift_url, is_known_model, normalize_model_name, suggest_models

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('giftparser')

TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID') or 0)
CONCURRENCY = int(os.getenv('PEEK_CONCURRENCY') or 5)
if not TOKEN:
    raise RuntimeError('BOT_TOKEN не указан в .env')

MAX_RANGE = 10_000        # максимум ID за один поиск
MAX_LIVE_CARDS = 30       # сколько карточек присылать «вживую» во время поиска
EDIT_INTERVAL = 1.5       # не чаще раза в N секунд обновляем статус (защита от flood)

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
db = Database()
api = PeekAPI(CONCURRENCY)
ACTIVE: set[int] = set()  # пользователи с запущенным поиском


class S(StatesGroup):
    model = State()
    range = State()
    id_model = State()
    id_number = State()
    random_count = State()


# ───────────────────────── помощники ─────────────────────────
def is_admin(uid):
    return uid == OWNER_ID


def is_unlimited(uid):
    return is_admin(uid) or bool((db.get_user(uid) or {}).get('is_premium'))


def user_menu(uid):
    return kb.main_menu(is_admin(uid), bool((db.get_user(uid) or {}).get('is_premium')))


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


# ───────────────────────── доступ ─────────────────────────
@dp.message.middleware()
async def access_message(handler, event: Message, data):
    if event.from_user:
        uid = event.from_user.id
        db.upsert_user(uid, event.from_user.username, event.from_user.first_name)
        if db.is_banned(uid):
            await event.answer('🚫 <b>Доступ ограничен.</b>')
            return
    return await handler(event, data)


@dp.callback_query.middleware()
async def access_callback(handler, event: CallbackQuery, data):
    if db.is_banned(event.from_user.id):
        await event.answer('🚫 Доступ ограничен', show_alert=True)
        return
    return await handler(event, data)


# ───────────────────────── старт / общее ─────────────────────────
@dp.message(Command('start'))
@dp.message(Command('menu'))
async def start(m: Message, state: FSMContext):
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
    if not allowed(m.from_user.id, 1):
        return await m.answer('🆓 <b>Лимит Trial закончился.</b>\n\n💎 Подключите Premium для продолжения.')
    await state.set_state(S.model)
    await m.answer(
        '🔎 <b>Новый поиск</b>\n\nВведите название модели:\n'
        + ui.block('<code>Plush Pepe</code>', '<code>Eternal Rose</code>') +
        '\n<i>Или выберите из каталога — кнопка «🎁 Модели».</i>',
        reply_markup=kb.cancel())


@dp.message(S.model)
async def model_step(m: Message, state: FSMContext):
    raw = (m.text or '').strip()
    if len(raw) < 2:
        return await m.answer('❌ Введите название модели.')
    name = normalize_model_name(raw)
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
    batch_size = max(5, CONCURRENCY * 3)

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
                return await edit(msg, card('stopped'), kb.search_done_kb(sid, found, resumable=True))

            nums = list(range(current, min(end, current + batch_size - 1) + 1))
            results = dict(zip(nums, await asyncio.gather(*(api.get_gift(model, n) for n in nums),
                                                          return_exceptions=True)))
            # ── повторная попытка для сбойных ID
            failed = [n for n, r in results.items() if isinstance(r, Exception)]
            if failed:
                await asyncio.sleep(2)
                again = await asyncio.gather(*(api.get_gift(model, n) for n in failed), return_exceptions=True)
                results.update(zip(failed, again))
                failed = [n for n, r in results.items() if isinstance(r, Exception)]

            # ── весь пакет упал: не «сжигаем» ID, ждём и пробуем снова
            if len(failed) == len(nums):
                fail_streak += 1
                log.warning('Batch %s-%s failed (%s/3): %s', nums[0], nums[-1], fail_streak, results[nums[0]])
                if fail_streak >= 3:
                    raise RuntimeError('Peek не отвечает')
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
                    return await edit(msg, card('limit'), kb.search_done_kb(sid, found, resumable=True))

            if time.monotonic() - last_edit >= EDIT_INTERVAL:
                last_edit = time.monotonic()
                await edit(msg, card('running'), kb.running_kb())

        db.update_search(sid, end, checked, found, 'completed')
        await edit(msg, card('done'), kb.search_done_kb(sid, found))
    except Exception:
        log.exception('Search failed uid=%s sid=%s', uid, sid)
        db.update_search(sid, max(start - 1, current - 1), checked, found, 'error')
        await edit(msg, card('error'), kb.search_done_kb(sid, found, resumable=True))
    finally:
        ACTIVE.discard(uid)
        db.clear_stop(uid)


# ───────────────────────── поиск по одному ID ─────────────────────────
@dp.message(F.text == kb.BTN_ID)
async def id_search(m: Message, state: FSMContext):
    await state.set_state(S.id_model)
    await m.answer('🆔 <b>Поиск по ID</b>\n\nВведите название модели:', reply_markup=kb.cancel())


@dp.message(S.id_model)
async def id_model(m: Message, state: FSMContext):
    raw = (m.text or '').strip()
    if len(raw) < 2:
        return await m.answer('❌ Введите название модели.')
    name = normalize_model_name(raw)
    await state.update_data(model=name)
    await state.set_state(S.id_number)
    await m.answer(f'🎁 Модель · <b>{ui.esc(ui.pretty(name))}</b>\n\nВведите номер подарка:', reply_markup=kb.cancel())


@dp.message(S.id_number)
async def id_number(m: Message, state: FSMContext):
    text = (m.text or '').strip().lstrip('#').replace(' ', '')
    if not text.isdigit() or int(text) <= 0:
        return await m.answer('❌ Номер должен быть положительным числом.')
    n = int(text)
    model = (await state.get_data())['model']
    await state.clear()
    await m.answer('🔎 Проверяю…', reply_markup=user_menu(m.from_user.id))
    try:
        g = await api.get_gift(model, n)
    except RuntimeError as e:
        return await m.answer(f'⚠️ {ui.esc(e)}')
    if not g:
        return await m.answer(f'🔎 {ui.esc(ui.pretty(model))} <code>#{n}</code> — не найден.')
    await send_card(m, m.from_user.id, g, large=True)


# ───────────────────────── случайный поиск ─────────────────────────
@dp.message(F.text == kb.BTN_RANDOM)
async def random_search(m: Message, state: FSMContext):
    if not allowed(m.from_user.id, 1):
        return await m.answer('🆓 <b>Лимит Trial закончился.</b>')
    await state.set_state(S.random_count)
    await m.answer('🎲 <b>Случайный поиск</b>\n\nСколько случайных ID проверить? <code>1–20</code>',
                   reply_markup=kb.cancel())


@dp.message(S.random_count)
async def random_count(m: Message, state: FSMContext):
    text = (m.text or '').strip()
    if not text.isdigit() or not 1 <= int(text) <= 20:
        return await m.answer('❌ Введите число от 1 до 20.')
    count, uid = int(text), m.from_user.id
    if not allowed(uid, count):
        return await m.answer(f'🆓 Недостаточно проверок: осталось <b>{trial_left(uid)}</b>.')
    await state.clear()

    model = random.choice(AVAILABLE_MODELS)
    numbers = random.sample(range(1, 100_000), count)
    status = await m.answer(f'🎲 <b>{ui.esc(ui.pretty(model))}</b>\n🔢 Проверяю {count} случайных ID…',
                            reply_markup=user_menu(uid))
    results = await asyncio.gather(*(api.get_gift(model, n) for n in numbers), return_exceptions=True)
    found = 0
    for r in results:
        if isinstance(r, dict):
            found += 1
            await send_card(m, uid, r)
    if not is_unlimited(uid):
        db.consume_trial(uid, count)
    errors = sum(isinstance(r, Exception) for r in results)
    tail = f'\n⚠️ Ошибок сети: {errors}' if errors else ''
    await edit(status, f'🎲 <b>Готово</b>\n{ui.block(ui.kv("🎁", "Модель", ui.esc(ui.pretty(model))), ui.kv("✨", "Найдено", f"{found} из {count}"))}{tail}')


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
@dp.message(F.text == kb.BTN_ADMIN)
async def admin(m: Message):
    if is_admin(m.from_user.id):
        await m.answer(f'⚙️ <b>{ui.small("Admin")} · Панель</b>', reply_markup=kb.admin_menu())


def stats_text():
    s = db.stats()
    return (f'📈 <b>{ui.small("Stats")} · Статистика</b>\n\n' + ui.block(
        ui.kv('👥', 'Пользователей', ui.num(s['users'])),
        ui.kv('💎', 'Premium', ui.num(s['premium'])),
        ui.kv('🚫', 'Банов', ui.num(s['banned'])),
        ui.kv('🔎', 'Поисков', ui.num(s['searches'])),
        ui.kv('🎁', 'Находок', ui.num(s['found'])),
        ui.kv('⚡', 'Активных поисков', len(ACTIVE))))


@dp.callback_query(F.data == 'adm:stats')
async def adm_stats(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    await c.message.answer(stats_text())


@dp.callback_query(F.data == 'adm:users')
async def adm_users(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer('Нет доступа', show_alert=True)
    await c.answer()
    lines = []
    for u in db.recent_users(10):
        tag = '💎' if u['is_premium'] else ('🚫' if u['is_banned'] else '•')
        name = f'@{u["username"]}' if u['username'] else (u['first_name'] or '—')
        lines.append(f'{tag} <code>{u["user_id"]}</code> · {ui.esc(name)}')
    await c.message.answer(
        f'👥 <b>{ui.small("Users")} · Последние пользователи</b>\n\n' + ui.block(*lines) +
        '\n<i>Команды:</i> <code>/grant ID</code> · <code>/revoke ID</code> · <code>/ban ID</code> · <code>/unban ID</code>')


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


# ───────────────────────── запуск ─────────────────────────
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
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
