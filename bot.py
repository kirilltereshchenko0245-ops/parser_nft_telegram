"""
🎁 Telegram NFT Gift Parser v3.0 CLEAN
Без неулучшенных подарков, красивый дизайн
"""

import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не указан в .env")

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database()
channel_logger = ChannelLogger(bot)

print("✅ Бот инициализирован")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎯 FSM СОСТОЯНИЯ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SearchStates(StatesGroup):
    waiting_for_model = State()
    waiting_for_range = State()
    waiting_for_id = State()
    waiting_for_random_count = State()

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_trials_count = State()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⌨️ КЛАВИАТУРЫ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main_menu(is_admin: bool = False, is_premium: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню"""
    builder = ReplyKeyboardBuilder()
    
    builder.row(
        KeyboardButton(text="🔍 Начать поиск"),
        KeyboardButton(text="🎲 Случайный поиск")
    )
    builder.row(
        KeyboardButton(text="🔎 Поиск по ID"),
        KeyboardButton(text="📋 Список моделей")
    )
    builder.row(
        KeyboardButton(text="📊 Профиль"),
        KeyboardButton(text="ℹ️ Помощь")
    )
    
    if not is_premium and not is_admin:
        builder.row(KeyboardButton(text="⭐ Купить Premium"))
    
    if is_admin:
        builder.row(KeyboardButton(text="⚙️ Админка"))
    
    return builder.as_markup(resize_keyboard=True)

def cancel_keyboard() -> ReplyKeyboardMarkup:
    """Кнопка отмены"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)

def stop_keyboard() -> ReplyKeyboardMarkup:
    """Кнопка остановки"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="⏹️ Стоп"))
    return builder.as_markup(resize_keyboard=True)

def gift_inline_keyboard(gift_id: int, username: str = None, model: str = None) -> InlineKeyboardMarkup:
    """Inline кнопки для подарка"""
    builder = InlineKeyboardBuilder()
    
    if username:
        builder.row(
            InlineKeyboardButton(text="👤 Профиль", url=f"https://t.me/{username}")
        )
    
    if model and gift_id:
        safe_name = model.replace(" ", "")
        builder.row(
            InlineKeyboardButton(text="🎁 Открыть подарок", url=f"https://t.me/nft/{safe_name}-{gift_id}")
        )
    
    return builder.as_markup()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛡️ MIDDLEWARE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message.middleware()
async def check_access(handler, event: Message, data: dict):
    """Проверка доступа к боту"""
    user_id = event.from_user.id
    username = event.from_user.username or "NoUsername"
    
    # Проверка бана
    if db.is_banned(user_id):
        await event.answer("🚫 <b>Вы заблокированы</b>")
        return
    
    # Регистрация нового пользователя
    user = db.get_user(user_id)
    if not user:
        db.add_user(user_id, username)
        await channel_logger.log_action(user_id, username, "🆕 Новый пользователь")
        logger.info(f"Новый пользователь: {user_id} (@{username})")
    
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
    first_name = message.from_user.first_name or "друг"
    
    # Красивое приветствие
    welcome_text = (
        "╔═══════════════════════════╗\n"
        "║   🎁 <b>GIFT PARSER v3.0</b>   ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👋 Привет, <b>{first_name}</b>!\n\n"
        "🔍 <b>Поиск Telegram NFT подарков:</b>\n"
        "   • По модели (EternalRose, EvilEye...)\n"
        "   • По диапазону ID (100-200)\n"
        "   • Случайный поиск\n\n"
        "💎 <b>Твой статус:</b>\n"
    )
    
    if is_admin:
        welcome_text += "   👑 <b>Администратор</b> (безлимит)\n"
    elif user and user['is_premium']:
        welcome_text += "   ⭐ <b>Premium</b> (безлимит)\n"
    else:
        trials = user['trial_searches_left'] if user else 25
        welcome_text += f"   🆓 Пробная версия (<b>{trials}/25</b> поисков)\n"
    
    welcome_text += "\n📌 <b>Выбери действие в меню</b> 👇"
    
    await message.answer(
        welcome_text,
        reply_markup=main_menu(
            is_admin=is_admin,
            is_premium=user['is_premium'] if user else False
        )
    )

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    """Остановить парсинг"""
    user_id = message.from_user.id
    db.set_stop_flag(user_id, True)
    
    await message.answer(
        "⏹️ <b>Останавливаю парсинг...</b>\n\n"
        "Подожди несколько секунд"
    )

@dp.message(F.text == "⏹️ Стоп")
async def btn_stop(message: Message):
    """Кнопка Стоп"""
    await cmd_stop(message)


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    """Помощь"""
    help_text = (
        "╔═══════════════════════════╗\n"
        "║      ℹ️ <b>ИНСТРУКЦИЯ</b>      ║\n"
        "╚═══════════════════════════╝\n\n"
        "<b>🔍 Поиск по модели:</b>\n"
        "1️⃣ Нажми «🔍 Начать поиск»\n"
        "2️⃣ Введи название (eternal rose)\n"
        "3️⃣ Введи диапазон (100-200)\n"
        "4️⃣ Получи результаты!\n\n"
        "<b>🎲 Случайный поиск:</b>\n"
        "• Бот сам выбирает модели\n"
        "• Укажи количество подарков\n"
        "• Быстро найдёшь что-то интересное\n\n"
        "<b>🔎 Поиск по ID:</b>\n"
        "• Проверь конкретный ID\n"
        "• Введи ID и модель\n\n"
        "<b>💡 Регистр не важен:</b>\n"
        "✅ eternal rose = EternalRose\n"
        "✅ evil eye = EvilEye\n\n"
        "<b>📊 Лимиты:</b>\n"
        "• 🆓 Пробная: 25 поисков\n"
        "• ⭐ Premium: безлимит (5 TON)\n\n"
        "💬 <b>Поддержка:</b> @kod30"
    )
    
    await message.answer(help_text)

@dp.message(F.text == "📊 Профиль")
async def btn_profile(message: Message):
    """Профиль"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    username = message.from_user.username or "NoUsername"
    
    profile_text = (
        "╔═══════════════════════════╗\n"
        "║     📊 <b>МОЙ ПРОФИЛЬ</b>     ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👤 <b>Пользователь:</b> @{username}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
    )
    
    if is_admin:
        profile_text += "💎 <b>Статус:</b> 👑 <b>Администратор</b>\n"
    elif user and user['is_premium']:
        profile_text += "💎 <b>Статус:</b> ⭐ <b>Premium</b>\n"
    else:
        trials = user['trial_searches_left'] if user else 25
        profile_text += (
            "💎 <b>Статус:</b> 🆓 Пробная версия\n"
            f"📊 <b>Осталось поисков:</b> {trials}/25\n"
        )
    
    # Последние поиски
    searches = db.get_user_searches(user_id, limit=5)
    if searches:
        profile_text += "\n📜 <b>Последние поиски:</b>\n"
        for s in searches[:5]:
            profile_text += f"  • {s['model']} ({s['range_start']}-{s['range_end']})\n"
    
    await message.answer(profile_text)


@dp.message(F.text == "📋 Список моделей")
async def btn_models_list(message: Message):
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
        "• EternalRose, BlueStar\n"
        "• ToyBear, IceCream, SnowGlobe\n\n"
        "<b>📦 Массовые:</b>\n"
        "• FreshSocks, BerryBox\n"
        "• GingerCookie, MoneyPot\n\n"
        f"<b>Всего моделей:</b> {len(AVAILABLE_MODELS)}\n\n"
        "💡 <i>Регистр не важен при вводе</i>"
    )
    
    await message.answer(models_text)

