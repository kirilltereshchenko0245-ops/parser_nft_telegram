from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import ui
from peek_api import AVAILABLE_MODELS, gift_url, owner_url

# Главное меню — короткое, с inline-управлением внутри сообщений.
BTN_SEARCH = '🔎 Парсер'
BTN_ID = '🆔 Поиск по ID'
BTN_RANDOM = '🎲 Случайный парсер'
BTN_HISTORY = '📚 История'
BTN_FAVS = '⭐ Избранное'
BTN_MODELS = '🎁 Модели'
BTN_PROFILE = '👤 Профиль'
BTN_HELP = '❓ Помощь'
BTN_PREMIUM = '💎 Премиум'
BTN_ADMIN = '⚙️ Админка'
BTN_OFFERS = '🎁 Оферы'
BTN_PARSER = '🔎 Парсер'
BTN_CANCEL = '✖️ Отмена'
BTN_STOP = '⏹ Остановить'

MODELS_PER_PAGE = 12
LIST_PER_PAGE = 8

def main_menu(admin=False, premium=False, parser_visible=True):
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text='🔎 Парсер'), KeyboardButton(text='💎 Премиум'))
    b.row(KeyboardButton(text='🎁 Оферы'), KeyboardButton(text='👤 Профиль'))
    if admin:
        b.row(KeyboardButton(text=BTN_ADMIN))
    return b.as_markup(resize_keyboard=True, input_field_placeholder='Выберите действие…')

def inline_main_menu(admin=False, premium=False, offers=False):
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='🔎 Парсер', callback_data='menu:parser'))
    b.row(InlineKeyboardButton(text='💎 Премиум', callback_data='menu:premium'),
          InlineKeyboardButton(text='🎁 Оферы', callback_data='menu:offers'))
    b.row(InlineKeyboardButton(text='👤 Профиль', callback_data='menu:profile'),
          InlineKeyboardButton(text='❓ Помощь', callback_data='menu:help'))
    if admin:
        b.row(InlineKeyboardButton(text='⚙️ Админка', callback_data='menu:admin'))
    return b.as_markup()

def parser_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='🔎 Обычный парсер', callback_data='parser_ui:regular'))
    b.row(InlineKeyboardButton(text='🎲 Случайный парсер', callback_data='parser_ui:random'))
    b.row(InlineKeyboardButton(text='🆔 Поиск по ID', callback_data='parser_ui:id'))
    b.row(InlineKeyboardButton(text='📡 Парсер чатов', callback_data='parser_ui:chats'))
    b.row(InlineKeyboardButton(text='‹ Главное меню', callback_data='menu:home'))
    return b.as_markup()

def profile_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='ПОСЛЕДНИЙ ПАРСИНГ', callback_data='profile:last'))
    b.row(InlineKeyboardButton(text='⭐ Избранное', callback_data='profile:favorites'), InlineKeyboardButton(text='💳 Покупки', callback_data='profile:payments'))
    b.row(InlineKeyboardButton(text='📚 История', callback_data='profile:history'))
    b.row(InlineKeyboardButton(text='💎 Премиум', callback_data='menu:premium'),
          InlineKeyboardButton(text='🆘 Техподдержка', url='https://t.me/witensea'))
    b.row(InlineKeyboardButton(text='‹ Главное меню', callback_data='menu:home'))
    return b.as_markup()

def premium_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='⚡ 7 TON · 2 дня', callback_data='buy:premium_2d'))
    b.row(InlineKeyboardButton(text='📡 10 TON · чаты · 24 часа', callback_data='buy:chat_24h'))
    b.row(InlineKeyboardButton(text='🎁 25 TON · оферы · 5 дней', callback_data='buy:offers_5d'))
    b.row(InlineKeyboardButton(text='‹ Главное меню', callback_data='menu:home'))
    return b.as_markup()

def offers_locked_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='💎 Купить доступ в Премиум', callback_data='menu:premium'))
    b.row(InlineKeyboardButton(text='📖 Инструкция по установке', callback_data='offers:install'))
    b.row(InlineKeyboardButton(text='‹ Главное меню', callback_data='menu:home'))
    return b.as_markup()

def offers_active_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='⚙️ Настроить профиль передачи', callback_data='offer:self:profile'))
    b.row(InlineKeyboardButton(text='📖 Инструкция по установке', callback_data='offers:install'))
    b.row(InlineKeyboardButton(text='‹ Главное меню', callback_data='menu:home'))
    return b.as_markup()

def catalog_kb(items, page, pages, query=''):
    b = InlineKeyboardBuilder()
    for i in range(0, len(items), 2):
        row = []
        for title, model in items[i:i+2]:
            row.append(InlineKeyboardButton(text=title, callback_data=f'catalog:pick:{model}'))
        b.row(*row)
    nav=[]
    if page > 1:
        nav.append(InlineKeyboardButton(text='‹', callback_data=f'catalog:page:{page-1}:{query}'))
    nav.append(InlineKeyboardButton(text=f'{page}/{pages}', callback_data='noop'))
    if page < pages:
        nav.append(InlineKeyboardButton(text='›', callback_data=f'catalog:page:{page+1}:{query}'))
    b.row(*nav)
    b.row(InlineKeyboardButton(text='🔎 Поиск по названию', callback_data='catalog:query'))
    b.row(InlineKeyboardButton(text='‹ Парсер', callback_data='menu:parser'))
    return b.as_markup()

def cancel():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='‹ Отмена', callback_data='fsm:cancel'))
    return b.as_markup()


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
