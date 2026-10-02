import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from database import Database
from peek_api import peek_gift_info, AVAILABLE_MODELS, format_summary, format_random_summary, normalize_model_name
from keyboards import (
    main_menu_keyboard, admin_menu_keyboard, cancel_keyboard,
    gift_result_keyboard, models_list_keyboard, payment_method_keyboard,
    stop_parsing_keyboard, continue_parsing_keyboard, profile_keyboard,
    ban_user_keyboard, admin_settings_keyboard, search_filter_keyboard,
    stop_search_keyboard
)
from payments import PaymentManager
from logger_channel import ChannelLogger

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не указан в .env")

# ID владельца (ваш Telegram ID) - можно переопределить через .env
OWNER_ID = int(os.getenv("OWNER_ID", "6969906545"))  # Замените на ваш реальный ID

# Инициализация
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database()
payment_manager = PaymentManager()
channel_logger = ChannelLogger(bot)

# FSM States
class SearchStates(StatesGroup):
    waiting_for_model = State()
    waiting_for_range = State()
    waiting_for_id = State()
    waiting_for_filter_choice = State()  # Выбор фильтра (все/только неулучшенные)
    
    # Случайный поиск
    waiting_for_random_count = State()
    waiting_for_random_filter = State()

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_trials_count = State()

# === MIDDLEWARE: Проверка бана и доступа ===
@dp.message.middleware()
async def check_access_middleware(handler, event: Message, data: dict):
    """Проверка доступа к боту"""
    user_id = event.from_user.id
    user = db.get_user(user_id)
    
    # Проверка бана
    if user and user['is_banned']:
        await event.answer("❌ Вы заблокированы в боте.")
        return
    
    # Проверка режима доступа
    settings = db.get_bot_settings()
    if settings['access_mode'] == 'owner_only' and user_id != OWNER_ID:
        await event.answer("🔒 Бот доступен только для владельца.")
        return
    
    return await handler(event, data)

# === КОМАНДЫ ===
@dp.message(F.text == "❌ Отмена")
async def cancel_handler(message: Message, state: FSMContext):
    """Универсальный обработчик отмены"""
    current_state = await state.get_state()
    
    if current_state is None:
        return
    
    await state.clear()
    
    user = db.get_user(message.from_user.id)
    is_admin = (message.from_user.id == OWNER_ID)
    
    # Если были в админке - возвращаем в админку
    if "Admin" in str(current_state):
        await message.answer(
            "❌ Отменено",
            reply_markup=admin_menu_keyboard()
        )
    else:
        # Иначе в главное меню
        await message.answer(
            "❌ Отменено",
            reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
        )

@dp.message(Command("start"))
async def start_command(message: Message):
    """Начало работы"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Регистрация или обновление пользователя
    user = db.get_user(user_id)
    if not user:
        db.add_user(user_id, username, first_name)
        await channel_logger.log_registration(user_id, username, first_name)
    else:
        db.update_user_info(user_id, username, first_name)
    
    # Проверка прав
    is_admin = (user_id == OWNER_ID)
    user_data = db.get_user(user_id)
    is_premium = user_data['is_premium'] if user_data else False
    is_nonupgraded_premium = user_data.get('is_nonupgraded_premium', False) if user_data else False
    
    text = (
        f"👋 Привет, <b>{first_name}</b>!\n\n"
        "🎁 <b>Gift Parser</b> - поиск NFT подарков в Telegram\n\n"
        "<b>Возможности:</b>\n"
        "🔍 Поиск по диапазону ID\n"
        "🔎 Поиск конкретного подарка\n"
        "📋 Список всех моделей\n"
        "📊 Статистика поисков\n\n"
    )
    
    if not is_premium:
        trials_left = user_data['trial_searches_left'] if user_data else 25
        text += f"🎫 <b>Пробных поисков:</b> {trials_left}/25\n\n"
    else:
        text += "⭐ <b>Premium подписка активна!</b>\n\n"
    
    text += "💬 <b>Поддержка:</b> @kod30"
    
    await message.answer(
        text,
        reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=is_premium, is_nonupgraded_premium=is_nonupgraded_premium)
    )

@dp.message(Command("stop"))
async def stop_command(message: Message):
    """Остановить текущий парсинг"""
    user_id = message.from_user.id
    db.set_stop_flag(user_id, True)
    
    await message.answer(
        "⏹️ <b>Парсинг будет остановлен</b>\n\n"
        "Нажмите <b>▶️ Продолжить</b> чтобы возобновить:",
        reply_markup=continue_parsing_keyboard()
    )

@dp.message(F.text == "⏹️ Стоп")
async def stop_button_handler(message: Message):
    """Обработчик кнопки Стоп"""
    await stop_command(message)

@dp.message(Command("help"))
async def help_command(message: Message):
    """Помощь"""
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>Как пользоваться:</b>\n\n"
        "🔍 <b>Поиск по модели:</b>\n"
        "1. Нажмите <b>🔍 Начать поиск</b>\n"
        "2. Введите модель (например: Eternal Rose)\n"
        "3. Введите диапазон через тире (10-50)\n"
        "4. Дождитесь результатов!\n\n"
        "🎲 <b>Случайный поиск:</b>\n"
        "1. Нажмите <b>🎲 Случайные подарки</b>\n"
        "2. Введите количество (например: 50)\n"
        "3. Выберите фильтр (все/неулучшенные)\n"
        "4. Бот найдёт случайные подарки из разных моделей!\n\n"
        "<b>Регистр и пробелы:</b>\n"
        "✅ eternal rose = EternalRose\n"
        "✅ evil eye = EvilEye\n"
        "✅ blue star = BlueStar\n\n"
        "<b>Команды:</b>\n"
        "/start - главное меню\n"
        "/stop - остановить парсинг\n"
        "/help - эта справка\n\n"
        "💬 <b>Поддержка:</b> @kod30"
    )
    await message.answer(text)

# === НАЧАТЬ ПОИСК ===
@dp.message(F.text == "🔍 Начать поиск")
async def search_start(message: Message, state: FSMContext):
    """Начало поиска"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    # Проверка лимитов (админ имеет безлимит)
    if user_id != OWNER_ID and not user['is_premium'] and user['trial_searches_left'] <= 0:
        await message.answer(
            "❌ <b>Пробные поиски закончились!</b>\n\n"
            f"💎 Осталось: {user['trial_searches_left']}/25\n\n"
            "Для продолжения работы оформите <b>Premium подписку</b>:\n"
            f"💰 Стоимость: <b>5 TON</b>\n\n"
            "Выберите способ оплаты:",
            reply_markup=payment_method_keyboard()
        )
        return
    
    await state.set_state(SearchStates.waiting_for_model)
    await message.answer(
        "🎁 <b>Введите модель подарка</b>\n\n"
        "Например: <code>Eternal Rose</code> или <code>Evil Eye</code>\n\n"
        "📋 Список доступных: /models",
        reply_markup=cancel_keyboard()
    )

