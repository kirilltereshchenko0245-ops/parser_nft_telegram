import asyncio
import logging
import os
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

try:
    from aiohttp_socks import ProxyConnector
    import aiohttp
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False

from peek_api import PeekAPI
from database import Database
from keyboards import *

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не указан в .env")

logging.basicConfig(level=logging.INFO)

db = Database()
dp = Dispatcher(storage=MemoryStorage())

class SearchForm(StatesGroup):
    model = State()
    range_input = State()

class AdminForm(StatesGroup):
    ban_user = State()
    unban_user = State()

@dp.message.middleware()
async def check_access_middleware(handler, event: Message, data: dict):
    """Проверка доступа к боту"""
    user_id = event.from_user.id
    
    user = db.get_user(user_id)
    if not user:
        db.create_user(
            user_id=user_id,
            username=event.from_user.username,
            first_name=event.from_user.first_name
        )
        user = db.get_user(user_id)
    else:
        db.update_user_activity(user_id)
    
    if user['is_banned']:
        await event.answer(
            "❌ <b>Доступ запрещён</b>\n\n"
            "Вы были заблокированы администратором.\n"
            "Для разблокировки обратитесь: @kod30"
        )
        return
    
    bot_mode = db.get_setting('bot_mode', 'all')
    if bot_mode == 'owner_only' and user_id != OWNER_ID:
        await event.answer(
            "🔒 <b>Бот временно недоступен</b>\n\n"
            "Бот находится в режиме обслуживания.\n"
            "Для получения доступа: @kod30"
        )
        return
    
    data['db_user'] = user
    data['is_admin'] = (user_id == OWNER_ID)
    
    return await handler(event, data)

@dp.message(CommandStart())
async def start_command(message: Message, db_user: dict, is_admin: bool):
    """Команда /start"""
    user_name = message.from_user.first_name
    
    welcome_text = (
        f"👋 Привет, <b>{user_name}</b>!\n\n"
        "🎁 <b>Telegram Gift Parser</b>\n"
        "Я помогу найти владельцев NFT-подарков Telegram.\n\n"
    )
    
    if db_user['is_premium']:
        welcome_text += "⭐ У вас <b>Premium</b> - безлимитный доступ!\n\n"
    else:
        welcome_text += f"🎫 Пробных поисков осталось: <b>{db_user['trial_searches_left']}</b>\n\n"
    
    welcome_text += (
        "Выберите действие из меню ниже:\n"
        "🔍 <b>Начать поиск</b> - найти подарки\n"
        "📊 <b>Профиль</b> - ваша статистика\n"
        "📜 <b>История</b> - история поисков\n"
        "⏭️ <b>Продолжить</b> - продолжить последний поиск\n"
    )
    
    if not db_user['is_premium']:
        welcome_text += "\n💎 Для полного доступа: @kod30"
    
    await message.answer(
        welcome_text,
        reply_markup=main_menu_keyboard(is_admin)
    )

@dp.message(F.text == "📊 Профиль")
async def profile_command(message: Message, db_user: dict):
    """Показать профиль пользователя"""
    user_id = message.from_user.id
    
    status = "⭐ Premium" if db_user['is_premium'] else f"🎫 Пробный ({db_user['trial_searches_left']} поисков)"
    
    profile_text = (
        "👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📝 Статус: {status}\n"
        f"📅 Регистрация: {db_user['registration_date'][:10]}\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"🔍 Всего поисков: <b>{db_user['total_searches']}</b>\n"
        f"🎁 Найдено подарков: <b>{db_user['total_found']}</b>\n\n"
    )
    
    if not db_user['is_premium']:
        profile_text += "💎 Хотите безлимит? Напишите @kod30"
    
    await message.answer(profile_text, reply_markup=profile_keyboard())

