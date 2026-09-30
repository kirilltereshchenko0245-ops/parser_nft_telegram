import asyncio
import logging
import os
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

try:
    from aiohttp_socks import ProxyConnector
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False

from peek_api import PeekAPI

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не указан в .env")

logging.basicConfig(level=logging.INFO)

class ParseForm(StatesGroup):
    model = State()
    start_id = State()
    end_id = State()

dp = Dispatcher(storage=MemoryStorage())
api = PeekAPI()

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎁 <b>Telegram Gift Parser</b>\n\n"
        "Я могу проверить подарки через Peek API и показать найденного владельца.\n\n"
        "Команда: /parse"
    )

@dp.message(lambda m: m.text == "/parse")
async def parse_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ParseForm.model)
    await message.answer(
        "1️⃣ Введи модель подарка.\n"
        "Например:\n<code>EternalRose</code>"
    )

@dp.message(ParseForm.model)
async def model_received(message: Message, state: FSMContext):
    model = message.text.strip()
    if not model:
        await message.answer("Модель не может быть пустой.")
        return
    await state.update_data(model=model)
    await state.set_state(ParseForm.start_id)
    await message.answer("2️⃣ Введи начальный ID.\nНапример: <code>13000</code>")

@dp.message(ParseForm.start_id)
async def start_received(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
        if value < 1:
            raise ValueError
    except ValueError:
        await message.answer("Нужен целый положительный ID.")
        return
    await state.update_data(start_id=value)
    await state.set_state(ParseForm.end_id)
    await message.answer("3️⃣ Введи конечный ID.\nНапример: <code>13100</code>")

@dp.message(ParseForm.end_id)
async def end_received(message: Message, state: FSMContext):
    try:
        end_id = int(message.text.strip())
        if end_id < 1:
            raise ValueError
    except ValueError:
        await message.answer("Нужен целый положительный ID.")
        return

    data = await state.get_data()
    start_id = data["start_id"]
    model = data["model"]

    if end_id < start_id:
        await message.answer("Конечный ID не может быть меньше начального.")
        return

    # Защита от случайного огромного диапазона.
    if end_id - start_id > 5000:
        await message.answer("За один запуск максимум 5000 ID.")
        return

    await state.clear()
    status = await message.answer(
        f"🔎 Начинаю проверку...\n"
        f"🎁 Модель: <code>{model}</code>\n"
        f"🆔 Диапазон: <code>{start_id}–{end_id}</code>"
    )

    found = 0
    checked = 0

    try:
        for gift_number in range(start_id, end_id + 1):
            checked += 1

            try:
                gift = await api.get_gift(model, gift_number)
            except Exception as e:
                logging.exception("Gift %s error: %s", gift_number, e)
                continue

            if gift:
                found += 1
                await message.answer(api.format_gift(gift))

            if checked % 25 == 0:
                try:
                    await status.edit_text(
                        f"🔎 Проверяю...\n"
                        f"🎁 <code>{model}</code>\n"
                        f"📊 Проверено: <b>{checked}</b>\n"
                        f"✅ Найдено: <b>{found}</b>"
                    )
                except Exception:
                    pass

            # Небольшая пауза, чтобы не долбить API слишком быстро.
            await asyncio.sleep(0.15)

        await status.edit_text(
            f"✅ <b>Готово</b>\n\n"
            f"🎁 Модель: <code>{model}</code>\n"
            f"🆔 Диапазон: <code>{start_id}–{end_id}</code>\n"
            f"📊 Проверено: <b>{checked}</b>\n"
            f"👤 Найдено: <b>{found}</b>"
        )
    finally:
        await api.close()

async def main():
    # Настройка прокси для бота
    proxy_url = os.getenv("PROXY_URL")
    api_server = os.getenv("API_SERVER")  # Локальный Bot API сервер
    
    bot_session = None
    aiohttp_session = None
    
    if proxy_url and PROXY_SUPPORT:
        logging.info(f"Используется прокси: {proxy_url}")
        # Создаём коннектор с прокси и remote DNS
        connector = ProxyConnector.from_url(proxy_url, rdns=True)
        # Создаём aiohttp сессию с прокси-коннектором
        aiohttp_session = aiohttp.ClientSession(connector=connector)
        
        # Создаём кастомную AiohttpSession
        class ProxyAiohttpSession(AiohttpSession):
            def __init__(self, session, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._custom_session = session
                
            async def create_session(self) -> aiohttp.ClientSession:
                return self._custom_session
        
        bot_session = ProxyAiohttpSession(aiohttp_session)
    elif proxy_url and not PROXY_SUPPORT:
        logging.warning("PROXY_URL указан, но библиотека aiohttp-socks не установлена")
    
    # Создание бота
    bot_kwargs = {
        "token": BOT_TOKEN,
        "default": DefaultBotProperties(parse_mode=ParseMode.HTML)
    }
    
    if bot_session:
        bot_kwargs["session"] = bot_session
    
    # Если указан локальный API сервер
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
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
