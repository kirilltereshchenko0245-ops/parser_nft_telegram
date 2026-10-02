"""
🎁 Telegram NFT Gift Parser v3.0
Красивый минималистичный дизайн
"""

import asyncio
import os
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dotenv import load_dotenv
from database import Database
from peek_api import peek_gift_info, AVAILABLE_MODELS, normalize_model_name
from logger_channel import ChannelLogger

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ НАСТРОЙКИ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1003864916255"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database()
channel_logger = ChannelLogger(bot, LOG_CHANNEL_ID)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎯 FSM STATES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SearchStates(StatesGroup):
    waiting_for_model = State()
    waiting_for_range = State()
    waiting_for_id = State()
    waiting_for_random_count = State()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎨 КРАСИВЫЕ КЛАВИАТУРЫ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main_menu(is_premium: bool = False, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔍 Поиск по модели", callback_data="search_model"),
        InlineKeyboardButton(text="🎲 Случайный поиск", callback_data="search_random")
    )
    builder.row(
        InlineKeyboardButton(text="🔎 Поиск по ID", callback_data="search_id"),
        InlineKeyboardButton(text="📋 Список моделей", callback_data="models_list")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Мой профиль", callback_data="profile"),
        InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
    )
    
    if not is_premium:
        builder.row(
            InlineKeyboardButton(text="⭐ Premium подписка", callback_data="buy_premium")
        )
    
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin")
        )
    
    return builder.as_markup()

def cancel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка отмены"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()

def stop_keyboard() -> InlineKeyboardMarkup:
    """Кнопка остановки парсинга"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏹️ Остановить", callback_data="stop_parsing"))
    return builder.as_markup()

def continue_keyboard() -> InlineKeyboardMarkup:
    """Кнопка продолжения"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="▶️ Продолжить", callback_data="continue_parsing"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
    )
    return builder.as_markup()

def gift_keyboard(gift_id: int, username: str = None, model: str = None) -> InlineKeyboardMarkup:
    """Кнопки для найденного подарка"""
    builder = InlineKeyboardBuilder()
    
    if username:
        builder.row(
            InlineKeyboardButton(text="👤 Профиль владельца", url=f"https://t.me/{username}")
        )
    
    if model and gift_id:
        safe_name = model.replace(" ", "")
        builder.row(
            InlineKeyboardButton(text="🎁 Открыть подарок", url=f"https://t.me/nft/{safe_name}-{gift_id}")
        )
    
    return builder.as_markup()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎭 MIDDLEWARE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message.middleware()