@dp.message(F.text == "📜 История")
@dp.callback_query(F.data.startswith("history_page_"))
async def history_command(event, db_user: dict):
    """Показать историю поисков"""
    user_id = db_user['user_id']
    
    if isinstance(event, CallbackQuery):
        page = int(event.data.split("_")[-1])
        await event.answer()
    else:
        page = 1
    
    per_page = 5
    offset = (page - 1) * per_page
    
    searches = db.get_user_searches(user_id, limit=per_page, offset=offset)
    total_count = db.count_user_searches(user_id)
    total_pages = (total_count + per_page - 1) // per_page
    
    if not searches:
        text = "📜 <b>История поисков</b>\n\nУ вас пока нет поисков."
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text)
        else:
            await event.answer(text)
        return
    
    text = f"📜 <b>История поисков</b> (стр. {page}/{total_pages})\n\n"
    
    for search in searches:
        status_emoji = "✅" if search['status'] == 'completed' else "⏸️"
        text += (
            f"{status_emoji} <b>{search['model']}</b>\n"
            f"🆔 Диапазон: {search['start_id']} - {search['end_id']}\n"
            f"📊 Проверено: {search['checked_count']} | Найдено: {search['found_count']}\n"
            f"📅 {search['started_at'][:16]}\n\n"
        )
    
    keyboard = history_pagination_keyboard(
        page=page,
        total_pages=total_pages,
        has_prev=(page > 1),
        has_next=(page < total_pages)
    )
    
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=keyboard)
    else:
        await event.answer(text, reply_markup=keyboard)

@dp.message(F.text == "⏭️ Продолжить")
async def continue_command(message: Message, db_user: dict):
    """Продолжить последний незавершённый поиск"""
    last_search = db.get_last_search(db_user['user_id'])
    
    if not last_search:
        await message.answer(
            "❌ Нет незавершённых поисков.\n\n"
            "Нажмите <b>🔍 Начать поиск</b> для нового парсинга."
        )
        return
    
    text = (
        "⏸️ <b>Найден незавершённый поиск</b>\n\n"
        f"🎁 Модель: <code>{last_search['model']}</code>\n"
        f"🆔 Диапазон: {last_search['start_id']} - {last_search['end_id']}\n"
        f"📊 Проверено: {last_search['checked_count']}\n"
        f"✅ Найдено: {last_search['found_count']}\n"
        f"🔄 Последний ID: {last_search['last_checked_id']}\n\n"
        "Продолжить этот поиск?"
    )
    
    await message.answer(text, reply_markup=continue_search_keyboard())

@dp.callback_query(F.data == "continue_yes")
async def continue_yes(callback: CallbackQuery, state: FSMContext, db_user: dict):
    """Подтверждение продолжения поиска"""
    await callback.answer()
    
    last_search = db.get_last_search(db_user['user_id'])
    if not last_search:
        await callback.message.edit_text("❌ Поиск не найден.")
        return
    
    await callback.message.edit_text("🔍 Продолжаю поиск...")
    await start_parsing(
        callback.message,
        search_id=last_search['id'],
        model=last_search['model'],
        start_id=last_search['last_checked_id'] + 1,
        end_id=last_search['end_id'],
        db_user=db_user,
        is_continue=True
    )

@dp.callback_query(F.data == "continue_no")
async def continue_no(callback: CallbackQuery, state: FSMContext):
    """Отмена продолжения, начать новый поиск"""
    await callback.answer()
    await callback.message.edit_text("Отменено. Нажмите <b>🔍 Начать поиск</b> для нового парсинга.")

@dp.message(F.text == "🔍 Начать поиск")
async def start_search_command(message: Message, state: FSMContext, db_user: dict):
    """Начать новый поиск"""
    if not db_user['is_premium'] and db_user['trial_searches_left'] <= 0:
        await message.answer(
            "❌ <b>Пробный период закончился</b>\n\n"
            "🎫 У вас больше нет пробных поисков.\n\n"
            "💎 Для получения полного доступа: @kod30"
        )
        return
    
    await state.set_state(SearchForm.model)
    await message.answer(
        "🎁 <b>Начинаем поиск подарков</b>\n\n"
        "1️⃣ Введите название модели подарка:\n"
        "Например: <code>EternalRose</code>, <code>Delicious</code>, <code>BlueStar</code>",
        reply_markup=cancel_keyboard()
    )

