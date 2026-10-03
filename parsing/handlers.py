"""Handlers для MTProto парсера."""
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from parsing.telegram_client import ParserClient
from parsing.scanner import ChatScanner
from parsing import keyboards as parser_kb

log = logging.getLogger('giftparser.parser')

router = Router()


class ParserStates(StatesGroup):
    """Состояния FSM для парсера."""
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()


# Глобальные переменные для хранения состояния парсера
parser_client = None
scanner = None
scan_task = None


def format_parser_menu(session_data):
    """Форматирование главного меню парсера."""
    if session_data:
        username = session_data.get('username') or 'Без username'
        first_name = session_data.get('first_name') or 'User'
        text = (
            '🔎 <b>Парсинг по чатам</b>\n\n'
            f'🟢 Аккаунт подключён\n'
            f'👤 @{username}\n'
            f'🔹 {first_name}\n\n'
            'Выберите действие:'
        )
    else:
        text = (
            '🔎 <b>Парсинг по чатам</b>\n\n'
            '🔴 Аккаунт не подключён\n\n'
            'Для начала работы подключите Telegram-аккаунт'
        )
    return text


@router.message(F.text == '🔎 Парсинг по чатам')
async def parser_menu_handler(message: Message, state: FSMContext, db):
    """Главное меню парсера."""
    await state.clear()
    
    session = db.get_parser_session()
    is_connected = bool(session and session.get('is_active'))
    
    text = format_parser_menu(session)
    await message.answer(text, reply_markup=parser_kb.parser_main_menu(is_connected))


@router.callback_query(F.data == 'parser:menu')
async def parser_menu_callback(call: CallbackQuery, state: FSMContext, db):
    """Возврат в главное меню парсера."""
    await state.clear()
    
    session = db.get_parser_session()
    is_connected = bool(session and session.get('is_active'))
    
    text = format_parser_menu(session)
    await call.message.edit_text(text, reply_markup=parser_kb.parser_main_menu(is_connected))
    await call.answer()


@router.callback_query(F.data == 'parser:connect')
async def parser_connect_handler(call: CallbackQuery, state: FSMContext):
    """Начало подключения аккаунта."""
    await state.set_state(ParserStates.waiting_phone)
    
    text = (
        '🔐 <b>Подключение аккаунта</b>\n\n'
        'Введите номер телефона в международном формате:\n'
        '<code>+79991234567</code>\n\n'
        '⚠️ Это должен быть ваш второй Telegram-аккаунт для парсинга'
    )
    
    await call.message.edit_text(text, reply_markup=parser_kb.back_to_parser())
    await call.answer()


@router.message(ParserStates.waiting_phone)
async def parser_phone_handler(message: Message, state: FSMContext, db):
    """Обработка номера телефона."""
    global parser_client
    
    if message.text == '✖️ Отмена':
        await state.clear()
        await parser_menu_handler(message, state, db)
        return
    
    phone = message.text.strip()
    
    if not phone.startswith('+'):
        await message.answer('❌ Номер должен начинаться с +\n\nПопробуйте снова:')
        return
    
    try:
        # Создаём клиента и отправляем код
        parser_client = ParserClient()
        await parser_client.start_auth(phone)
        
        await state.update_data(phone=phone)
        await state.set_state(ParserStates.waiting_code)
        
        text = (
            '📩 <b>Код отправлен!</b>\n\n'
            f'Telegram отправил код авторизации на номер:\n'
            f'<code>{phone}</code>\n\n'
            'Введите код из Telegram:'
        )
        
        await message.answer(text, reply_markup=parser_kb.parser_cancel_keyboard())
        
    except Exception as e:
        log.error(f'Ошибка при отправке кода: {e}')
        await message.answer(
            f'❌ Ошибка при отправке кода:\n{str(e)}\n\nПопробуйте снова или нажмите ✖️ Отмена'
        )