# === СЛУЧАЙНЫЙ ПОИСК ===
@dp.message(F.text == "🎲 Случайные подарки")
async def random_search_start(message: Message, state: FSMContext):
    """Начало случайного поиска"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    # Проверка лимитов (админ имеет безлимит)
    if user_id != OWNER_ID and not user['is_premium'] and user['trial_searches_left'] <= 0:
        await message.answer(
            "❌ <b>Пробные поиски закончились!</b>\n\n"
            f"💎 Осталось: {user['trial_searches_left']}/25\n\n"
            "Для продолжения работы оформите <b>Premium подписку</b>:\n"
            f"💰 Стоимость: <b>5 TON</b>\n\n"
            "Выберите способ оплаты:",
            reply_markup=payment_method_keyboard()
        )
        return
    
    await state.set_state(SearchStates.waiting_for_random_count)
    
    max_count = user['trial_searches_left'] if not user['is_premium'] else 1000
    
    await message.answer(
        "🎲 <b>Случайный поиск подарков</b>\n\n"
        f"Введите количество подарков для поиска (1-{max_count}):\n\n"
        "Например: <code>50</code> - найдёт 50 случайных подарков",
        reply_markup=cancel_keyboard()
    )

@dp.message(SearchStates.waiting_for_random_count)
async def random_count_entered(message: Message, state: FSMContext):
    """Обработка количества для случайного поиска"""
    if message.text == "❌ Отмена":
        await state.clear()
        user = db.get_user(message.from_user.id)
        is_admin = (message.from_user.id == OWNER_ID)
        await message.answer(
            "❌ Поиск отменён",
            reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
        )
        return
    
    try:
        count = int(message.text.strip())
        
        user_id = message.from_user.id
        user = db.get_user(user_id)
        
        # Проверка лимитов
        max_count = user['trial_searches_left'] if not user['is_premium'] else 1000
        
        if count < 1:
            await message.answer("❌ Количество должно быть больше 0!")
            return
        
        if count > max_count:
            await message.answer(
                f"❌ <b>Превышен лимит!</b>\n\n"
                f"💎 Доступно: {max_count}\n"
                f"📝 Вы запросили: {count}\n\n"
                f"{'Оформите Premium для безлимитного доступа!' if not user['is_premium'] else ''}"
            )
            return
        
        await state.update_data(random_count=count)
        await state.set_state(SearchStates.waiting_for_random_filter)
        
        await message.answer(
            "🎲 <b>Выберите тип подарков:</b>\n\n"
            f"Будет найдено до <b>{count}</b> подарков",
            reply_markup=search_filter_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Введите число! Например: <code>50</code>")


@dp.message(SearchStates.waiting_for_model)
async def search_model_entered(message: Message, state: FSMContext):
    """Обработка введённой модели"""
    if message.text == "❌ Отмена":
        await state.clear()
        user = db.get_user(message.from_user.id)
        is_admin = (message.from_user.id == OWNER_ID)
        await message.answer(
            "❌ Поиск отменён",
            reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
        )
        return
    
    # Нормализация названия через маппинг
    model_name = normalize_model_name(message.text)
    
    await state.update_data(model=model_name)
    await state.set_state(SearchStates.waiting_for_range)
    
    await message.answer(
        f"✅ Модель: <b>{model_name}</b>\n\n"
        "📊 <b>Введите диапазон ID</b>\n\n"
        "Формат: <code>начало-конец</code>\n"
        "Пример: <code>1-100</code>",
        reply_markup=cancel_keyboard()
    )

@dp.message(SearchStates.waiting_for_range)
async def search_range_entered(message: Message, state: FSMContext):
    """Обработка диапазона"""
    if message.text == "❌ Отмена":
        await state.clear()
        user = db.get_user(message.from_user.id)
        is_admin = (message.from_user.id == OWNER_ID)
        await message.answer(
            "❌ Поиск отменён",
            reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
        )
        return
    
    # Парсинг диапазона
    try:
        range_text = message.text.strip()
        start, end = map(int, range_text.split("-"))
        
        if start >= end or start < 1:
            await message.answer("❌ Некорректный диапазон! Попробуйте снова (например: 1-100)")
            return
        
        if end - start > 10000:
            await message.answer("❌ Максимальный диапазон: 10000 ID")
            return
        
        # Проверка лимитов для пробных пользователей
        user = db.get_user(message.from_user.id)
        if not user['is_premium']:
            max_range = user['trial_searches_left']
            actual_range = end - start + 1
            
            if actual_range > max_range:
                await message.answer(
                    f"❌ <b>Превышен лимит!</b>\n\n"
                    f"📊 Вы хотите проверить: <b>{actual_range} ID</b>\n"
                    f"🎫 У вас осталось: <b>{max_range} пробных запросов</b>\n\n"
                    f"Максимальный диапазон: <code>{start}-{start + max_range - 1}</code>\n\n"
                    f"Для безлимитных поисков оформите Premium подписку!"
                )
                return
        
    except ValueError:
        await message.answer("❌ Неверный формат! Используйте: начало-конец (например: 1-100)")
        return
    
    # Сохраняем диапазон
    await state.update_data(range_start=start, range_end=end)
    
    # Спрашиваем фильтр
    await state.set_state(SearchStates.waiting_for_filter_choice)
    
    await message.answer(
        "🔍 <b>Выберите фильтр:</b>\n\n"
        "🎁 <b>Все подарки</b> - обычная подписка\n"
        "💎 <b>Только неулучшенные</b> - специальная подписка (5 TON)",
        reply_markup=search_filter_keyboard()
    )

async def start_random_parsing(message: Message, user_id: int, count: int, only_nonupgraded: bool = False):
    """Случайный парсинг подарков из разных моделей"""
    import random
    from peek_api import AVAILABLE_MODELS
    
    found_gifts = []
    total_checked = 0
    skipped_upgraded = 0
    
    progress_msg = await message.answer("⏳ <b>Прогресс:</b> 0%")
    
    attempts = 0
    max_attempts = count * 3  # Максимум попыток (в 3 раза больше чем нужно найти)
    
    while len(found_gifts) < count and attempts < max_attempts:
        # Проверка флага остановки каждые 7 секунд
        if db.should_stop_parsing(user_id):
            await progress_msg.edit_text(
                "⏹️ <b>Парсинг остановлен!</b>\n\n"
                f"Попыток: {attempts}\n"
                f"Найдено: {len(found_gifts)}/{count}"
            )
            break
        
        # Случайная модель и ID
        random_model = random.choice(AVAILABLE_MODELS)
        random_id = random.randint(1, 30000)  # Диапазон подарков
        
        # Проверка подарка
        gift_data = await peek_gift_info(random_id, random_model)
        attempts += 1
        
        if gift_data['found']:
            # Фильтр неулучшенных
            if only_nonupgraded and gift_data.get('is_upgraded'):
                skipped_upgraded += 1
                total_checked += 1
                continue
            
            total_checked += 1
            found_gifts.append(gift_data)
            
            # Отправляем уведомление о находке
            visibility = "👁️ Открыт" if gift_data.get('is_visible') else "🔒 Скрыт"
            upgraded = "⭐ Улучшен" if gift_data.get('is_upgraded') else "🎁 Обычный"
            
            result_text = (
                f"✅ <b>Найден подарок #{random_id}</b>\n\n"
                f"🎁 Модель: <code>{random_model}</code>\n"
                f"🎯 Статус: {upgraded}\n"
            )
            
            # Информация о владельце
            username = gift_data.get('username')
            owner_user_id = gift_data.get('user_id')
            
            if username:
                result_text += f"👤 Владелец: @{username}\n"
            
            if owner_user_id:
                result_text += f"🆔 ID владельца: <code>{owner_user_id}</code>\n"
            elif not username:
                result_text += f"👤 Владелец: Неизвестен\n"
            
            result_text += f"👁️ Видимость: {visibility}\n"
            
            if 'rarity' in gift_data:
                result_text += f"💎 Редкость: <b>{gift_data['rarity']}</b>\n"
            
            result_text += f"\n📊 Прогресс: {len(found_gifts)}/{count}"
            
            await message.answer(
                result_text,
                reply_markup=gift_result_keyboard(
                    random_id,
                    gift_data.get('username'),
                    random_model
                )
            )
        
        # Обновление прогресса
        progress_percent = int((len(found_gifts) / count) * 100)
        await progress_msg.edit_text(
            f"⏳ <b>Прогресс:</b> {progress_percent}%\n"
            f"✅ Найдено: {len(found_gifts)}/{count}\n"
            f"🔍 Попыток: {attempts}"
        )
        
        # Задержка 7 секунд с проверкой остановки каждую секунду
        for _ in range(7):
            if db.should_stop_parsing(user_id):
                break
            await asyncio.sleep(1)
    
    # Удаляем сообщение прогресса
    await progress_msg.delete()
    
    # Итоговый список (может быть несколько сообщений)
    if found_gifts:
        # Группируем по моделям для красивого вывода
        summary_messages = format_random_summary(found_gifts, count)
        
        for summary_part in summary_messages:
            await message.answer(summary_part)
        
        result_text = f"✅ <b>Случайный поиск завершён!</b>\n\n"
        result_text += f"🔍 Попыток: {attempts}\n"
        result_text += f"✅ Найдено: {len(found_gifts)}/{count}\n"
        
        if only_nonupgraded and skipped_upgraded > 0:
            result_text += f"⏭️ Пропущено улучшенных: {skipped_upgraded}\n"
        
        await message.answer(result_text)
    else:
        await message.answer(
            "❌ <b>Подарки не найдены</b>\n\n"
            f"Попыток: {attempts}"
        )
    
    # Вернуть главное меню
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    await message.answer(
        "Выберите действие:",
        reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
    )

async def start_parsing(message: Message, user_id: int, model: str, start: int, end: int, search_id: int, only_nonupgraded: bool = False):
    """Основной процесс парсинга"""
    found_gifts = []
    total_checked = 0
    skipped_upgraded = 0
    
    progress_msg = await message.answer("⏳ <b>Прогресс:</b> 0%")
    
    total_range = end - start + 1
    
    for gift_id in range(start, end + 1):
        # Проверка флага остановки
        if db.should_stop_parsing(user_id):
            await progress_msg.edit_text(
                "⏹️ <b>Парсинг остановлен!</b>\n\n"
                f"Проверено: {total_checked}/{total_range}\n"
                f"Найдено: {len(found_gifts)}",
                reply_markup=continue_parsing_keyboard()
            )
            # Сохраняем состояние для продолжения
            db.update_last_search(user_id, model, gift_id, end)
            return
        
        # Проверка подарка
        gift_data = await peek_gift_info(gift_id, model)
        total_checked += 1
        
        if gift_data['found']:
            # DEBUG: Логирование для отладки фильтра
            import logging
            logging.info(f"Gift {gift_id}: is_upgraded={gift_data.get('is_upgraded')}, only_nonupgraded={only_nonupgraded}")
            
            # Фильтр неулучшенных
            if only_nonupgraded and gift_data.get('is_upgraded'):
                skipped_upgraded += 1
                continue
            
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
            
            # Отправляем уведомление
            visibility = "👁️ Открыт" if gift_data.get('is_visible') else "🔒 Скрыт"
            upgraded = "⭐ Улучшен" if gift_data.get('is_upgraded') else "🎁 Обычный"
            
            result_text = (
                f"✅ <b>Найден подарок #{gift_id}</b>\n\n"
                f"🎁 Модель: <code>{model}</code>\n"
                f"� Статус: {upgraded}\n"
            )
            
            # Информация о владельце
            username = gift_data.get('username')
            user_id = gift_data.get('user_id')
            
            if username:
                result_text += f"👤 Владелец: @{username}\n"
            
            if user_id:
                result_text += f"🆔 ID владельца: <code>{user_id}</code>\n"
            elif not username:
                result_text += f"👤 Владелец: Неизвестен\n"
            
            result_text += f"👁️ Видимость: {visibility}\n"
            
            if 'rarity' in gift_data:
                result_text += f"💎 Редкость: <b>{gift_data['rarity']}</b>\n"
            
            await message.answer(
                result_text,
                reply_markup=gift_result_keyboard(
                    gift_id,
                    gift_data.get('username'),
                    model
                )
            )
        
        # Обновление прогресса каждые 10%
        progress_percent = int((total_checked / total_range) * 100)
        if total_checked % max(1, total_range // 10) == 0:
            try:
                progress_text = (
                    f"⏳ <b>Прогресс:</b> {progress_percent}%\n"
                    f"Проверено: {total_checked}/{total_range}\n"
                    f"Найдено: {len(found_gifts)}"
                )
                if only_nonupgraded and skipped_upgraded > 0:
                    progress_text += f"\n⏭️ Пропущено улучшенных: {skipped_upgraded}"
                
                await progress_msg.edit_text(progress_text)
            except:
                pass
        
        # Задержка 7 секунд (против нагрузки на API)
        # Проверяем флаг остановки каждую секунду во время задержки
        for _ in range(7):
            if db.should_stop_parsing(user_id):
                await progress_msg.edit_text(
                    "⏹️ <b>Парсинг остановлен!</b>\n\n"
                    f"Проверено: {total_checked}/{total_range}\n"
                    f"Найдено: {len(found_gifts)}",
                    reply_markup=continue_parsing_keyboard()
                )
                db.update_last_search(user_id, model, gift_id + 1, end)
                
                # Главное меню
                user = db.get_user(user_id)
                is_admin = (user_id == OWNER_ID)
                await message.answer(
                    "Выберите действие:",
                    reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
                )
                return
            await asyncio.sleep(1)
    
    # Завершение
    await progress_msg.delete()
    
    # Итоговый список (может быть несколько сообщений)
    if found_gifts:
        summary_messages = format_summary(found_gifts, model)
        
        # Отправляем все части итогового списка
        for summary_part in summary_messages:
            await message.answer(summary_part)
        
        result_text = f"✅ <b>Поиск завершён!</b>\n\n"
        result_text += f"📊 Проверено: {total_checked}\n"
        result_text += f"✅ Найдено: {len(found_gifts)}\n"
        
        if only_nonupgraded and skipped_upgraded > 0:
            result_text += f"⏭️ Пропущено улучшенных: {skipped_upgraded}\n"
        
        await message.answer(result_text)
    else:
        result_text = f"❌ <b>Поиск завершён</b>\n\n"
        result_text += f"📊 Проверено: {total_checked}\n"
        
        if only_nonupgraded and skipped_upgraded > 0:
            result_text += f"⏭️ Пропущено улучшенных: {skipped_upgraded}\n"
            result_text += f"❌ Неулучшенных не найдено\n"
        else:
            result_text += f"❌ Ничего не найдено"
        
        await message.answer(result_text)
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Главное меню
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    await message.answer(
        "Выберите действие:",
        reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
    )

# === ПОИСК ПО ID ===
@dp.message(F.text == "🔎 Поиск по ID")
async def search_by_id_start(message: Message, state: FSMContext):
    """Поиск конкретного подарка по ID"""
    await state.set_state(SearchStates.waiting_for_id)
    await message.answer(
        "🔎 <b>Поиск по ID</b>\n\n"
        "Введите в формате: <code>модель-номер</code>\n"
        "Пример: <code>EternalRose-13000</code>",
        reply_markup=cancel_keyboard()
    )

@dp.message(SearchStates.waiting_for_id)
async def search_by_id_process(message: Message, state: FSMContext):
    """Обработка поиска по ID"""
    if message.text == "❌ Отмена":
        await state.clear()
        user = db.get_user(message.from_user.id)
        is_admin = (message.from_user.id == OWNER_ID)
        await message.answer(
            "❌ Поиск отменён",
            reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
        )
        return
    
    try:
        # Парсинг
        parts = message.text.strip().split("-")
        if len(parts) != 2:
            raise ValueError()
        
        # Нормализация названия модели через маппинг
        model = normalize_model_name(parts[0].strip())
        gift_id = int(parts[1])
        
        if gift_id < 1:
            raise ValueError()
        
    except ValueError:
        await message.answer("❌ Неверный формат! Используйте: модель-номер (например: EternalRose-100)")
        return
    
    await state.clear()
    
    # Поиск
    msg = await message.answer(f"🔍 Ищу подарок <code>{model}-{gift_id}</code>...")
    
    gift_data = await peek_gift_info(gift_id, model)
    
    if gift_data['found']:
        visibility = "👁️ Открыт" if gift_data.get('is_visible') else "🔒 Скрыт"
        upgraded = "⭐ Улучшен" if gift_data.get('is_upgraded') else "🎁 Обычный"
        
        result_text = (
            f"✅ <b>Подарок найден!</b>\n\n"
            f"🎁 ID: <code>{model}-{gift_id}</code>\n"
            f"� Статус: {upgraded}\n"
        )
        
        # Информация о владельце
        username = gift_data.get('username')
        user_id = gift_data.get('user_id')
        
        if username:
            result_text += f"👤 Владелец: @{username}\n"
        
        if user_id:
            result_text += f"🆔 ID владельца: <code>{user_id}</code>\n"
        elif not username:
            result_text += f"👤 Владелец: Неизвестен\n"
        
        result_text += f"👁️ Видимость: {visibility}\n"
        
        if 'rarity' in gift_data:
            result_text += f"💎 Редкость: <b>{gift_data['rarity']}</b>\n"
        
        await msg.edit_text(
            result_text,
            reply_markup=gift_result_keyboard(
                gift_id,
                gift_data.get('username'),
                model
            )
        )
    else:
        await msg.edit_text(
            f"❌ <b>Подарок не найден</b>\n\n"
            f"🎁 ID: <code>{model}-{gift_id}</code>\n\n"
            "Возможные причины:\n"
            "• Подарок не существует\n"
            "• Неверное название модели\n"
            "• Технические проблемы API"
        )
    
    # Вернуть главное меню после результата
    user = db.get_user(message.from_user.id)
    is_admin = (message.from_user.id == OWNER_ID)
    await message.answer(
        "Выберите действие:",
        reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
    )

# === СПИСОК ПОДАРКОВ ===
@dp.message(F.text == "📋 Список подарков")
async def models_list_command(message: Message):
    """Показать список моделей с ценами"""
    text = "📋 <b>Доступные модели подарков:</b>\n\n"
    
    text += "💎 <b>Топ дорогие:</b>\n"
    text += "• <code>DurovsCap</code> - ~500-1000 TON | 50 шт\n"
    text += "• <code>HeroicHelmet</code> - ~200-500 TON | 100 шт\n"
    text += "• <code>MightyArm</code> - ~150-300 TON | 200 шт\n\n"
    
    text += "🔥 <b>Редкие:</b>\n"
    text += "• <code>PreciousPeach</code> - ~50-100 TON | 500 шт\n"
    text += "• <code>PlushPepe</code> - ~30-80 TON | 1000 шт\n"
    text += "• <code>IonGem</code> - ~20-50 TON | 2000 шт\n\n"
    
    text += "⭐ <b>Средние:</b>\n"
    text += "• <code>EvilEye</code> - ~5-15 TON | 5000 шт\n"
    text += "• <code>GenieLamp</code> - ~5-15 TON | 5000 шт\n"
    text += "• <code>SignetRing</code> - ~3-10 TON | 10000 шт\n\n"
    
    text += "🎁 <b>Обычные:</b>\n"
    text += "• <code>ToyBear</code> - ~1-3 TON | 20000 шт\n"
    text += "• <code>IceCream</code> - ~1-3 TON | 20000 шт\n"
    text += "• <code>SnowGlobe</code> - ~1-2 TON | 50000 шт\n\n"
    
    text += "📦 <b>Массовые:</b>\n"
    text += "• <code>FreshSocks</code> - ~0.1-0.5 TON | 100k+ шт\n"
    text += "• <code>BerryBox</code> - ~0.1-0.5 TON | 100k+ шт\n"
    text += "• <code>GingerCookie</code> - ~0.1-0.3 TON | 200k+ шт\n\n"
    
    text += f"<b>Всего моделей:</b> {len(AVAILABLE_MODELS)}\n\n"
    
    text += "💡 <b>Подсказка:</b>\n"
    text += "Можно писать с пробелами или без:\n"
    text += "• evil eye = EvilEye ✅\n"
    text += "• precious peach = PreciousPeach ✅"
    
    await message.answer(text)

# === ПРОФИЛЬ ===
@dp.message(F.text == "📊 Профиль")
async def profile_command(message: Message):
    """Показать профиль пользователя"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Ошибка: пользователь не найден")
        return
    
    user_stats = db.get_user_stats(user_id)
    
    premium_status = "⭐ Premium" if user['is_premium'] else f"🎫 Trial ({user['trial_searches_left']}/25)"
    
    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Username: @{user.get('username', 'не указан')}\n"
        f"📅 Регистрация: {user['registration_date'][:10]}\n"
        f"💎 Статус: {premium_status}\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"🔍 Всего поисков: <b>{user_stats['total_searches']}</b>\n"
        f"✅ Найдено подарков: <b>{user_stats['total_gifts_found']}</b>\n"
    )
    
    # Последний поиск
    last_search = db.get_last_search(user_id)
    if last_search:
        text += (
            f"\n🔄 <b>Последний поиск:</b>\n"
            f"🎁 {last_search['model']}\n"
            f"📊 {last_search['start_id']}-{last_search['end_id']}\n"
            f"✅ Найдено: {last_search['found_count']}\n"
        )
    
    await message.answer(text, reply_markup=profile_keyboard())