@dp.message(SearchForm.model)
async def search_model_received(message: Message, state: FSMContext):
    """Получена модель подарка"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_keyboard())
        return
    
    model = message.text.strip()
    if not model:
        await message.answer("Модель не может быть пустой.")
        return
    
    await state.update_data(model=model)
    await state.set_state(SearchForm.range_input)
    
    await message.answer(
        "2️⃣ Введите диапазон ID через тире:\n\n"
        "Примеры:\n"
        "• <code>10-20</code> (проверит ID с 10 по 20)\n"
        "• <code>13000-13100</code>\n"
        "• <code>1-50</code> (для пробных пользователей макс. 50 ID)\n\n"
        "❗ Важно: пробным пользователям доступно макс. 50 ID за раз.",
        reply_markup=cancel_keyboard()
    )

@dp.message(SearchForm.range_input)
async def search_range_received(message: Message, state: FSMContext, db_user: dict, is_admin: bool):
    """Получен диапазон ID"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_keyboard(is_admin))
        return
    
    match = re.match(r'^(\d+)-(\d+)$', message.text.strip())
    if not match:
        await message.answer(
            "❌ Неверный формат!\n\n"
            "Используйте формат: <code>начало-конец</code>\n"
            "Например: <code>10-20</code>"
        )
        return
    
    start_id = int(match.group(1))
    end_id = int(match.group(2))
    
    if start_id >= end_id:
        await message.answer("❌ Начальный ID должен быть меньше конечного!")
        return
    
    range_size = end_id - start_id + 1
    
    if not db_user['is_premium']:
        if range_size > 50:
            await message.answer(
                f"❌ Для пробных пользователей максимум <b>50</b> ID за раз!\n\n"
                f"Ваш диапазон: {range_size} ID\n\n"
                "💎 Для безлимитного доступа: @kod30"
            )
            return
        
        if not db.use_trial_search(db_user['user_id']):
            await message.answer(
                "❌ <b>Пробный период закончился</b>\n\n"
                "💎 Для продолжения: @kod30"
            )
            return
    
    data = await state.get_data()
    model = data['model']
    await state.clear()
    
    search_id = db.create_search(db_user['user_id'], model, start_id, end_id)
    
    await message.answer(
        f"🔎 <b>Начинаю поиск...</b>\n\n"
        f"🎁 Модель: <code>{model}</code>\n"
        f"🆔 Диапазон: <code>{start_id}-{end_id}</code> ({range_size} ID)\n\n"
        f"⏱️ Примерное время: ~{range_size * 5} сек",
        reply_markup=main_menu_keyboard(is_admin)
    )
    
    await start_parsing(message, search_id, model, start_id, end_id, db_user)