async def check_access(handler, event: Message, data: dict):
    """Проверка доступа"""
    user_id = event.from_user.id
    username = event.from_user.username or "NoUsername"
    
    # Проверка бана
    if db.is_banned(user_id):
        await event.answer("🚫 <b>Вы заблокированы</b>")
        return
    
    # Регистрация нового пользователя
    if not db.get_user(user_id):
        db.add_user(user_id, username)
        await channel_logger.log_action(user_id, username, "🆕 Новый пользователь")
    
    return await handler(event, data)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🏠 КОМАНДЫ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Старт бота"""
    await state.clear()
    
    user_id = message.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    welcome_text = (
        "╔═══════════════════════════╗\n"
        "║   🎁 <b>GIFT PARSER v3.0</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "🔍 Ищу подарки Telegram NFT по:\n"
        "   • Модели (EternalRose, EvilEye...)\n"
        "   • ID (случайный поиск)\n"
        "   • Диапазону (#100-200)\n\n"
        "💎 <b>Твой статус:</b>\n"
    )
    
    if is_admin:
        welcome_text += "   👑 <b>Администратор</b> (безлимит)\n"
    elif user['is_premium']:
        welcome_text += "   ⭐ <b>Premium</b> (безлимит)\n"
    else:
        welcome_text += f"   🆓 Пробная версия ({user['trial_searches_left']}/25 поисков)\n"
    
    welcome_text += "\n📌 <b>Выбери действие:</b>"
    
    await message.answer(
        welcome_text,
        reply_markup=main_menu(is_premium=user['is_premium'], is_admin=is_admin)
    )

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    """Остановить парсинг"""
    user_id = message.from_user.id
    db.set_stop_flag(user_id, True)
    
    await message.answer(
        "⏹️ <b>Остановка парсинга...</b>\n\n"
        "Подожди несколько секунд",
        reply_markup=continue_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Помощь"""
    help_text = (
        "╔═══════════════════════════╗\n"
        "║      ℹ️ <b>ИНСТРУКЦИЯ</b>      ║\n"
        "╚═══════════════════════════╝\n\n"
        "<b>🔍 Поиск по модели:</b>\n"
        "1️⃣ Нажми «Поиск по модели»\n"
        "2️⃣ Введи название (например: eternal rose)\n"
        "3️⃣ Введи диапазон (100-200)\n"
        "4️⃣ Получи результаты!\n\n"
        "<b>🎲 Случайный поиск:</b>\n"
        "• Бот сам выбирает модели и ID\n"
        "• Укажи сколько подарков найти\n"
        "• Быстрый способ найти что-то интересное\n\n"
        "<b>🔎 Поиск по ID:</b>\n"
        "• Проверь конкретный ID любой модели\n"
        "• Введи ID и модель\n\n"
        "<b>💡 Регистр не важен:</b>\n"
        "✅ eternal rose = EternalRose\n"
        "✅ evil eye = EvilEye\n"
        "✅ blue star = BlueStar\n\n"
        "<b>📊 Лимиты:</b>\n"
        "• Пробная версия: 25 поисков\n"
        "• Premium: безлимит за 5 TON\n\n"
        "💬 <b>Поддержка:</b> @kod30"
    )
    
    await message.answer(help_text)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎯 CALLBACK HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуться в главное меню"""
    await state.clear()
    
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\n"
        "Выбери действие:",
        reply_markup=main_menu(is_premium=user['is_premium'], is_admin=is_admin)
    )
    await callback.answer()

@dp.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена"""
    await state.clear()
    
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    await callback.message.edit_text(
        "❌ <b>Отменено</b>",
        reply_markup=main_menu(is_premium=user['is_premium'], is_admin=is_admin)
    )
    await callback.answer()