# === ПРОДОЛЖИТЬ ===
@dp.message(F.text == "⏭️ Продолжить")
async def continue_last_search(message: Message):
    """Продолжить последний поиск"""
    user_id = message.from_user.id
    last_search = db.get_last_search(user_id)
    
    if not last_search:
        await message.answer("❌ Нет предыдущих поисков для продолжения")
        return
    
    await message.answer(
        f"🔄 <b>Продолжить поиск?</b>\n\n"
        f"🎁 Модель: <code>{last_search['model']}</code>\n"
        f"📊 Диапазон: {last_search['range_start']}-{last_search['range_end']}\n"
        f"✅ Найдено: {last_search['found_count']}",
        reply_markup=continue_parsing_keyboard()
    )

# === CALLBACK: Выбор фильтра поиска ===
@dp.callback_query(F.data == "filter_all")
async def callback_filter_all(callback: CallbackQuery, state: FSMContext):
    """Выбран фильтр: все подарки"""
    data = await state.get_data()
    model = data['model']
    start = data['range_start']
    end = data['range_end']
    
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Логирование начала поиска
    await channel_logger.log_search(user_id, username, model, start, end)
    
    # Сохраняем поиск в историю
    search_id = db.add_search(user_id, model, start, end)
    
    # Уменьшаем количество пробных поисков (админ не расходует)
    user = db.get_user(user_id)
    if user_id != OWNER_ID and not user['is_premium']:
        checked_ids = end - start + 1
        new_value = user['trial_searches_left'] - checked_ids
        db.update_trial_searches(user_id, max(0, new_value))
    
    await state.clear()
    
    await callback.answer()  # Ответ сразу!
    
    # Начинаем парсинг
    await callback.message.edit_text(
        f"🔍 <b>Начинаю поиск...</b>\n\n"
        f"🎁 Модель: <code>{model}</code>\n"
        f"📊 Диапазон: <code>{start}-{end}</code>\n"
        f"🔍 Фильтр: <b>Все подарки</b>\n\n"
        f"⏱️ Это может занять некоторое время...\n"
        f"Для остановки нажмите кнопку <b>⏹️ Стоп</b>"
    )
    
    # Показываем кнопку Стоп на клавиатуре
    await callback.message.answer(
        "⏹️ Для остановки:",
        reply_markup=stop_search_keyboard()
    )
    
    # Запускаем парсинг
    await start_parsing(callback.message, user_id, model, start, end, search_id, only_nonupgraded=False)