async def start_parsing(message: Message, search_id: int, model: str, start_id: int, end_id: int, db_user: dict, is_continue: bool = False):
    """Основная функция парсинга"""
    api = PeekAPI()
    user_id = db_user['user_id']
    
    found = 0
    checked = 0
    last_progress_update = 0
    
    status_msg = await message.answer(
        f"🔎 <b>Идёт поиск...</b>\n\n"
        f"🎁 Модель: <code>{model}</code>\n"
        f"📊 Проверено: <b>0</b> / {end_id - start_id + 1}\n"
        f"✅ Найдено: <b>0</b>\n"
        f"⏱️ Прогресс: ▱▱▱▱▱▱▱▱▱▱ 0%"
    )
    
    try:
        for gift_number in range(start_id, end_id + 1):
            checked += 1
            
            try:
                gift = await api.get_gift(model, gift_number)
            except Exception as e:
                logging.error(f"Gift {gift_number} error: {type(e).__name__}: {str(e)}")
                logging.exception(f"Full traceback for gift {gift_number}:")
                await asyncio.sleep(2)
                try:
                    gift = await api.get_gift(model, gift_number)
                except Exception as e2:
                    logging.error(f"Gift {gift_number} retry failed: {type(e2).__name__}: {str(e2)}")
                    gift = None
            
            if gift:
                found += 1
                db.save_found_gift(search_id, user_id, gift)
                await send_gift_result(message, gift)
            
            db.update_search_progress(search_id, gift_number, checked, found)
            
            if checked - last_progress_update >= 10 or checked == (end_id - start_id + 1):
                last_progress_update = checked
                total = end_id - start_id + 1
                progress = int((checked / total) * 100)
                progress_bar = "█" * (progress // 10) + "▱" * (10 - progress // 10)
                
                try:
                    await status_msg.edit_text(
                        f"🔎 <b>Идёт поиск...</b>\n\n"
                        f"🎁 Модель: <code>{model}</code>\n"
                        f"📊 Проверено: <b>{checked}</b> / {total}\n"
                        f"✅ Найдено: <b>{found}</b>\n"
                        f"⏱️ Прогресс: {progress_bar} {progress}%"
                    )
                except:
                    pass
            
            await asyncio.sleep(5)
        
        db.complete_search(search_id)
        db.increment_user_stats(user_id, found)
        
        await status_msg.edit_text(
            f"✅ <b>Поиск завершён!</b>\n\n"
            f"🎁 Модель: <code>{model}</code>\n"
            f"🆔 Диапазон: <code>{start_id}-{end_id}</code>\n"
            f"📊 Проверено: <b>{checked}</b>\n"
            f"🎁 Найдено владельцев: <b>{found}</b>\n\n"
            f"{'🎉 Отличный результат!' if found > 0 else '😔 В этом диапазоне владельцы не найдены'}"
        )
        
    finally:
        await api.close()

async def send_gift_result(message: Message, gift: dict):
    """Отправить красиво оформленный результат о подарке"""
    number = gift.get("giftNumber", "?")
    username = gift.get("username", "").replace("t.me/", "").lstrip("@")
    owner = gift.get("owner", "Неизвестно")
    model = gift.get("model", "?")
    pattern = gift.get("pattern", "?")
    backdrop = gift.get("backdrop", "?")
    gift_name = gift.get("giftName", gift.get("title", "?"))
    
    rarity_model = gift.get("rarityModel", 0)
    rarity_pattern = gift.get("rarityPattern", 0)
    rarity_backdrop = gift.get("rarityBackdrop", 0)
    
    market = gift.get("market") or {}
    price = market.get("price")
    
    text = (
        "🎉 <b>НАЙДЕН ПОДАРОК!</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"
        f"🎁 <b>{gift_name}</b>\n"
        f"🆔 ID: <code>{number}</code>\n\n"
        f"✨ Модель: <code>{model}</code>\n"
        f"   └ Редкость: {rarity_model}%\n\n"
        f"🌸 Узор: <code>{pattern}</code>\n"
        f"   └ Редкость: {rarity_pattern}%\n\n"
        f"🌈 Фон: <code>{backdrop}</code>\n"
        f"   └ Редкость: {rarity_backdrop}%\n\n"
    )
    
    if username:
        text += f"👤 Владелец: @{username}\n"
    else:
        text += f"👤 Владелец: <code>{owner}</code>\n"
    
    if price:
        text += f"💰 Цена: <b>{price} TON</b>\n"
    
    text += "\n━━━━━━━━━━━━━━━━"
    
    await message.answer(
        text,
        reply_markup=gift_result_keyboard(number, username, gift_name)
    )

@dp.message(F.text == "ℹ️ Помощь")
async def help_command(message: Message):
    """Помощь"""
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Нажмите <b>🔍 Начать поиск</b>\n"
        "2. Введите модель подарка (например: EternalRose)\n"
        "3. Введите диапазон ID через тире (например: 10-50)\n"
        "4. Ожидайте результаты!\n\n"
        "<b>Доступные кнопки:</b>\n"
        "🔍 Начать поиск - новый парсинг\n"
        "📊 Профиль - ваша статистика\n"
        "📜 История - все ваши поиски\n"
        "⏭️ Продолжить - продолжить последний поиск\n\n"
        "<b>Лимиты:</b>\n"
        "🎫 Пробный период: 50 поисков по 50 ID\n"
        "⭐ Premium: безлимитно\n\n"
        "💬 Связь с разработчиком: @kod30"
    )
    await message.answer(text)

