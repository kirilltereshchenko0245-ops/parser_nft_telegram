from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# === ГЛАВНОЕ МЕНЮ ===

def main_menu_keyboard(is_admin: bool = False, is_premium: bool = False):
    """Главное меню бота"""
    builder = ReplyKeyboardBuilder()
    
    builder.row(
        KeyboardButton(text="🔍 Начать поиск"),
        KeyboardButton(text="🔎 Поиск по ID")
    )
    builder.row(
        KeyboardButton(text="📋 Список подарков"),
        KeyboardButton(text="📊 Профиль")
    )
    builder.row(
        KeyboardButton(text="⏭️ Продолжить"),
        KeyboardButton(text="ℹ️ Помощь")
    )
    
    if not is_premium:
        builder.row(KeyboardButton(text="⭐ Купить Premium"))
    
    if is_admin:
        builder.row(KeyboardButton(text="⚙️ Админка"))
    
    return builder.as_markup(resize_keyboard=True)

# === АДМИНКА ===

def admin_menu_keyboard():
    """Меню админки"""
    builder = ReplyKeyboardBuilder()
    
    builder.row(
        KeyboardButton(text="👥 Пользователи"),
        KeyboardButton(text="📈 Статистика")
    )
    builder.row(
        KeyboardButton(text="🔧 Настройки"),
        KeyboardButton(text="🚫 Баны")
    )
    builder.row(
        KeyboardButton(text="🔙 Назад")
    )
    
    return builder.as_markup(resize_keyboard=True)

def admin_settings_keyboard(mode: str):
    """Настройки доступа к боту"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if mode == 'owner_only' else '⬜'} Только владелец",
            callback_data="settings_owner_only"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if mode == 'all' else '⬜'} Все пользователи",
            callback_data="settings_all"
        )
    )
    
    return builder.as_markup()

def ban_user_keyboard(user_id: int):
    """Кнопки для бана/разбана пользователя"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{user_id}"),
        InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_{user_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⭐ Дать Premium", callback_data=f"premium_give_{user_id}"),
        InlineKeyboardButton(text="❌ Забрать Premium", callback_data=f"premium_remove_{user_id}")
    )
    
    return builder.as_markup()

# === ПОИСК ===

def cancel_keyboard():
    """Кнопка отмены"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)

# === РЕЗУЛЬТАТЫ ПОИСКА ===

def gift_result_keyboard(gift_number: int, username: str = None, gift_name: str = None):
    """Кнопки для найденного подарка"""
    builder = InlineKeyboardBuilder()
    
    # Кнопка перехода к владельцу
    if username:
        clean_username = username.replace('t.me/', '').lstrip('@')
        builder.row(
            InlineKeyboardButton(
                text="👤 Перейти к владельцу",
                url=f"https://t.me/{clean_username}"
            )
        )
    
    # Кнопка просмотра подарка
    if gift_name and gift_number:
        from urllib.parse import quote
        safe_name = str(gift_name).replace(" ", "")
        builder.row(
            InlineKeyboardButton(
                text="🎁 Посмотреть подарок",
                url=f"https://t.me/nft/{quote(safe_name)}-{gift_number}"
            )
        )
    
    return builder.as_markup()

# === ПРОФИЛЬ ===

def profile_keyboard():
    """Кнопки в профиле"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="💬 Связаться с разработчиком", url="https://t.me/kod30")
    )
    
    return builder.as_markup()

def models_list_keyboard(page: int = 1, per_page: int = 10, total: int = 0):
    """Клавиатура для списка моделей с пагинацией"""
    builder = InlineKeyboardBuilder()
    
    total_pages = (total + per_page - 1) // per_page
    
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"models_page_{page-1}"))
    
    buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="models_current"))
    
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"models_page_{page+1}"))
    
    if buttons:
        builder.row(*buttons)
    
    return builder.as_markup()

# === ПРОДОЛЖЕНИЕ ПОИСКА ===

def continue_search_keyboard():
    """Продолжить последний поиск"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Продолжить", callback_data="continue_yes"),
        InlineKeyboardButton(text="❌ Новый поиск", callback_data="continue_no")
    )
    
    return builder.as_markup()

# === ПЛАТЕЖИ ===

def payment_method_keyboard():
    """Выбор метода оплаты"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="💎 CryptoBot", callback_data="pay_cryptobot")
    )
    builder.row(
        InlineKeyboardButton(text="🚀 XRocket", callback_data="pay_xrocket")
    )
    builder.row(
        InlineKeyboardButton(text="💬 Связаться с поддержкой", url="https://t.me/kod30")
    )
    
    return builder.as_markup()

def stop_parsing_keyboard():
    """Кнопка остановки парсинга"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⏹️ Остановить", callback_data="stop_parsing")
    )
    
    return builder.as_markup()

def continue_parsing_keyboard():
    """Продолжить парсинг после остановки"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="▶️ Продолжить", callback_data="continue_parsing")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")
    )
    
    return builder.as_markup()