@dp.callback_query(F.data == "filter_nonupgraded")
async def callback_filter_nonupgraded(callback: CallbackQuery, state: FSMContext):
    """Выбран фильтр: только неулучшенные"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    
    data = await state.get_data()
    start = data['range_start']
    end = data['range_end']
    actual_range = end - start + 1
    
    # Проверка лимитов для неулучшенных (админ = безлимит)
    if user_id != OWNER_ID and not user['is_nonupgraded_premium']:
        max_range = user['nonupgraded_searches_left']
        
        if max_range <= 0:
            await callback.message.edit_text(
                "❌ <b>Пробные поиски неулучшенных закончились!</b>\n\n"
                f"💎 Осталось: 0/3\n\n"
                "Для продолжения работы оформите <b>подписку на неулучшенные</b>:\n"
                f"💰 Стоимость: <b>5 TON</b>\n\n"
                "⚠️ <b>Внимание:</b> Эта подписка доступна только при наличии обычной Premium подписки!\n\n"
                "Выберите способ оплаты:",
                reply_markup=payment_method_keyboard(is_nonupgraded=True)
            )
            await state.clear()
            await callback.answer()
            return
        
        if actual_range > max_range:
            await callback.message.edit_text(
                f"❌ <b>Превышен лимит неулучшенных!</b>\n\n"
                f"📊 Вы хотите проверить: <b>{actual_range} ID</b>\n"
                f"💎 У вас осталось: <b>{max_range} запросов</b>\n\n"
                f"Максимальный диапазон: <code>{start}-{start + max_range - 1}</code>\n\n"
                f"Для безлимитных поисков неулучшенных оформите подписку!"
            )
            await state.clear()
            await callback.answer()
            return
    
    # Проверка наличия обычной подписки
    if not user['is_premium']:
        await callback.message.edit_text(
            "❌ <b>Требуется Premium подписка!</b>\n\n"
            "Подписка на неулучшенные подарки доступна только\n"
            "при наличии обычной Premium подписки.\n\n"
            "Сначала оформите Premium (5 TON), затем\n"
            "сможете купить доступ к неулучшенным (+5 TON).",
            reply_markup=payment_method_keyboard(is_nonupgraded=False)
        )
        await state.clear()
        await callback.answer()
        return
    
    model = data['model']
    start = data['range_start']
    end = data['range_end']
    
    username = callback.from_user.username
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Логирование начала поиска
    await channel_logger.log_search(user_id, username, model, start, end)
    
    # Сохраняем поиск в историю
    search_id = db.add_search(user_id, model, start, end)
    
    # Уменьшаем количество поисков неулучшенных (админ не расходует)
    if user_id != OWNER_ID and not user['is_nonupgraded_premium']:
        checked_ids = end - start + 1
        new_value = user['nonupgraded_searches_left'] - checked_ids
        db.update_nonupgraded_searches(user_id, max(0, new_value))
    
    await state.clear()
    
    await callback.answer()  # Ответ сразу!
    
    # Начинаем парсинг
    await callback.message.edit_text(
        f"🔍 <b>Начинаю поиск...</b>\n\n"
        f"🎁 Модель: <code>{model}</code>\n"
        f"📊 Диапазон: <code>{start}-{end}</code>\n"
        f"💎 Фильтр: <b>Только неулучшенные</b>\n\n"
        f"⏱️ Это может занять некоторое время...\n"
        f"Для остановки нажмите кнопку <b>⏹️ Стоп</b>"
    )
    
    # Показываем кнопку Стоп на клавиатуре
    await callback.message.answer(
        "⏹️ Для остановки:",
        reply_markup=stop_search_keyboard()
    )
    
    # Запускаем парсинг с фильтром
    await start_parsing(callback.message, user_id, model, start, end, search_id, only_nonupgraded=True)

# === CALLBACK: Случайный поиск - все подарки ===
@dp.callback_query(F.data == "filter_all", SearchStates.waiting_for_random_filter)
async def callback_random_filter_all(callback: CallbackQuery, state: FSMContext):
    """Случайный поиск: все подарки"""
    data = await state.get_data()
    count = data['random_count']
    
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Логирование
    await channel_logger.log_action(user_id, username, f"Случайный поиск: {count} подарков (все)")
    
    # Уменьшаем количество пробных поисков (админ не расходует)
    user = db.get_user(user_id)
    if user_id != OWNER_ID and not user['is_premium']:
        new_value = user['trial_searches_left'] - count
        db.update_trial_searches(user_id, max(0, new_value))
    
    await state.clear()
    await callback.answer()
    
    await callback.message.edit_text(
        f"🎲 <b>Начинаю случайный поиск...</b>\n\n"
        f"🎁 Количество: <b>{count}</b>\n"
        f"🔍 Фильтр: <b>Все подарки</b>\n\n"
        f"⏱️ Это может занять некоторое время...\n"
        f"Для остановки нажмите кнопку <b>⏹️ Стоп</b>"
    )
    
    await callback.message.answer(
        "⏹️ Для остановки:",
        reply_markup=stop_search_keyboard()
    )
    
    # Запускаем случайный парсинг
    await start_random_parsing(callback.message, user_id, count, only_nonupgraded=False)

# === CALLBACK: Случайный поиск - неулучшенные ===
@dp.callback_query(F.data == "filter_nonupgraded", SearchStates.waiting_for_random_filter)
async def callback_random_filter_nonupgraded(callback: CallbackQuery, state: FSMContext):
    """Случайный поиск: только неулучшенные"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    
    # Проверка подписки на неулучшенные (админ имеет доступ)
    if user_id != OWNER_ID and not user.get('is_nonupgraded_premium', False) and user.get('nonupgraded_searches_left', 0) <= 0:
        await callback.message.edit_text(
            "❌ <b>Нет доступа к поиску неулучшенных подарков!</b>\n\n"
            "💎 Оформите подписку на неулучшенные подарки:\n"
            f"💰 Стоимость: <b>5 TON</b>\n\n"
            f"📊 Бесплатных поисков: {user.get('nonupgraded_searches_left', 0)}/3",
            reply_markup=payment_method_keyboard(is_nonupgraded=True)
        )
        await state.clear()
        await callback.answer()
        return
    
    data = await state.get_data()
    count = data['random_count']
    
    # Проверка лимита для бесплатных поисков неулучшенных (админ = безлимит)
    if user_id != OWNER_ID and not user['is_nonupgraded_premium'] and count > user['nonupgraded_searches_left']:
        await callback.message.edit_text(
            f"❌ <b>Недостаточно бесплатных поисков!</b>\n\n"
            f"💎 Доступно: {user['nonupgraded_searches_left']}\n"
            f"📝 Запрошено: {count}\n\n"
            "Оформите подписку или уменьшите количество.",
            reply_markup=payment_method_keyboard(is_nonupgraded=True)
        )
        await state.clear()
        await callback.answer()
        return
    
    username = callback.from_user.username
    
    # Очистить флаг остановки
    db.clear_stop_flag(user_id)
    
    # Логирование
    await channel_logger.log_action(user_id, username, f"Случайный поиск: {count} подарков (неулучшенные)")
    
    # Уменьшаем количество поисков неулучшенных (админ не расходует)
    if user_id != OWNER_ID and not user['is_nonupgraded_premium']:
        new_value = user['nonupgraded_searches_left'] - count
        db.update_nonupgraded_searches(user_id, max(0, new_value))
    
    await state.clear()
    await callback.answer()
    
    await callback.message.edit_text(
        f"🎲 <b>Начинаю случайный поиск...</b>\n\n"
        f"🎁 Количество: <b>{count}</b>\n"
        f"💎 Фильтр: <b>Только неулучшенные</b>\n\n"
        f"⏱️ Это может занять некоторое время...\n"
        f"Для остановки нажмите кнопку <b>⏹️ Стоп</b>"
    )
    
    await callback.message.answer(
        "⏹️ Для остановки:",
        reply_markup=stop_search_keyboard()
    )
    
    # Запускаем случайный парсинг с фильтром
    await start_random_parsing(callback.message, user_id, count, only_nonupgraded=True)