@dp.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    """Помощь"""
    await cmd_help(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def callback_profile(callback: CallbackQuery):
    """Профиль пользователя"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    profile_text = (
        "╔═══════════════════════════╗\n"
        "║     📊 <b>МОЙ ПРОФИЛЬ</b>     ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👤 <b>Пользователь:</b> @{callback.from_user.username or 'NoUsername'}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
        f"💎 <b>Статус:</b> "
    )
    
    if is_admin:
        profile_text += "👑 <b>Администратор</b>\n"
    elif user['is_premium']:
        profile_text += "⭐ <b>Premium</b>\n"
    else:
        profile_text += f"🆓 Пробная версия\n"
        profile_text += f"📊 <b>Осталось поисков:</b> {user['trial_searches_left']}/25\n"
    
    # История поисков
    searches = db.get_user_searches(user_id, limit=5)
    if searches:
        profile_text += "\n📜 <b>Последние поиски:</b>\n"
        for search in searches:
            profile_text += f"  • {search['model']} ({search['range_start']}-{search['range_end']})\n"
    
    await callback.message.edit_text(profile_text, reply_markup=main_menu(is_premium=user['is_premium'], is_admin=is_admin))
    await callback.answer()

@dp.callback_query(F.data == "models_list")
async def callback_models_list(callback: CallbackQuery):
    """Список моделей"""
    models_text = (
        "╔═══════════════════════════╗\n"
        "║   📋 <b>ДОСТУПНЫЕ МОДЕЛИ</b>  ║\n"
        "╚═══════════════════════════╝\n\n"
        "<b>💎 Легендарные:</b>\n"
        "• DurovsCap (~500-1000 TON)\n"
        "• HeroicHelmet (~200-500 TON)\n"
        "• MightyArm (~150-300 TON)\n\n"
        "<b>🔥 Редкие:</b>\n"
        "• PreciousPeach, PlushPepe\n"
        "• IonGem, EvilEye, GenieLamp\n\n"
        "<b>⭐ Обычные:</b>\n"
        "• EternalRose, BlueStar, ToyBear\n"
        "• IceCream, SnowGlobe\n\n"
        "<b>📦 Массовые:</b>\n"
        "• FreshSocks, BerryBox\n"
        "• GingerCookie, MoneyPot\n\n"
        f"<b>Всего моделей:</b> {len(AVAILABLE_MODELS)}\n\n"
        "💡 <i>Регистр не важен при вводе</i>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    
    await callback.message.edit_text(models_text, reply_markup=builder.as_markup())
    await callback.answer()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔍 ПОИСК ПО МОДЕЛИ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "search_model")
async def callback_search_model(callback: CallbackQuery, state: FSMContext):
    """Начать поиск по модели"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    # Проверка лимитов (админ = безлимит)
    if not is_admin and not user['is_premium'] and user['trial_searches_left'] <= 0:
        await callback.message.edit_text(
            "❌ <b>Пробные поиски закончились!</b>\n\n"
            f"💎 Осталось: 0/25\n\n"
            "Оформи <b>Premium</b> для безлимитного доступа:\n"
            "💰 Всего <b>5 TON</b>",
            reply_markup=main_menu(is_premium=False, is_admin=False)
        )
        await callback.answer()
        return
    
    await state.set_state(SearchStates.waiting_for_model)
    
    await callback.message.edit_text(
        "╔═══════════════════════════╗\n"
        "║   🔍 <b>ПОИСК ПО МОДЕЛИ</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        "🎁 <b>Введи название модели:</b>\n\n"
        "<i>Примеры:</i>\n"
        "• <code>Eternal Rose</code>\n"
        "• <code>Evil Eye</code>\n"
        "• <code>BlueStar</code>\n\n"
        "💡 Регистр и пробелы не важны",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()

@dp.message(SearchStates.waiting_for_model)
async def process_model_input(message: Message, state: FSMContext):
    """Обработка модели"""
    model_input = message.text.strip()
    model_name = normalize_model_name(model_input)
    
    await state.update_data(model=model_name)
    await state.set_state(SearchStates.waiting_for_range)
    
    await message.answer(
        f"✅ <b>Модель:</b> <code>{model_name}</code>\n\n"
        "📊 <b>Введи диапазон ID:</b>\n\n"
        "<i>Формат:</i> <code>начало-конец</code>\n\n"
        "<i>Примеры:</i>\n"
        "• <code>1-100</code>\n"
        "• <code>500-600</code>\n"
        "• <code>13000-13050</code>",
        reply_markup=cancel_keyboard()
    )

@dp.message(SearchStates.waiting_for_range)
async def process_range_input(message: Message, state: FSMContext):
    """Обработка диапазона"""
    range_text = message.text.strip()
    
    try:
        start, end = map(int, range_text.split('-'))
        
        if start < 1 or end < start:
            await message.answer("❌ Неправильный диапазон! Попробуй ещё раз:")
            return
        
        user_id = message.from_user.id
        user = db.get_user(user_id)
        is_admin = (user_id == OWNER_ID)
        
        actual_range = end - start + 1
        
        # Проверка лимитов (админ = безлимит)
        if not is_admin and not user['is_premium']:
            max_range = user['trial_searches_left']
            
            if actual_range > max_range:
                await message.answer(
                    f"❌ <b>Превышен лимит!</b>\n\n"
                    f"📊 Запрошено: {actual_range}\n"
                    f"💎 Доступно: {max_range}\n\n"
                    f"Максимальный диапазон: <code>{start}-{start + max_range - 1}</code>"
                )
                return
        
        data = await state.get_data()
        model = data['model']
        
        await state.clear()
        
        # Начинаем парсинг
        await start_parsing(message, user_id, model, start, end)
        
    except ValueError:
        await message.answer(
            "❌ <b>Неправильный формат!</b>\n\n"
            "Используй формат: <code>начало-конец</code>\n"
            "Например: <code>1-100</code>"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎲 СЛУЧАЙНЫЙ ПОИСК
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "search_random")
async def callback_search_random(callback: CallbackQuery, state: FSMContext):
    """Случайный поиск"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    # Проверка лимитов
    if not is_admin and not user['is_premium'] and user['trial_searches_left'] <= 0:
        await callback.message.edit_text(
            "❌ <b>Пробные поиски закончились!</b>\n\n"
            "Оформи Premium для продолжения",
            reply_markup=main_menu(is_premium=False, is_admin=False)
        )
        await callback.answer()
        return
    
    await state.set_state(SearchStates.waiting_for_random_count)
    
    max_count = user['trial_searches_left'] if not is_admin and not user['is_premium'] else 1000
    
    await callback.message.edit_text(
        "╔═══════════════════════════╗\n"
        "║   🎲 <b>СЛУЧАЙНЫЙ ПОИСК</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        "🎁 <b>Сколько подарков найти?</b>\n\n"
        f"💎 Доступно: до <b>{max_count}</b>\n\n"
        "<i>Введи число:</i>\n"
        "• <code>10</code> - найти 10 подарков\n"
        "• <code>50</code> - найти 50 подарков",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()

@dp.message(SearchStates.waiting_for_random_count)
async def process_random_count(message: Message, state: FSMContext):
    """Обработка количества для случайного поиска"""
    try:
        count = int(message.text.strip())
        
        user_id = message.from_user.id
        user = db.get_user(user_id)
        is_admin = (user_id == OWNER_ID)
        
        max_count = user['trial_searches_left'] if not is_admin and not user['is_premium'] else 1000
        
        if count < 1:
            await message.answer("❌ Число должно быть больше 0!")
            return
        
        if count > max_count:
            await message.answer(
                f"❌ <b>Превышен лимит!</b>\n\n"
                f"💎 Доступно: {max_count}\n"
                f"📝 Запрошено: {count}"
            )
            return
        
        await state.clear()
        
        # Начинаем случайный парсинг
        await start_random_parsing(message, user_id, count)
        
    except ValueError:
        await message.answer("❌ Введи число! Например: <code>50</code>")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ ПАРСИНГ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_parsing(message: Message, user_id: int, model: str, start: int, end: int):
    """Основной процесс парсинга"""
    username = message.from_user.username or "NoUsername"
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Логирование
    await channel_logger.log_search(user_id, username, model, start, end)
    
    # Сохраняем поиск
    search_id = db.add_search(user_id, model, start, end)
    
    # Уменьшаем лимит (админ не расходует)
    if not is_admin and not user['is_premium']:
        checked_ids = end - start + 1
        new_value = user['trial_searches_left'] - checked_ids
        db.update_trial_searches(user_id, max(0, new_value))
    
    # Начало
    progress_msg = await message.answer(
        "╔═══════════════════════════╗\n"
        "║   🔍 <b>ПОИСК ЗАПУЩЕН</b>    ║\n"
        "╚═══════════════════════════╝\n\n"
        f"🎁 <b>Модель:</b> <code>{model}</code>\n"
        f"📊 <b>Диапазон:</b> <code>{start}-{end}</code>\n\n"
        "⏳ <b>Прогресс:</b> 0%",
        reply_markup=stop_keyboard()
    )
    
    found_gifts = []
    total_checked = 0
    total_range = end - start + 1
    last_found_id = None
    
    for gift_id in range(start, end + 1):
        # Проверка остановки
        if db.should_stop_parsing(user_id):
            await progress_msg.edit_text(
                "⏹️ <b>ПАРСИНГ ОСТАНОВЛЕН</b>\n\n"
                f"📊 Проверено: {total_checked}/{total_range}\n"
                f"✅ Найдено: {len(found_gifts)}\n\n"
                f"📌 Последний ID: <code>{last_found_id or gift_id}</code>",
                reply_markup=continue_keyboard()
            )
            # Сохраняем для продолжения
            db.update_last_search(user_id, model, gift_id, end)
            return
        
        # Проверка подарка
        gift_data = await peek_gift_info(gift_id, model)
        total_checked += 1
        
        if gift_data['found']:
            last_found_id = gift_id
            found_gifts.append(gift_data)
            
            # Сохраняем в БД
            db.add_found_gift(
                search_id=search_id,
                gift_number=gift_id,
                model=model,
                owner_username=gift_data.get('username', 'Unknown'),
                is_visible=gift_data.get('is_visible', True),
                attributes=str(gift_data.get('attributes', {}))
            )
            
            # Уведомление о находке
            visibility = "👁️ Открыт" if gift_data.get('is_visible') else "🔒 Скрыт"
            owner = gift_data.get('username')
            owner_id = gift_data.get('user_id')
            
            gift_text = (
                "┏━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                f"┃  ✅ <b>НАЙДЕН #{gift_id}</b>\n"
                "┗━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
                f"🎁 <b>Модель:</b> <code>{model}</code>\n"
            )
            
            if owner:
                gift_text += f"👤 <b>Владелец:</b> @{owner}\n"
            
            if owner_id:
                gift_text += f"🆔 <b>ID:</b> <code>{owner_id}</code>\n"
            
            gift_text += f"👁️ <b>Видимость:</b> {visibility}\n"
            
            await message.answer(
                gift_text,
                reply_markup=gift_keyboard(gift_id, owner, model)
            )
        
        # Обновление прогресса каждые 10%
        progress_percent = int((total_checked / total_range) * 100)
        if total_checked % max(1, total_range // 10) == 0:
            await progress_msg.edit_text(
                "╔═══════════════════════════╗\n"
                "║   🔍 <b>ПОИСК ЗАПУЩЕН</b>    ║\n"
                "╚═══════════════════════════╝\n\n"
                f"🎁 <b>Модель:</b> <code>{model}</code>\n"
                f"📊 <b>Диапазон:</b> <code>{start}-{end}</code>\n\n"
                f"⏳ <b>Прогресс:</b> {progress_percent}%\n"
                f"✅ <b>Найдено:</b> {len(found_gifts)}",
                reply_markup=stop_keyboard()
            )
        
        # Задержка с проверкой остановки
        for _ in range(7):
            if db.should_stop_parsing(user_id):
                break
            await asyncio.sleep(1)
    
    # Завершение
    await progress_msg.delete()
    
    # Итоговое сообщение
    if found_gifts:
        summary = format_gift_summary(found_gifts, model, start, end)
        for part in summary:
            await message.answer(part)
    
    result_text = (
        "╔═══════════════════════════╗\n"
        "║   ✅ <b>ПОИСК ЗАВЕРШЁН</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        f"📊 <b>Проверено:</b> {total_checked}\n"
        f"✅ <b>Найдено:</b> {len(found_gifts)}\n"
    )
    
    if last_found_id:
        result_text += f"\n📌 <b>Последний найденный:</b> #{last_found_id}"
    
    await message.answer(
        result_text,
        reply_markup=main_menu(is_premium=user['is_premium'], is_admin=is_admin)
    )

async def start_random_parsing(message: Message, user_id: int, count: int):
    """Случайный парсинг"""
    import random
    
    username = message.from_user.username or "NoUsername"
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Логирование
    await channel_logger.log_action(user_id, username, f"Случайный поиск: {count} подарков")
    
    # Уменьшаем лимит (админ не расходует)
    if not is_admin and not user['is_premium']:
        new_value = user['trial_searches_left'] - count
        db.update_trial_searches(user_id, max(0, new_value))
    
    # Начало
    progress_msg = await message.answer(
        "╔═══════════════════════════╗\n"
        "║  🎲 <b>СЛУЧАЙНЫЙ ПОИСК</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        f"🎯 <b>Цель:</b> {count} подарков\n"
        f"✅ <b>Найдено:</b> 0\n\n"
        "⏳ <b>Ищу...</b>",
        reply_markup=stop_keyboard()
    )
    
    found_gifts = []
    attempts = 0
    max_attempts = count * 5
    last_found = None
    
    while len(found_gifts) < count and attempts < max_attempts:
        # Проверка остановки
        if db.should_stop_parsing(user_id):
            await progress_msg.edit_text(
                "⏹️ <b>ПОИСК ОСТАНОВЛЕН</b>\n\n"
                f"✅ Найдено: {len(found_gifts)}/{count}\n"
                f"🔍 Попыток: {attempts}\n\n"
                f"📌 Последний: {last_found}" if last_found else "",
                reply_markup=continue_keyboard()
            )
            return
        
        # Случайный выбор
        random_model = random.choice(AVAILABLE_MODELS)
        random_id = random.randint(1, 30000)
        
        # Проверка
        gift_data = await peek_gift_info(random_id, random_model)
        attempts += 1
        
        if gift_data['found']:
            last_found = f"{random_model} #{random_id}"
            found_gifts.append(gift_data)
            
            # Уведомление
            visibility = "👁️ Открыт" if gift_data.get('is_visible') else "🔒 Скрыт"
            owner = gift_data.get('username')
            owner_id = gift_data.get('user_id')
            
            gift_text = (
                "┏━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                f"┃  ✅ <b>#{random_id}</b>\n"
                "┗━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
                f"🎁 <b>Модель:</b> <code>{random_model}</code>\n"
            )
            
            if owner:
                gift_text += f"👤 <b>Владелец:</b> @{owner}\n"
            
            if owner_id:
                gift_text += f"🆔 <b>ID:</b> <code>{owner_id}</code>\n"
            
            gift_text += f"👁️ <b>Видимость:</b> {visibility}\n"
            gift_text += f"\n📊 <b>Прогресс:</b> {len(found_gifts)}/{count}"
            
            await message.answer(
                gift_text,
                reply_markup=gift_keyboard(random_id, owner, random_model)
            )
        
        # Обновление прогресса
        if attempts % 10 == 0:
            progress_percent = int((len(found_gifts) / count) * 100)
            await progress_msg.edit_text(
                "╔═══════════════════════════╗\n"
                "║  🎲 <b>СЛУЧАЙНЫЙ ПОИСК</b>   ║\n"
                "╚═══════════════════════════╝\n\n"
                f"🎯 <b>Цель:</b> {count} подарков\n"
                f"✅ <b>Найдено:</b> {len(found_gifts)}\n"
                f"🔍 <b>Попыток:</b> {attempts}\n\n"
                f"⏳ <b>Прогресс:</b> {progress_percent}%",
                reply_markup=stop_keyboard()
            )
        
        # Задержка
        for _ in range(7):
            if db.should_stop_parsing(user_id):
                break
            await asyncio.sleep(1)
    
    # Завершение
    await progress_msg.delete()
    
    result_text = (
        "╔═══════════════════════════╗\n"
        "║   ✅ <b>ПОИСК ЗАВЕРШЁН</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        f"🎯 <b>Цель:</b> {count}\n"
        f"✅ <b>Найдено:</b> {len(found_gifts)}\n"
        f"🔍 <b>Попыток:</b> {attempts}\n"
    )
    
    if last_found:
        result_text += f"\n📌 <b>Последний:</b> {last_found}"
    
    await message.answer(
        result_text,
        reply_markup=main_menu(is_premium=user['is_premium'], is_admin=is_admin)
    )

def format_gift_summary(gifts: list, model: str, start: int, end: int) -> list:
    """Форматирование итогового списка"""
    if not gifts:
        return ["Подарки не найдены."]
    
    messages = []
    current_msg = (
        "╔═══════════════════════════╗\n"
        "║   📋 <b>ИТОГОВЫЙ СПИСОК</b>  ║\n"
        "╚═══════════════════════════╝\n\n"
        f"🎁 <b>Модель:</b> <code>{model}</code>\n"
        f"📊 <b>Диапазон:</b> {start}-{end}\n"
        f"✅ <b>Найдено:</b> {len(gifts)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, gift in enumerate(gifts, 1):
        num = gift.get('giftNumber', '?')
        owner = gift.get('username', '')
        visible = "👁️" if gift.get('is_visible') else "🔒"
        
        owner_text = f"@{owner}" if owner else "Неизвестен"
        
        gift_line = f"{i}. <b>#{num}</b> | {owner_text} | {visible}\n"
        
        if len(current_msg + gift_line) > 3800:
            messages.append(current_msg)
            current_msg = f"📋 <b>Продолжение ({len(messages) + 1})</b>\n\n" + gift_line
        else:
            current_msg += gift_line
    
    if current_msg:
        messages.append(current_msg)
    
    if messages:
        messages[-1] += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n<b>Всего:</b> {len(gifts)}"
    
    return messages

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⏹️ ОСТАНОВКА / ПРОДОЛЖЕНИЕ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "stop_parsing")
async def callback_stop_parsing(callback: CallbackQuery):
    """Остановить парсинг"""
    user_id = callback.from_user.id
    db.set_stop_flag(user_id, True)
    
    await callback.answer("⏹️ Останавливаю...", show_alert=True)

@dp.callback_query(F.data == "continue_parsing")
async def callback_continue_parsing(callback: CallbackQuery):
    """Продолжить парсинг"""
    user_id = callback.from_user.id
    last_search = db.get_last_search(user_id)
    
    if not last_search:
        await callback.answer("❌ Нет сохранённого поиска", show_alert=True)
        return
    
    model = last_search['model']
    current_id = last_search['current_id']
    end = last_search['end_id']
    
    await callback.message.edit_text(
        f"▶️ <b>Продолжаю поиск...</b>\n\n"
        f"🎁 Модель: <code>{model}</code>\n"
        f"📊 ID: {current_id} → {end}\n\n"
        f"📌 <b>Последний проверенный:</b> #{current_id - 1}"
    )
    
    await start_parsing(callback.message, user_id, model, current_id, end)
    await callback.answer()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 ЗАПУСК
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main():
    """Запуск бота"""
    logger.info("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
