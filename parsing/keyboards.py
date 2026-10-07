"""Keyboards for the MTProto parser."""
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton


def parser_main_menu(is_connected=False, mode='all'):
    b = InlineKeyboardBuilder()
    if is_connected:
        b.row(
            InlineKeyboardButton(text=('✅ Любые NFT' if mode == 'all' else '🎁 Любые NFT'), callback_data='parser:mode:all'),
            InlineKeyboardButton(text=('✅ Неулучшенные' if mode == 'unupgraded' else '💎 Неулучшенные'), callback_data='parser:mode:unupgraded')
        )
        b.row(
            InlineKeyboardButton(text='📂 Мои чаты', callback_data='parser:chats'),
            InlineKeyboardButton(text='▶️ Запустить', callback_data='parser:start')
        )
        b.row(
            InlineKeyboardButton(text='📊 Статистика', callback_data='parser:stats'),
            InlineKeyboardButton(text='⚙️ Аккаунт', callback_data='parser:account')
        )
    else:
        b.row(InlineKeyboardButton(text='🔐 Подключить аккаунт (сессия)', callback_data='parser:connect'))
    return b.as_markup()


def parser_account_menu():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='🔄 Проверить подключение', callback_data='parser:reconnect_existing'))
    b.row(InlineKeyboardButton(text='🚪 Отключить аккаунт', callback_data='parser:disconnect'))
    b.row(InlineKeyboardButton(text='‹ Назад', callback_data='parser:menu'))
    return b.as_markup()


def parser_chats_list(chats, page=0, per_page=15):
    b = InlineKeyboardBuilder()
    start = page * per_page
    visible = chats[start:start + per_page]
    for chat in visible:
        icon = '🟢' if chat['is_enabled'] else '⚪'
        title = (chat['title'] or 'Без названия')[:34]
        b.row(InlineKeyboardButton(text=f'{icon} {title}', callback_data=f'parser:toggle:{chat["chat_id"]}'))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='‹', callback_data=f'parser:chats:{page-1}'))
    if start + per_page < len(chats):
        nav.append(InlineKeyboardButton(text='›', callback_data=f'parser:chats:{page+1}'))
    if nav:
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text='✅ Включить все', callback_data='parser:chats_all:on'),
        InlineKeyboardButton(text='❌ Выключить все', callback_data='parser:chats_all:off')
    )
    b.row(
        InlineKeyboardButton(text='🔄 Обновить', callback_data='parser:refresh_chats'),
        InlineKeyboardButton(text='‹ Назад', callback_data='parser:menu')
    )
    return b.as_markup()


def parser_limit_menu(mode):
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text='50', callback_data='parser:limit:50'),
        InlineKeyboardButton(text='100', callback_data='parser:limit:100'),
        InlineKeyboardButton(text='250', callback_data='parser:limit:250')
    )
    b.row(
        InlineKeyboardButton(text='500', callback_data='parser:limit:500'),
        InlineKeyboardButton(text='1000', callback_data='parser:limit:1000'),
        InlineKeyboardButton(text='✏️ Свой лимит', callback_data='parser:limit:custom')
    )
    b.row(InlineKeyboardButton(text='‹ Назад', callback_data='parser:menu'))
    return b.as_markup()


def parser_running_keyboard():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='⏹ Остановить', callback_data='parser:stop'))
    return b.as_markup()


def parser_cancel_keyboard():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='‹ Отмена', callback_data='parser:fsm_cancel'))
    return b.as_markup()


def parser_gift_kb(gift):
    b = InlineKeyboardBuilder()
    slug = gift.get('gift_slug')
    username = gift.get('username')
    if slug:
        b.row(InlineKeyboardButton(text='🎁 Открыть подарок', url=f'https://t.me/nft/{slug}'))
    if username:
        b.row(InlineKeyboardButton(text='👤 Открыть профиль', url=f'https://t.me/{username.lstrip("@")}'))
    else:
        b.row(InlineKeyboardButton(text='👤 Профиль по ID', url=f'tg://user?id={gift.get("user_id")}'))
    return b.as_markup()


def back_to_parser():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text='‹ К парсеру', callback_data='parser:menu'))
    return b.as_markup()