@dp.message(F.text == "⚙️ Админка")
async def admin_panel(message: Message, is_admin: bool):
    """Админ-панель"""
    if not is_admin:
        return
    
    stats = db.get_total_stats()
    bot_mode = db.get_setting('bot_mode', 'all')
    
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"⭐ Premium: {stats['premium_users']}\n"
        f"🔍 Всего поисков: {stats['total_searches']}\n"
        f"🎁 Найдено подарков: {stats['total_gifts_found']}\n\n"
        f"🔧 <b>Режим бота:</b> {'🔒 Только владелец' if bot_mode == 'owner_only' else '🌍 Все пользователи'}"
    )
    
    await message.answer(text, reply_markup=admin_menu_keyboard())

@dp.message(F.text == "🔧 Настройки")
async def admin_settings(message: Message, is_admin: bool):
    """Настройки бота"""
    if not is_admin:
        return
    
    bot_mode = db.get_setting('bot_mode', 'all')
    
    text = (
        "🔧 <b>Настройки доступа</b>\n\n"
        "Выберите режим работы бота:"
    )
    
    await message.answer(text, reply_markup=admin_settings_keyboard(bot_mode))

@dp.callback_query(F.data.startswith("settings_"))
async def admin_settings_callback(callback: CallbackQuery, is_admin: bool):
    """Изменение настроек"""
    if not is_admin:
        return
    
    mode = callback.data.split("_", 1)[1]
    db.set_setting('bot_mode', mode)
    
    await callback.answer("✅ Настройка сохранена!")
    
    text = (
        "🔧 <b>Настройки доступа</b>\n\n"
        "Выберите режим работы бота:"
    )
    
    await callback.message.edit_text(text, reply_markup=admin_settings_keyboard(mode))

@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: Message, is_admin: bool):
    """Вернуться в главное меню"""
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard(is_admin))

async def main():
    proxy_url = os.getenv("PROXY_URL")
    api_server = os.getenv("API_SERVER")
    
    bot_session = None
    aiohttp_session = None
    
    if proxy_url and PROXY_SUPPORT:
        logging.info(f"Используется прокси: {proxy_url}")
        connector = ProxyConnector.from_url(proxy_url, rdns=True)
        aiohttp_session = aiohttp.ClientSession(connector=connector)
        
        class ProxyAiohttpSession(AiohttpSession):
            def __init__(self, session, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._custom_session = session
                
            async def create_session(self) -> aiohttp.ClientSession:
                return self._custom_session
        
        bot_session = ProxyAiohttpSession(aiohttp_session)
    elif proxy_url and not PROXY_SUPPORT:
        logging.warning("PROXY_URL указан, но библиотека aiohttp-socks не установлена")
    
    bot_kwargs = {
        "token": BOT_TOKEN,
        "default": DefaultBotProperties(parse_mode=ParseMode.HTML)
    }
    
    if bot_session:
        bot_kwargs["session"] = bot_session
    
    if api_server:
        logging.info(f"Используется локальный Bot API: {api_server}")
        from aiogram.client.telegram import TelegramAPIServer
        if proxy_url and PROXY_SUPPORT:
            connector = ProxyConnector.from_url(proxy_url, rdns=True)
            aiohttp_session = aiohttp.ClientSession(connector=connector)
            
            class ProxyAiohttpSession(AiohttpSession):
                def __init__(self, session, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._custom_session = session
                    
                async def create_session(self) -> aiohttp.ClientSession:
                    return self._custom_session
            
            bot_session = ProxyAiohttpSession(
                aiohttp_session,
                api=TelegramAPIServer.from_base(api_server)
            )
            bot_kwargs["session"] = bot_session
        else:
            bot_kwargs["session"] = AiohttpSession(
                api=TelegramAPIServer.from_base(api_server)
            )
    
    bot = Bot(**bot_kwargs)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Webhook удалён (если был)")
        
        logging.info("Бот запущен!")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
