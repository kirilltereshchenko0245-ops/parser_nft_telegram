"""Клавиатуры для MTProto парсера."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton


def parser_main_menu(is_connected=False):
    """Главное меню парсера."""
    builder = InlineKeyboardBuilder()
    
    if is_connected:
        builder.row(
            InlineKeyboardButton(text='🎁 Любые NFT', callback_data='parser:mode:all'),
            InlineKeyboardButton(text='💎 Неулучшенные', callback_data='parser:mode:unupgraded')
        )
        builder.row(
            InlineKeyboardButton(text='📂 Мои чаты', callback_data='parser:chats'),
            InlineKeyboardButton(text='▶️ Запустить', callback_data='parser:start')
        )
        builder.row(
            InlineKeyboardButton(text='📊 Статистика', callback_data='parser:stats'),
            InlineKeyboardButton(text='⚙️ Аккаунт', callback_data='parser:account')
        )
    else:
        builder.row(
            InlineKeyboardButton(text='🔐 Подключить аккаунт', callback_data='parser:connect')
        )
    
    return builder.as_markup()


def parser_account_menu():
    """Меню управления аккаунтом."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🔄 Переподключить', callback_data='parser:reconnect'),
        InlineKeyboardButton(text='🚪 Отключить', callback_data='parser:disconnect')
    )
    builder.row(
        InlineKeyboardButton(text='‹ Назад', callback_data='parser:menu')
    )
    return builder.as_markup()


def parser_chats_list(chats):
    """Список чатов с кнопками вкл/выкл."""
    builder = InlineKeyboardBuilder()
    
    for chat in chats:
        icon = '✅' if chat['is_enabled'] else '❌'
        title = chat['title'][:30]
        builder.row(
            InlineKeyboardButton(
                text=f'{icon} {title}',
                callback_data=f'parser:toggle:{chat["chat_id"]}'
            )
        )
    
    builder.row(
        InlineKeyboardButton(text='🔄 Обновить список', callback_data='parser:refresh_chats'),
        InlineKeyboardButton(text='‹ Назад', callback_data='parser:menu')
    )
    
    return builder.as_markup()


def parser_running_keyboard():
    """Клавиатура во время работы парсера."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='⏹ Остановить', callback_data='parser:stop')
    )
    return builder.as_markup()


def parser_cancel_keyboard():
    """Кнопка отмены."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text='✖️ Отмена'))
    return builder.as_markup(resize_keyboard=True)


def back_to_parser():
    """Кнопка возврата к парсеру."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='‹ К парсеру', callback_data='parser:menu')
    )
    return builder.as_markup()
