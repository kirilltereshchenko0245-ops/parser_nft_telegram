from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import ui
from peek_api import AVAILABLE_MODELS, gift_url, owner_url

# Названия кнопок вынесены в константы: бот сравнивает по ним текст сообщений.
BTN_SEARCH = '🔎 Новый поиск'
BTN_ID = '🆔 Поиск по ID'
BTN_RANDOM = '🎲 Случайный'
BTN_HISTORY = '📚 История'
BTN_FAVS = '⭐ Избранное'
BTN_MODELS = '🎁 Модели'
BTN_PROFILE = '📊 Профиль'
BTN_HELP = '❓ Помощь'
BTN_PREMIUM = '💎 Premium'
BTN_ADMIN = '⚙️ Админка'
BTN_OFFERS = '🎁 Оферы'
BTN_PARSER = '🔎 Парсинг по чатам'
BTN_CANCEL = '✖️ Отмена'
BTN_STOP = '⏹ Остановить'  # оставлено для совместимости со старыми клавиатурами

MODELS_PER_PAGE = 12
LIST_PER_PAGE = 8


# ───────────── reply-клавиатуры ─────────────
def main_menu(admin=False, premium=False, parser_visible=True):
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_ID))
    b.row(KeyboardButton(text=BTN_RANDOM), KeyboardButton(text=BTN_MODELS))
    b.row(KeyboardButton(text=BTN_HISTORY), KeyboardButton(text=BTN_FAVS))
    b.row(KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_HELP))
    b.row(KeyboardButton(text=BTN_OFFERS))
    if parser_visible:
        b.row(KeyboardButton(text=BTN_PARSER))
    extra = []
    if not premium:
        extra.append(KeyboardButton(text=BTN_PREMIUM))
    if admin:
        extra.append(KeyboardButton(text=BTN_ADMIN))
    if extra:
        b.row(*extra)
    return b.as_markup(resize_keyboard=True, input_field_placeholder='Выберите действие…')


def cancel():
    b = ReplyKeyboardBuilder()
    b.add(KeyboardButton(text=BTN_CANCEL))
    return b.as_markup(resize_keyboard=True, input_field_placeholder='Введите значение…')


# ───────────── карточка подарка ─────────────
def fav_button(name, n, favorite):
    return InlineKeyboardButton(
        text='★ В избранном' if favorite else '☆ В избранное',
        callback_data=f'fav:{name}:{n}',
    )


def gift_kb(g, favorite=False):
    b = InlineKeyboardBuilder()
    n = g.get('giftNumber')
    name = (g.get('giftName') or g.get('model') or '').replace(' ', '')
    links = []
    if owner_url(g):
        links.append(InlineKeyboardButton(text='👤 Владелец', url=owner_url(g)))
    if gift_url(g):
        links.append(InlineKeyboardButton(text='🎁 Открыть', url=gift_url(g)))
    if links:
        b.row(*links)
    if n is not None and name:
        b.row(fav_button(name, n, favorite))
    return b.as_markup()


def with_fav_state(markup: InlineKeyboardMarkup, favorite: bool) -> InlineKeyboardMarkup:
    """Меняет только кнопку избранного, остальные не трогает."""
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data and btn.callback_data.startswith('fav:'):
                _, name, n = btn.callback_data.split(':', 2)
                btn = fav_button(name, n, favorite)
            new_row.append(btn)
        rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── поиск ─────────────
def running_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='⏹ Остановить', callback_data='stop'))
    return b.as_markup()


def search_done_kb(sid, found, resumable=False):
    b = InlineKeyboardBuilder()
    row = []
    if found:
        row.append(InlineKeyboardButton(text='📋 Результаты', callback_data=f'pg:res:{sid}:1'))
        row.append(InlineKeyboardButton(text='📄 TXT', callback_data=f'exp:{sid}'))
    if row:
        b.row(*row)
    if resumable:
        b.row(InlineKeyboardButton(text='▶️ Продолжить', callback_data=f'resume:{sid}'))
    return b.as_markup()


def model_suggest_kb(options, raw):
    b = InlineKeyboardBuilder()
    for m in options:
        b.row(InlineKeyboardButton(text=f'🎁 {ui.pretty(m)}', callback_data=f'pm:{m}'))
    b.row(InlineKeyboardButton(text=f'Искать как «{raw[:24]}»', callback_data=f'pm:{raw}'))
    return b.as_markup()


# ───────────── история ─────────────
def history_list_kb(rows):
    b = InlineKeyboardBuilder()
    for x in rows:
        b.row(InlineKeyboardButton(text=ui.history_button_label(x), callback_data=f'hv:{x["id"]}'))
    return b.as_markup()


def history_detail_kb(x):
    b = InlineKeyboardBuilder()
    if x['found_count']:
        b.row(
            InlineKeyboardButton(text='📋 Результаты', callback_data=f'pg:res:{x["id"]}:1'),
            InlineKeyboardButton(text='📄 TXT', callback_data=f'exp:{x["id"]}'),
        )
    if x['status'] in ('stopped', 'error'):
        b.row(InlineKeyboardButton(text='▶️ Продолжить', callback_data=f'resume:{x["id"]}'))
    b.row(InlineKeyboardButton(text='‹ К списку', callback_data='hl'))
    return b.as_markup()


# ───────────── списки с пагинацией ─────────────
def pager_row(kind, arg, page, pages):
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text='‹', callback_data=f'pg:{kind}:{arg}:{page - 1}'))
    row.append(InlineKeyboardButton(text=f'{page}/{pages}', callback_data='noop'))
    if page < pages:
        row.append(InlineKeyboardButton(text='›', callback_data=f'pg:{kind}:{arg}:{page + 1}'))
    return row


def gifts_list_kb(gifts, kind, arg, page, pages, export_sid=None, back=None):
    """gifts — список словарей-подарков; каждая кнопка открывает полную карточку."""
    b = InlineKeyboardBuilder()
    buttons = []
    for g in gifts:
        name = (g.get('giftName') or '').replace(' ', '')
        buttons.append(InlineKeyboardButton(text=ui.gift_label(g), callback_data=f'gc:{name}:{g.get("giftNumber")}'))
    for i in range(0, len(buttons), 2):
        b.row(*buttons[i:i + 2])
    b.row(*pager_row(kind, arg, page, pages))
    tail = []
    if back:
        tail.append(InlineKeyboardButton(text='‹ Назад', callback_data=back))
    if export_sid:
        tail.append(InlineKeyboardButton(text='📄 TXT', callback_data=f'exp:{export_sid}'))
    if tail:
        b.row(*tail)
    return b.as_markup()


# ───────────── модели ─────────────
def models_kb(page):
    pages = max(1, -(-len(AVAILABLE_MODELS) // MODELS_PER_PAGE))
    start = (page - 1) * MODELS_PER_PAGE
    items = AVAILABLE_MODELS[start:start + MODELS_PER_PAGE]
    b = InlineKeyboardBuilder()
    btns = [InlineKeyboardButton(text=ui.pretty(m), callback_data=f'pm:{m}') for m in items]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='‹', callback_data=f'models:{page - 1}'))
    nav.append(InlineKeyboardButton(text=f'{page}/{pages}', callback_data='noop'))
    if page < pages:
        nav.append(InlineKeyboardButton(text='›', callback_data=f'models:{page + 1}'))
    b.row(*nav)
    return b.as_markup(), pages


# ───────────── админка ─────────────
def admin_menu():
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text='📈 Статистика', callback_data='adm:stats'),
        InlineKeyboardButton(text='👥 Пользователи', callback_data='adm:users'),
    )
    return b.as_markup()