# === CALLBACK: Продолжить парсинг ===
@dp.callback_query(F.data == "continue_parsing")
async def callback_continue_parsing(callback: CallbackQuery):
    """Продолжить остановленный парсинг"""
    user_id = callback.from_user.id
    last = db.get_last_search_state(user_id)
    
    if not last:
        await callback.answer("❌ Нет данных для продолжения", show_alert=True)
        return
    
    db.clear_stop_flag(user_id)
    
    await callback.message.answer(
        f"▶️ <b>Продолжаю поиск...</b>\n\n"
        f"🎁 Модель: <code>{last['model']}</code>\n"
        f"📊 С ID {last['current_id']} по {last['end_id']}",
        reply_markup=stop_parsing_keyboard()
    )
    
    # Продолжаем парсинг
    await start_parsing(
        callback.message,
        user_id,
        last['model'],
        last['current_id'],
        last['end_id'],
        last['search_id']
    )
    
    await callback.answer()

# === CALLBACK: Остановить парсинг ===
@dp.callback_query(F.data == "stop_parsing")
async def callback_stop_parsing(callback: CallbackQuery):
    """Остановить текущий парсинг"""
    user_id = callback.from_user.id
    db.set_stop_flag(user_id, True)
    await callback.answer("⏹️ Остановка...", show_alert=True)