@router.message(ParserStates.waiting_code)
async def parser_code_handler(message: Message, state: FSMContext, db):
    """Обработка кода из Telegram."""
    global parser_client
    
    if message.text == '✖️ Отмена':
        if parser_client:
            await parser_client.disconnect()
        await state.clear()
        await parser_menu_handler(message, state, db)
        return
    
    code = message.text.strip().replace('-', '').replace(' ', '')
    
    try:
        result = await parser_client.verify_code(code)
        
        if result.get('need_2fa'):
            await state.set_state(ParserStates.waiting_2fa)
            text = (
                '🔐 <b>Требуется пароль 2FA</b>\n\n'
                'Введите пароль двухфакторной аутентификации:'
            )
            await message.answer(text, reply_markup=parser_kb.parser_cancel_keyboard())
            
        elif result.get('success'):
            # Успешная авторизация
            await save_session(message, state, db, parser_client)
            
        elif result.get('error') == 'invalid_code':
            await message.answer('❌ Неверный код.\n\nПопробуйте снова:')
            
        else:
            await message.answer(f'❌ Ошибка: {result.get("error")}\n\nПопробуйте снова:')
            
    except Exception as e:
        log.error(f'Ошибка при проверке кода: {e}')
        await message.answer(f'❌ Ошибка: {str(e)}\n\nПопробуйте снова:')


@router.message(ParserStates.waiting_2fa)
async def parser_2fa_handler(message: Message, state: FSMContext, db):
    """Обработка 2FA пароля."""
    global parser_client
    
    if message.text == '✖️ Отмена':
        if parser_client:
            await parser_client.disconnect()
        await state.clear()
        await parser_menu_handler(message, state, db)
        return
    
    password = message.text.strip()
    
    try:
        result = await parser_client.verify_2fa(password)
        
        if result.get('success'):
            await save_session(message, state, db, parser_client)
        elif result.get('error') == 'invalid_password':
            await message.answer('❌ Неверный пароль 2FA.\n\nПопробуйте снова:')
        else:
            await message.answer(f'❌ Ошибка: {result.get("error")}\n\nПопробуйте снова:')
            
    except Exception as e:
        log.error(f'Ошибка при проверке 2FA: {e}')
        await message.answer(f'❌ Ошибка: {str(e)}\n\nПопробуйте снова:')


async def save_session(message: Message, state: FSMContext, db, client: ParserClient):
    """Сохранение сессии после успешной авторизации."""
    try:
        session_string = await client.get_session_string()
        me = await client.get_me()
        
        data = await state.get_data()
        phone = data.get('phone')
        
        # Сохраняем в БД
        db.save_parser_session(
            owner_id=message.from_user.id,
            session_string=session_string,
            phone=phone,
            username=me.get('username'),
            user_id=me.get('user_id'),
            first_name=me.get('first_name')
        )
        
        # Загружаем список чатов
        await load_chats(client, db)
        
        await state.clear()
        
        text = (
            '✅ <b>Аккаунт успешно подключён!</b>\n\n'
            f'👤 @{me.get("username", "без username")}\n'
            f'🆔 ID: <code>{me.get("user_id")}</code>\n'
            f'🔹 {me.get("first_name")}\n\n'
            'Теперь этот аккаунт будет использоваться для парсинга чатов.'
        )
        
        await message.answer(text, reply_markup=parser_kb.parser_main_menu(is_connected=True))
        
    except Exception as e:
        log.error(f'Ошибка при сохранении сессии: {e}')
        await message.answer(f'❌ Ошибка при сохранении сессии: {str(e)}')


async def load_chats(client: ParserClient, db):
    """Загрузка списка чатов из Telegram."""
    try:
        dialogs = await client.get_dialogs()
        
        for dialog in dialogs:
            db.add_parser_chat(
                chat_id=dialog['chat_id'],
                title=dialog['title'],
                username=dialog.get('username'),
                chat_type=dialog['type'],
                total_participants=dialog.get('participants_count', 0)
            )
        
        log.info(f'Загружено {len(dialogs)} чатов')
        
    except Exception as e:
        log.error(f'Ошибка при загрузке чатов: {e}')