@dp.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    """Отмена"""
    await state.clear()
    
    user_id = message.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    await message.answer(
        "❌ <b>Отменено</b>",
        reply_markup=main_menu(
            is_admin=is_admin,
            is_premium=user['is_premium'] if user else False
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔍 ПОИСК ПО МОДЕЛИ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message(F.text == "🔍 Начать поиск")
async def btn_search_start(message: Message, state: FSMContext):
    """Начать поиск"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    # Проверка лимитов (админ = безлимит)
    if not is_admin and user and not user['is_premium'] and user['trial_searches_left'] <= 0:
        await message.answer(
            "❌ <b>Пробные поиски закончились!</b>\n\n"
            "💎 Осталось: 0/25\n\n"
            "Оформи <b>Premium</b> для безлимитного доступа:\n"
            "💰 Всего <b>5 TON</b>"
        )
        return
    
    await state.set_state(SearchStates.waiting_for_model)
    
    await message.answer(
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

@dp.message(SearchStates.waiting_for_model)
async def process_model(message: Message, state: FSMContext):
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
async def process_range(message: Message, state: FSMContext):
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
        if not is_admin and user and not user['is_premium']:
            max_range = user['trial_searches_left']
            
            if actual_range > max_range:
                await message.answer(
                    f"❌ <b>Превышен лимит!</b>\n\n"
                    f"📊 Запрошено: {actual_range}\n"
                    f"💎 Доступно: {max_range}\n\n"
                    f"Макс. диапазон: <code>{start}-{start + max_range - 1}</code>"
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
            "Используй: <code>начало-конец</code>\n"
            "Например: <code>1-100</code>"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ ФУНКЦИЯ ПАРСИНГА
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
    if not is_admin and user and not user['is_premium']:
        checked_ids = end - start + 1
        new_value = user['trial_searches_left'] - checked_ids
        db.update_trial_searches(user_id, max(0, new_value))
    
    # Начало поиска
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
            await progress_msg.delete()
            
            stopped_text = (
                "⏹️ <b>ПАРСИНГ ОСТАНОВЛЕН</b>\n\n"
                f"📊 Проверено: {total_checked}/{total_range}\n"
                f"✅ Найдено: {len(found_gifts)}\n"
            )
            
            if last_found_id:
                stopped_text += f"\n📌 <b>Последний найденный:</b> #{last_found_id}"
            
            await message.answer(
                stopped_text,
                reply_markup=main_menu(is_admin=is_admin, is_premium=user['is_premium'] if user else False)
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
            
            # Уведомление о находке с красивым оформлением
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
                reply_markup=gift_inline_keyboard(gift_id, owner, model)
            )
        
        # Обновление прогресса
        progress_percent = int((total_checked / total_range) * 100)
        if total_checked % max(1, total_range // 10) == 0:
            try:
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
            except:
                pass  # Игнорируем ошибки редактирования
        
        # Задержка с проверкой остановки (7 секунд)
        for _ in range(7):
            if db.should_stop_parsing(user_id):
                break
            await asyncio.sleep(1)
    
    # Завершение - удаляем кнопку Стоп
    try:
        await progress_msg.delete()
    except:
        pass
    
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
        reply_markup=main_menu(is_admin=is_admin, is_premium=user['is_premium'] if user else False)
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
        
        owner_text = f"@{owner}" if owner else "Скрыт"
        
        gift_line = f"{i}. <b>#{num}</b> | {owner_text} {visible}\n"
        
        # Проверка лимита Telegram (4096 символов)
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
# 🎲 СЛУЧАЙНЫЙ ПОИСК
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message(F.text == "🎲 Случайный поиск")
async def btn_random_search(message: Message, state: FSMContext):
    """Случайный поиск"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    # Проверка лимитов
    if not is_admin and user and not user['is_premium'] and user['trial_searches_left'] <= 0:
        await message.answer(
            "❌ <b>Пробные поиски закончились!</b>\n\n"
            "Оформи Premium для продолжения"
        )
        return
    
    await state.set_state(SearchStates.waiting_for_random_count)
    
    max_count = user['trial_searches_left'] if (user and not is_admin and not user['is_premium']) else 1000
    
    await message.answer(
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

@dp.message(SearchStates.waiting_for_random_count)
async def process_random_count(message: Message, state: FSMContext):
    """Обработка количества"""
    try:
        count = int(message.text.strip())
        
        user_id = message.from_user.id
        user = db.get_user(user_id)
        is_admin = (user_id == OWNER_ID)
        
        max_count = user['trial_searches_left'] if (user and not is_admin and not user['is_premium']) else 1000
        
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
    
    # Уменьшаем лимит
    if not is_admin and user and not user['is_premium']:
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
            await progress_msg.delete()
            
            stopped_text = (
                "⏹️ <b>ПОИСК ОСТАНОВЛЕН</b>\n\n"
                f"✅ Найдено: {len(found_gifts)}/{count}\n"
                f"🔍 Попыток: {attempts}\n"
            )
            
            if last_found:
                stopped_text += f"\n📌 <b>Последний:</b> {last_found}"
            
            await message.answer(
                stopped_text,
                reply_markup=main_menu(is_admin=is_admin, is_premium=user['is_premium'] if user else False)
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
                reply_markup=gift_inline_keyboard(random_id, owner, random_model)
            )
        
        # Обновление прогресса
        if attempts % 10 == 0:
            progress_percent = int((len(found_gifts) / count) * 100)
            try:
                await progress_msg.edit_text(
                    "╔═══════════════════════════╗\n"
                    "║  🎲 <b>СЛУЧАЙНЫЙ ПОИСК</b>   ║\n"
                    "╚═══════════════════════════╝\n\n"
                    f"🎯 <b>Цель:</b> {count}\n"
                    f"✅ <b>Найдено:</b> {len(found_gifts)}\n"
                    f"🔍 <b>Попыток:</b> {attempts}\n\n"
                    f"⏳ <b>Прогресс:</b> {progress_percent}%",
                    reply_markup=stop_keyboard()
                )
            except:
                pass
        
        # Задержка
        for _ in range(7):
            if db.should_stop_parsing(user_id):
                break
            await asyncio.sleep(1)
    
    # Завершение - удаляем кнопку Стоп
    try:
        await progress_msg.delete()
    except:
        pass
    
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
        reply_markup=main_menu(is_admin=is_admin, is_premium=user['is_premium'] if user else False)
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 ЗАПУСК БОТА
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main():
    """Запуск бота"""
    logger.info("🚀 Бот запускается...")
    logger.info(f"👑 Владелец: {OWNER_ID}")
    logger.info(f"📢 Канал логов: {LOG_CHANNEL_ID}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    print("\n" + "="*40)
    print("🎁 GIFT PARSER v3.0 CLEAN")
    print("="*40 + "\n")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