# === CALLBACK: В меню ===
@dp.callback_query(F.data == "back_to_menu")
async def callback_back_to_menu(callback: CallbackQuery):
    """Вернуться в главное меню"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    is_admin = (user_id == OWNER_ID)
    
    await callback.message.edit_text("🏠 Главное меню:")
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=main_menu_keyboard(is_admin=is_admin, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
    )
    await callback.answer()

# === ПЛАТЕЖИ ===
@dp.message(F.text == "⭐ Купить Premium")
async def buy_premium_command(message: Message):
    """Показать варианты оплаты"""
    text = (
        "⭐ <b>Premium подписка</b>\n\n"
        "💎 <b>Преимущества:</b>\n"
        "✅ Безлимитные поиски\n"
        "✅ Приоритетная поддержка\n"
        "✅ Ранний доступ к новым функциям\n\n"
        f"💰 <b>Стоимость:</b> 5 TON\n\n"
        "Выберите способ оплаты:"
    )
    
    await message.answer(text, reply_markup=payment_method_keyboard())

@dp.message(F.text == "💎 Купить подписку на неулучшенные")
async def buy_nonupgraded_premium_command(message: Message):
    """Показать варианты оплаты подписки на неулучшенные"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    # Проверка наличия обычной подписки
    if not user['is_premium']:
        text = (
            "❌ <b>Требуется Premium подписка!</b>\n\n"
            "Подписка на неулучшенные подарки доступна только\n"
            "при наличии обычной Premium подписки.\n\n"
            "Сначала оформите Premium (5 TON), затем\n"
            "сможете купить доступ к неулучшенным (+5 TON).\n\n"
            "Выберите способ оплаты для Premium:"
        )
        await message.answer(text, reply_markup=payment_method_keyboard(is_nonupgraded=False))
        return
    
    text = (
        "💎 <b>Подписка на неулучшенные подарки</b>\n\n"
        "💎 <b>Преимущества:</b>\n"
        "✅ Поиск только неулучшенных подарков\n"
        "✅ Пропуск улучшенных в результатах\n"
        "✅ Экономия времени на парсинг\n"
        "✅ Больше шансов найти редкие\n\n"
        f"💰 <b>Стоимость:</b> 5 TON\n"
        f"🎫 <b>Пробных поисков:</b> {user.get('nonupgraded_searches_left', 3)}/3\n\n"
        "⚠️ <b>Требуется:</b> Обычная Premium подписка ✅\n\n"
        "Выберите способ оплаты:"
    )
    
    await message.answer(text, reply_markup=payment_method_keyboard(is_nonupgraded=True))