@router.callback_query(F.data == 'parser:chats')
async def parser_chats_handler(call: CallbackQuery, db):
    """Показать список чатов."""
    chats = db.get_parser_chats()
    
    if not chats:
        text = '📂 <b>Чаты не найдены</b>\n\nОбновите список чатов'
        await call.message.edit_text(text, reply_markup=parser_kb.back_to_parser())
    else:
        text = (
            f'📂 <b>Мои чаты ({len(chats)})</b>\n\n'
            'Нажмите на чат, чтобы включить/выключить его для парсинга:\n'
            '✅ — включен\n'
            '❌ — выключен'
        )
        await call.message.edit_text(text, reply_markup=parser_kb.parser_chats_list(chats))
    
    await call.answer()


@router.callback_query(F.data.startswith('parser:toggle:'))
async def parser_toggle_chat(call: CallbackQuery, db):
    """Включить/выключить чат."""
    chat_id = int(call.data.split(':')[2])
    
    db.toggle_parser_chat(chat_id)
    
    # Обновляем список
    chats = db.get_parser_chats()
    text = (
        f'📂 <b>Мои чаты ({len(chats)})</b>\n\n'
        'Нажмите на чат, чтобы включить/выключить его для парсинга:\n'
        '✅ — включен\n'
        '❌ — выключен'
    )
    await call.message.edit_text(text, reply_markup=parser_kb.parser_chats_list(chats))
    await call.answer('✅ Изменено')


@router.callback_query(F.data == 'parser:refresh_chats')
async def parser_refresh_chats(call: CallbackQuery, db):
    """Обновить список чатов."""
    global parser_client
    
    session = db.get_parser_session()
    if not session or not session.get('session_string'):
        await call.answer('❌ Аккаунт не подключён', show_alert=True)
        return
    
    try:
        await call.answer('🔄 Обновление...')
        
        if not parser_client or not parser_client.is_connected():
            parser_client = ParserClient(session['session_string'])
            await parser_client.connect()
        
        await load_chats(parser_client, db)
        
        chats = db.get_parser_chats()
        text = (
            f'📂 <b>Мои чаты ({len(chats)})</b>\n\n'
            'Список обновлён!\n\n'
            'Нажмите на чат, чтобы включить/выключить его для парсинга:\n'
            '✅ — включен\n'
            '❌ — выключен'
        )
        await call.message.edit_text(text, reply_markup=parser_kb.parser_chats_list(chats))
        
    except Exception as e:
        log.error(f'Ошибка при обновлении чатов: {e}')
        await call.answer(f'❌ Ошибка: {str(e)}', show_alert=True)


@router.callback_query(F.data == 'parser:stats')
async def parser_stats_handler(call: CallbackQuery, db):
    """Показать статистику парсера."""
    stats = db.get_parser_stats()
    
    text = (
        '📊 <b>Статистика парсинга</b>\n\n'
        f'📂 Чатов: <b>{stats["total_chats"]}</b>\n'
        f'✅ Активных: <b>{stats["enabled_chats"]}</b>\n\n'
        f'👥 Пользователей проверено: <b>{stats["total_users"]}</b>\n'
        f'🎁 С подарками: <b>{stats["users_with_gifts"]}</b>\n\n'
        f'💎 Всего подарков: <b>{stats["total_gifts"]}</b>\n'
        f'✨ Неулучшенных: <b>{stats["unupgraded_gifts"]}</b>'
    )
    
    await call.message.edit_text(text, reply_markup=parser_kb.back_to_parser())
    await call.answer()


@router.callback_query(F.data == 'parser:start')
async def parser_start_handler(call: CallbackQuery, db):
    """Запустить парсинг."""
    global parser_client, scanner, scan_task
    
    session = db.get_parser_session()
    if not session or not session.get('session_string'):
        await call.answer('❌ Аккаунт не подключён', show_alert=True)
        return
    
    enabled_chats = db.get_enabled_parser_chats()
    if not enabled_chats:
        await call.answer('❌ Нет активных чатов', show_alert=True)
        return
    
    if scanner and scanner.is_running:
        await call.answer('⚠️ Парсинг уже запущен', show_alert=True)
        return
    
    try:
        # Подключаем клиента
        if not parser_client or not parser_client.is_connected():
            parser_client = ParserClient(session['session_string'])
            await parser_client.connect()
        
        # Создаём сканер
        scanner = ChatScanner(parser_client, db)
        
        # Запускаем в фоне
        scan_task = asyncio.create_task(scanner.start_scan(mode='all'))
        
        text = (
            '🔎 <b>Парсинг запущен!</b>\n\n'
            f'📂 Активных чатов: <b>{len(enabled_chats)}</b>\n\n'
            'Идёт сканирование участников...'
        )
        
        await call.message.edit_text(text, reply_markup=parser_kb.parser_running_keyboard())
        await call.answer('✅ Запущено')
        
        # Мониторим прогресс
        asyncio.create_task(monitor_scan_progress(call.message, scanner, db))
        
    except Exception as e:
        log.error(f'Ошибка при запуске парсинга: {e}')
        await call.answer(f'❌ Ошибка: {str(e)}', show_alert=True)


async def monitor_scan_progress(message: Message, scanner: ChatScanner, db):
    """Мониторинг прогресса сканирования."""
    while scanner.is_running:
        await asyncio.sleep(5)
        
        stats = scanner.get_stats()
        
        text = (
            '🔎 <b>Парсинг идёт</b>\n\n'
            f'📂 Чат: <b>{stats["current_chat_title"]}</b>\n'
            f'🔍 Проверено: <b>{stats["chat_checked"]}</b>\n'
            f'🎁 Найдено: <b>{stats["chat_found"]}</b>\n\n'
            f'📊 Всего проверено: <b>{stats["total_checked"]}</b>\n'
            f'💎 Всего найдено: <b>{stats["total_found"]}</b>'
        )
        
        try:
            await message.edit_text(text, reply_markup=parser_kb.parser_running_keyboard())
        except:
            pass
    
    # Финальный результат
    stats = scanner.get_stats()
    text = (
        '✅ <b>Парсинг завершён!</b>\n\n'
        f'📊 Проверено пользователей: <b>{stats["total_checked"]}</b>\n'
        f'🎁 Найдено с подарками: <b>{stats["total_found"]}</b>\n\n'
        'Результаты сохранены в статистике'
    )
    
    await message.edit_text(text, reply_markup=parser_kb.back_to_parser())


@router.callback_query(F.data == 'parser:stop')
async def parser_stop_handler(call: CallbackQuery):
    """Остановить парсинг."""
    global scanner
    
    if scanner and scanner.is_running:
        scanner.stop()
        await call.answer('⏹ Остановка...')
    else:
        await call.answer('⚠️ Парсинг не запущен')


@router.callback_query(F.data == 'parser:account')
async def parser_account_handler(call: CallbackQuery, db):
    """Информация об аккаунте."""
    session = db.get_parser_session()
    
    if not session:
        await call.answer('❌ Аккаунт не подключён', show_alert=True)
        return
    
    text = (
        '⚙️ <b>Настройки аккаунта</b>\n\n'
        f'👤 @{session.get("username", "без username")}\n'
        f'🆔 ID: <code>{session.get("user_id")}</code>\n'
        f'🔹 {session.get("first_name")}\n'
        f'📱 {session.get("phone")}'
    )
    
    await call.message.edit_text(text, reply_markup=parser_kb.parser_account_menu())
    await call.answer()


@router.callback_query(F.data == 'parser:disconnect')
async def parser_disconnect_handler(call: CallbackQuery, db):
    """Отключить аккаунт."""
    global parser_client
    
    if parser_client:
        await parser_client.disconnect()
        parser_client = None
    
    db.clear_parser_session()
    
    text = (
        '🚪 <b>Аккаунт отключён</b>\n\n'
        'Сессия удалена. Для возобновления работы подключите аккаунт заново.'
    )
    
    await call.message.edit_text(text, reply_markup=parser_kb.parser_main_menu(is_connected=False))
    await call.answer('✅ Отключено')


@router.callback_query(F.data == 'parser:reconnect')
async def parser_reconnect_handler(call: CallbackQuery, state: FSMContext):
    """Переподключить аккаунт."""
    global parser_client
    
    if parser_client:
        await parser_client.disconnect()
        parser_client = None
    
    await parser_connect_handler(call, state)