@dp.callback_query(F.data == "pay_cryptobot")
async def callback_pay_cryptobot(callback: CallbackQuery):
    """Оплата через CryptoBot"""
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    invoice_url = await payment_manager.create_cryptobot_invoice(user_id, username)
    
    if invoice_url:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💳 Оплатить", url=invoice_url))
        
        await callback.message.edit_text(
            "💎 <b>CryptoBot Payment</b>\n\n"
            "Нажмите кнопку ниже для оплаты.\n"
            "После оплаты Premium активируется автоматически!",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.answer("❌ Ошибка создания счёта", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "pay_xrocket")
async def callback_pay_xrocket(callback: CallbackQuery):
    """Оплата через XRocket"""
    user_id = callback.from_user.id
    
    invoice_url = await payment_manager.create_xrocket_invoice(user_id)
    
    if invoice_url:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🚀 Оплатить", url=invoice_url))
        
        await callback.message.edit_text(
            "🚀 <b>XRocket Payment</b>\n\n"
            "Нажмите кнопку ниже для оплаты.\n"
            "После оплаты Premium активируется автоматически!",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.answer("❌ Ошибка создания счёта", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "pay_nonupgraded_cryptobot")
async def callback_pay_nonupgraded_cryptobot(callback: CallbackQuery):
    """Оплата подписки на неулучшенные через CryptoBot"""
    user_id = callback.from_user.id
    username = callback.from_user.username
    user = db.get_user(user_id)
    
    # Проверка наличия обычной подписки
    if not user['is_premium']:
        await callback.message.edit_text(
            "❌ <b>Требуется Premium подписка!</b>\n\n"
            "Подписка на неулучшенные подарки доступна только\n"
            "при наличии обычной Premium подписки.\n\n"
            "Сначала оформите Premium (5 TON).",
            reply_markup=payment_method_keyboard(is_nonupgraded=False)
        )
        await callback.answer()
        return
    
    invoice_url = await payment_manager.create_cryptobot_invoice(user_id, username, is_nonupgraded=True)
    
    if invoice_url:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💳 Оплатить", url=invoice_url))
        
        await callback.message.edit_text(
            "💎 <b>CryptoBot Payment</b>\n"
            "💎 <b>Подписка на неулучшенные подарки</b>\n\n"
            "Нажмите кнопку ниже для оплаты.\n"
            "После оплаты подписка активируется автоматически!",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.answer("❌ Ошибка создания счёта", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "pay_nonupgraded_xrocket")
async def callback_pay_nonupgraded_xrocket(callback: CallbackQuery):
    """Оплата подписки на неулучшенные через XRocket"""
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    
    # Проверка наличия обычной подписки
    if not user['is_premium']:
        await callback.message.edit_text(
            "❌ <b>Требуется Premium подписка!</b>\n\n"
            "Подписка на неулучшенные подарки доступна только\n"
            "при наличии обычной Premium подписки.\n\n"
            "Сначала оформите Premium (5 TON).",
            reply_markup=payment_method_keyboard(is_nonupgraded=False)
        )
        await callback.answer()
        return
    
    invoice_url = await payment_manager.create_xrocket_invoice(user_id, is_nonupgraded=True)
    
    if invoice_url:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🚀 Оплатить", url=invoice_url))
        
        await callback.message.edit_text(
            "🚀 <b>XRocket Payment</b>\n"
            "💎 <b>Подписка на неулучшенные подарки</b>\n\n"
            "Нажмите кнопку ниже для оплаты.\n"
            "После оплаты подписка активируется автоматически!",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.answer("❌ Ошибка создания счёта", show_alert=True)
    
    await callback.answer()

# === ПОМОЩЬ ===
@dp.message(F.text == "ℹ️ Помощь")
async def help_button_handler(message: Message):
    """Помощь через кнопку"""
    await help_command(message)

# === АДМИНКА ===
@dp.message(F.text == "⚙️ Админка")
async def admin_panel(message: Message):
    """Панель администратора"""
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ У вас нет доступа к админке")
        return
    
    stats = db.get_total_stats()
    
    text = (
        "⚙️ <b>Админ панель</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"⭐ Premium: <b>{stats['premium_users']}</b>\n"
        f"🔍 Всего поисков: <b>{stats['total_searches']}</b>\n"
        f"✅ Найдено подарков: <b>{stats['total_gifts_found']}</b>\n"
    )
    
    await message.answer(text, reply_markup=admin_menu_keyboard())

@dp.message(F.text == "👥 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    """Управление пользователями"""
    if message.from_user.id != OWNER_ID:
        return
    
    await state.set_state(AdminStates.waiting_for_user_id)
    await message.answer(
        "👥 <b>Управление пользователями</b>\n\n"
        "Введите ID или @username пользователя:",
        reply_markup=cancel_keyboard()
    )

@dp.message(AdminStates.waiting_for_user_id)
async def admin_user_entered(message: Message, state: FSMContext):
    """Обработка введённого ID пользователя"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=admin_menu_keyboard())
        return
    
    try:
        # Попытка получить user_id
        if message.text.startswith("@"):
            username = message.text[1:]
            user = db.get_user_by_username(username)
            if not user:
                await message.answer("❌ Пользователь не найден. Попробуйте ввести ID (число)")
                return
            target_user_id = user['user_id']
        else:
            target_user_id = int(message.text)
            user = db.get_user(target_user_id)
            if not user:
                await message.answer("❌ Пользователь не найден. Проверьте ID")
                return
        
        await state.clear()
        
        ban_status = "🚫 Забанен" if user['is_banned'] else "✅ Активен"
        premium_status = "⭐ Premium" if user['is_premium'] else "🎫 Trial"
        
        text = (
            f"👤 <b>Пользователь</b>\n\n"
            f"🆔 ID: <code>{user['user_id']}</code>\n"
            f"👤 Username: @{user.get('username', 'не указан')}\n"
            f"� Имя: {user.get('first_name', 'не указано')}\n"
            f"�� Регистрация: {user['registration_date'][:10]}\n"
            f"💎 Статус: {premium_status}\n"
            f"🎫 Пробных поисков: {user['trial_searches_left']}/25\n"
            f"🔒 Бан: {ban_status}\n"
        )
        
        await message.answer(text, reply_markup=ban_user_keyboard(target_user_id))
        
    except ValueError:
        await message.answer("❌ Неверный формат! Введите ID (число) или @username")

@dp.callback_query(F.data.startswith("ban_"))
async def callback_ban_user(callback: CallbackQuery):
    """Забанить пользователя"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[1])
    db.ban_user(user_id)
    
    await callback.answer("🚫 Пользователь забанен", show_alert=True)
    await callback.message.edit_text(
        f"🚫 Пользователь {user_id} забанен",
        reply_markup=ban_user_keyboard(user_id)
    )

@dp.callback_query(F.data.startswith("unban_"))
async def callback_unban_user(callback: CallbackQuery):
    """Разбанить пользователя"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[1])
    db.unban_user(user_id)
    
    await callback.answer("✅ Пользователь разбанен", show_alert=True)
    await callback.message.edit_text(
        f"✅ Пользователь {user_id} разбанен",
        reply_markup=ban_user_keyboard(user_id)
    )

@dp.callback_query(F.data.startswith("premium_give_"))
async def callback_give_premium(callback: CallbackQuery):
    """Выдать Premium"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    db.set_premium(user_id, True)
    
    user = db.get_user(user_id)
    await channel_logger.log_premium_activated(user_id, user.get('username'))
    
    await callback.answer("⭐ Premium выдан", show_alert=True)
    await callback.message.edit_text(
        f"⭐ Premium выдан пользователю {user_id}",
        reply_markup=ban_user_keyboard(user_id)
    )

@dp.callback_query(F.data.startswith("premium_remove_"))
async def callback_remove_premium(callback: CallbackQuery):
    """Забрать Premium"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    db.set_premium(user_id, False)
    
    await callback.answer("❌ Premium удалён", show_alert=True)
    await callback.message.edit_text(
        f"❌ Premium удалён у пользователя {user_id}",
        reply_markup=ban_user_keyboard(user_id)
    )

@dp.callback_query(F.data.startswith("trials_add10_"))
async def callback_add_trials_10(callback: CallbackQuery):
    """Добавить 10 пробных поисков"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    user = db.get_user(user_id)
    
    new_value = user['trial_searches_left'] + 10
    db.update_trial_searches(user_id, new_value)
    
    await callback.answer(f"✅ Добавлено 10 поисков (теперь: {new_value})", show_alert=True)
    
    # Обновляем сообщение
    ban_status = "🚫 Забанен" if user['is_banned'] else "✅ Активен"
    premium_status = "⭐ Premium" if user['is_premium'] else "🎫 Trial"
    
    text = (
        f"👤 <b>Пользователь</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: @{user.get('username', 'не указан')}\n"
        f"👤 Имя: {user.get('first_name', 'не указано')}\n"
        f"📅 Регистрация: {user['registration_date'][:10]}\n"
        f"💎 Статус: {premium_status}\n"
        f"🎫 Пробных поисков: {new_value}/25\n"
        f"🔒 Бан: {ban_status}\n"
    )
    
    await callback.message.edit_text(text, reply_markup=ban_user_keyboard(user_id))

@dp.callback_query(F.data.startswith("trials_add25_"))
async def callback_add_trials_25(callback: CallbackQuery):
    """Добавить 25 пробных поисков"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    user = db.get_user(user_id)
    
    new_value = user['trial_searches_left'] + 25
    db.update_trial_searches(user_id, new_value)
    
    await callback.answer(f"✅ Добавлено 25 поисков (теперь: {new_value})", show_alert=True)
    
    # Обновляем сообщение
    ban_status = "🚫 Забанен" if user['is_banned'] else "✅ Активен"
    premium_status = "⭐ Premium" if user['is_premium'] else "🎫 Trial"
    
    text = (
        f"👤 <b>Пользователь</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: @{user.get('username', 'не указан')}\n"
        f"👤 Имя: {user.get('first_name', 'не указано')}\n"
        f"📅 Регистрация: {user['registration_date'][:10]}\n"
        f"💎 Статус: {premium_status}\n"
        f"🎫 Пробных поисков: {new_value}/25\n"
        f"🔒 Бан: {ban_status}\n"
    )
    
    await callback.message.edit_text(text, reply_markup=ban_user_keyboard(user_id))

@dp.callback_query(F.data.startswith("trials_set_"))
async def callback_set_trials(callback: CallbackQuery, state: FSMContext):
    """Установить количество пробных поисков"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    await state.set_state(AdminStates.waiting_for_trials_count)
    await state.update_data(target_user_id=user_id)
    
    await callback.message.answer(
        "🎫 <b>Установить количество поисков</b>\n\n"
        "Введите число (например: 50):",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_trials_count)
async def admin_trials_count_entered(message: Message, state: FSMContext):
    """Обработка введённого количества поисков"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=admin_menu_keyboard())
        return
    
    try:
        count = int(message.text)
        if count < 0:
            raise ValueError()
        
        data = await state.get_data()
        user_id = data['target_user_id']
        
        db.update_trial_searches(user_id, count)
        
        await state.clear()
        await message.answer(
            f"✅ Установлено {count} пробных поисков для пользователя {user_id}",
            reply_markup=admin_menu_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Неверный формат! Введите число (например: 50)")

@dp.message(F.text == "📈 Статистика")
async def admin_stats(message: Message):
    """Статистика бота"""
    if message.from_user.id != OWNER_ID:
        return
    
    stats = db.get_total_stats()
    
    text = (
        "📈 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"⭐ Premium подписок: <b>{stats['premium_users']}</b>\n"
        f"🔍 Всего поисков: <b>{stats['total_searches']}</b>\n"
        f"✅ Найдено подарков: <b>{stats['total_gifts_found']}</b>\n"
    )
    
    await message.answer(text)

@dp.message(F.text == "🔧 Настройки")
async def admin_settings(message: Message):
    """Настройки доступа"""
    if message.from_user.id != OWNER_ID:
        return
    
    settings = db.get_bot_settings()
    mode = settings['access_mode']
    
    mode_text = "Только владелец" if mode == 'owner_only' else "Все пользователи"
    
    text = (
        "🔧 <b>Настройки бота</b>\n\n"
        f"🔒 Режим доступа: <b>{mode_text}</b>\n\n"
        "Выберите режим:"
    )
    
    await message.answer(text, reply_markup=admin_settings_keyboard(mode))

@dp.callback_query(F.data == "settings_owner_only")
async def callback_settings_owner_only(callback: CallbackQuery):
    """Установить режим "только владелец" """
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    db.update_bot_settings(access_mode='owner_only')
    
    await callback.answer("✅ Режим изменён", show_alert=True)
    await callback.message.edit_text(
        "🔧 <b>Настройки бота</b>\n\n"
        "🔒 Режим доступа: <b>Только владелец</b>\n\n"
        "Выберите режим:",
        reply_markup=admin_settings_keyboard('owner_only')
    )

@dp.callback_query(F.data == "settings_all")
async def callback_settings_all(callback: CallbackQuery):
    """Установить режим "все пользователи" """
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    db.update_bot_settings(access_mode='all')
    
    await callback.answer("✅ Режим изменён", show_alert=True)
    await callback.message.edit_text(
        "🔧 <b>Настройки бота</b>\n\n"
        "🔒 Режим доступа: <b>Все пользователи</b>\n\n"
        "Выберите режим:",
        reply_markup=admin_settings_keyboard('all')
    )

@dp.message(F.text == "🔙 Назад")
async def admin_back(message: Message):
    """Назад из админки"""
    if message.from_user.id != OWNER_ID:
        return
    
    user = db.get_user(message.from_user.id)
    await message.answer(
        "🏠 Главное меню",
        reply_markup=main_menu_keyboard(is_admin=True, is_premium=user['is_premium'], is_nonupgraded_premium=user.get('is_nonupgraded_premium', False))
    )

# === ЗАПУСК БОТА ===
async def main():
    """Запуск бота"""
    logging.info("Бот запускается...")
    
    # Удаление webhook (если был)
    await bot.delete_webhook(drop_pending_updates=True)
    
    logging.info("Polling запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
