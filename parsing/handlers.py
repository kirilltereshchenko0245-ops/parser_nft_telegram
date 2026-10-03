"""Telegram handlers for the MTProto chat/NFT parser."""
import asyncio
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon.errors import FloodWaitError

from parsing.telegram_client import ParserClient
from parsing.scanner import ChatScanner
from parsing import keyboards as parser_kb

log = logging.getLogger('giftparser.parser')
router = Router()


class ParserStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()
    waiting_limit = State()


parser_client = None
scanner = None
scan_task = None


def _session_owned(session, uid):
    return bool(session and int(session.get('owner_id') or 0) == int(uid))


def format_parser_menu(session_data, mode='all'):
    if session_data and session_data.get('is_active'):
        username = session_data.get('username') or 'Без username'
        return (
            '🔎 <b>ПАРСИНГ ПО ЧАТАМ</b>\n\n'
            '🟢 <b>Аккаунт подключён</b>\n'
            f'👤 @{username}\n'
            f'🆔 <code>{session_data.get("user_id")}</code>\n\n'
            'Выбери режим, затем количество новых профилей для проверки.\n'
            'Проверка идёт последовательно и с паузами: качество важнее скорости.'
        )
    return '🔎 <b>ПАРСИНГ ПО ЧАТАМ</b>\n\n🔴 <b>Аккаунт не подключён</b>\n\nПодключи свой Telegram-аккаунт для работы парсера.'


async def ensure_client(db):
    global parser_client
    session = db.get_parser_session()
    if not session or not session.get('session_string'):
        raise RuntimeError('Аккаунт не подключён')
    if parser_client and parser_client.is_connected():
        return parser_client
    parser_client = ParserClient(session['session_string'])
    await parser_client.connect()
    db.set_parser_active(True)
    return parser_client


async def restore_saved_parser_session(db):
    """Restore StringSession at bot startup; no phone/code request is made."""
    global parser_client
    session = db.get_parser_session()
    if not session or not session.get('session_string'):
        return False
    try:
        parser_client = ParserClient(session['session_string'])
        await parser_client.connect()
        db.set_parser_active(True)
        log.info('Saved parser account restored automatically')
        return True
    except Exception as e:
        log.warning('Saved parser session could not be restored: %s', e)
        db.set_parser_active(False)
        parser_client = None
        return False


@router.message(F.text == '🔎 Парсинг по чатам')
async def parser_menu_handler(message: Message, state: FSMContext, db):
    await state.clear()
    session = db.get_parser_session()
    if session and session.get('is_active'):
        try:
            await ensure_client(db)
        except Exception:
            db.set_parser_active(False)
            session = db.get_parser_session()
    await message.answer(format_parser_menu(session), reply_markup=parser_kb.parser_main_menu(bool(session and session.get('is_active'))))


@router.callback_query(F.data == 'parser:menu')
async def parser_menu_callback(call: CallbackQuery, state: FSMContext, db):
    await state.clear()
    session = db.get_parser_session()
    await call.message.edit_text(format_parser_menu(session), reply_markup=parser_kb.parser_main_menu(bool(session and session.get('is_active'))))
    await call.answer()


@router.callback_query(F.data == 'parser:connect')
async def parser_connect_handler(call: CallbackQuery, state: FSMContext, db):
    session = db.get_parser_session()
    if session and session.get('session_string') and _session_owned(session, call.from_user.id):
        try:
            await ensure_client(db)
            await call.message.edit_text('🟢 <b>Аккаунт уже подключён.</b>\n\nНовая авторизация не требуется.', reply_markup=parser_kb.parser_main_menu(True))
            return await call.answer('Уже подключён')
        except Exception:
            pass
    await state.set_state(ParserStates.waiting_phone)
    await call.message.edit_text('🔐 <b>Подключение аккаунта</b>\n\nВведите номер в международном формате:\n<code>+79991234567</code>\n\nПосле успешного входа сессия сохранится и новый код при каждом запуске не потребуется.', reply_markup=parser_kb.back_to_parser())
    await call.answer()


@router.message(ParserStates.waiting_phone)
async def parser_phone_handler(message: Message, state: FSMContext, db):
    global parser_client
    if message.text == '✖️ Отмена':
        await state.clear(); return await parser_menu_handler(message, state, db)
    phone = (message.text or '').strip()
    if not phone.startswith('+'):
        return await message.answer('❌ Номер должен начинаться с +')
    try:
        parser_client = ParserClient()
        await parser_client.start_auth(phone)
        db.save_pending_parser_auth(message.from_user.id, phone, parser_client._phone_code_hash)
        await state.update_data(phone=phone)
        await state.set_state(ParserStates.waiting_code)
        await message.answer('📩 <b>Код отправлен</b>\n\nВведите код из Telegram.\n\n⚠️ Новый код автоматически запрашиваться не будет.', reply_markup=parser_kb.parser_cancel_keyboard())
    except Exception as e:
        log.exception('Failed to send auth code')
        await message.answer(f'❌ Не удалось отправить код: <code>{str(e)[:500]}</code>')


@router.message(ParserStates.waiting_code)
async def parser_code_handler(message: Message, state: FSMContext, db):
    global parser_client
    if message.text == '✖️ Отмена':
        if parser_client: await parser_client.disconnect()
        await state.clear(); return await parser_menu_handler(message, state, db)
    if not parser_client:
        pending = db.get_parser_session()
        if pending and pending.get('auth_phone') and pending.get('auth_phone_code_hash') and int(pending.get('owner_id') or 0) == int(message.from_user.id):
            parser_client = ParserClient()
            await parser_client.start_auth(pending['auth_phone'], pending['auth_phone_code_hash'])
        else:
            await state.clear(); return await message.answer('❌ Активная попытка входа не найдена. Нажмите подключение и запросите один новый код.')
    code = ''.join(ch for ch in (message.text or '') if ch.isdigit())
    result = await parser_client.verify_code(code)
    if result.get('need_2fa'):
        await state.set_state(ParserStates.waiting_2fa)
        return await message.answer('🔐 <b>Требуется пароль 2FA</b>\n\nВведите пароль:', reply_markup=parser_kb.parser_cancel_keyboard())
    if result.get('success'):
        return await save_session(message, state, db, parser_client)
    if result.get('error') == 'invalid_code':
        return await message.answer('❌ Код неверный. Введите тот же полученный код ещё раз.')
    if result.get('error') == 'code_expired':
        return await message.answer('⌛ <b>Код истёк.</b>\n\nНажмите ✖️ Отмена и начните подключение заново. Новый код сам по себе не запрашивается.')
    await message.answer(f'❌ Ошибка: {result.get("message", result.get("error", "неизвестная ошибка"))}')


@router.message(ParserStates.waiting_2fa)
async def parser_2fa_handler(message: Message, state: FSMContext, db):
    if message.text == '✖️ Отмена':
        if parser_client: await parser_client.disconnect()
        await state.clear(); return await parser_menu_handler(message, state, db)
    result = await parser_client.verify_2fa((message.text or '').strip())
    if result.get('success'):
        return await save_session(message, state, db, parser_client)
    await message.answer('❌ Неверный пароль 2FA.' if result.get('error') == 'invalid_password' else f'❌ Ошибка: {result.get("error")}')


async def save_session(message, state, db, client):
    global parser_client
    session_string = await client.get_session_string()
    me = await client.get_me()
    if not session_string or not me:
        return await message.answer('❌ Telegram не вернул сохранённую сессию. Попробуйте подключить аккаунт ещё раз.')
    data = await state.get_data()
    db.save_parser_session(message.from_user.id, session_string, data.get('phone'), me.get('username'), me.get('user_id'), me.get('first_name'))
    db.clear_pending_parser_auth()
    db.set_parser_active(True)
    await load_chats(client, db)
    await state.clear()
    await message.answer(
        '✅ <b>АККАУНТ ПОДКЛЮЧЁН</b>\n\n'
        f'👤 @{me.get("username") or "без username"}\n'
        f'🆔 <code>{me.get("user_id")}</code>\n\n'
        '🔐 Сессия сохранена. При следующем запуске новый код не понадобится.',
        reply_markup=parser_kb.parser_main_menu(True)
    )


async def load_chats(client, db):
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        db.add_parser_chat(dialog['chat_id'], dialog['title'], dialog.get('username'), dialog['type'], dialog.get('participants_count', 0))
    return len(dialogs)


async def _show_chats(call, db, page=0):
    chats = db.get_parser_chats()
    enabled = sum(1 for x in chats if x['is_enabled'])
    text = f'📂 <b>МОИ ЧАТЫ</b>\n\n🟢 Включено: <b>{enabled}</b>\n⚪ Выключено: <b>{len(chats)-enabled}</b>\n\nНажми на чат, чтобы включить или выключить его.'
    await call.message.edit_text(text, reply_markup=parser_kb.parser_chats_list(chats, page))


@router.callback_query(F.data == 'parser:chats')
async def parser_chats_handler(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await _show_chats(call, db, 0); await call.answer()


@router.callback_query(F.data.startswith('parser:chats:'))
async def parser_chats_page(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    await _show_chats(call, db, int(call.data.split(':')[2])); await call.answer()


@router.callback_query(F.data.startswith('parser:toggle:'))
async def parser_toggle_chat(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    db.toggle_parser_chat(int(call.data.split(':')[2])); await _show_chats(call, db, 0); await call.answer('Изменено')


@router.callback_query(F.data.startswith('parser:chats_all:'))
async def parser_toggle_all(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    value = call.data.endswith(':on')
    db.set_all_parser_chats(value)
    await _show_chats(call, db, 0); await call.answer('Обновлено')


@router.callback_query(F.data == 'parser:refresh_chats')
async def parser_refresh_chats(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    try:
        client = await ensure_client(db)
        await load_chats(client, db)
        await _show_chats(call, db, 0); await call.answer('Список обновлён')
    except Exception as e:
        await call.answer(f'Ошибка: {str(e)[:150]}', show_alert=True)


@router.callback_query(F.data == 'parser:stats')
async def parser_stats_handler(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    s = db.get_parser_stats()
    text = ('📊 <b>СТАТИСТИКА</b>\n\n'
            f'📂 Чатов: <b>{s["total_chats"]}</b>\n'
            f'🟢 Активных: <b>{s["enabled_chats"]}</b>\n\n'
            f'👥 Проверено профилей: <b>{s["total_users"]}</b>\n'
            f'🎁 Пользователей с подарками: <b>{s["users_with_gifts"]}</b>\n'
            f'💎 Подарков найдено: <b>{s["total_gifts"]}</b>\n'
            f'✨ Неулучшенных: <b>{s["unupgraded_gifts"]}</b>')
    await call.message.edit_text(text, reply_markup=parser_kb.back_to_parser()); await call.answer()


@router.callback_query(F.data.startswith('parser:mode:'))
async def parser_mode_handler(call: CallbackQuery, state: FSMContext, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    mode = call.data.split(':')[2]
    await state.update_data(scan_mode=mode)
    label = '🎁 Любые NFT' if mode == 'all' else '💎 Только неулучшенные NFT'
    await call.message.edit_text(f'⚙️ <b>РЕЖИМ ВЫБРАН</b>\n\n{label}\n\nСколько новых профилей тщательно проверить?', reply_markup=parser_kb.parser_limit_menu(mode))
    await call.answer()


@router.callback_query(F.data == 'parser:start')
async def parser_start_handler(call: CallbackQuery, state: FSMContext, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    try:
        await ensure_client(db)
    except Exception as e:
        return await call.answer(str(e), show_alert=True)
    if not db.get_enabled_parser_chats(): return await call.answer('❌ Нет включённых чатов', show_alert=True)
    data = await state.get_data(); mode = data.get('scan_mode', 'all')
    await call.message.edit_text('👥 <b>СКОЛЬКО ПРОФИЛЕЙ ПРОВЕРИТЬ?</b>\n\nПарсер будет идти медленно и последовательно, с адаптивными паузами.', reply_markup=parser_kb.parser_limit_menu(mode))
    await call.answer()


@router.callback_query(F.data.startswith('parser:limit:'))
async def parser_limit_handler(call: CallbackQuery, state: FSMContext, db):
    value = call.data.split(':')[2]
    if value == 'custom':
        await state.set_state(ParserStates.waiting_limit)
        return await call.message.edit_text('✏️ <b>Введите количество профилей</b>\n\nНапример: <code>350</code>\nМаксимум за один запуск: <code>5000</code>.', reply_markup=parser_kb.parser_cancel_keyboard())
    await start_scan_with_limit(call, call.from_user.id, state, db, int(value), call)


@router.message(ParserStates.waiting_limit)
async def parser_custom_limit(message: Message, state: FSMContext, db):
    if message.text == '✖️ Отмена':
        await state.clear(); return await parser_menu_handler(message, state, db)
    try:
        limit = int((message.text or '').strip())
        if not 1 <= limit <= 5000: raise ValueError
    except ValueError:
        return await message.answer('❌ Введи целое число от 1 до 5000.')
    await start_scan_with_limit(message, message.from_user.id, state, db, limit, None)


async def start_scan_with_limit(message, uid, state, db, limit, call):
    global parser_client, scanner, scan_task
    if scanner and scanner.is_running:
        if call: await call.answer('⚠️ Парсинг уже идёт', show_alert=True)
        return
    data = await state.get_data(); mode = data.get('scan_mode', 'all')
    await state.clear()
    client = await ensure_client(db)
    enabled = db.get_enabled_parser_chats()

    origin_message = message.message if isinstance(message, CallbackQuery) else message

    async def on_result(user, gifts):
        for gift in gifts[:5]:
            card = format_gift_card(user, gift)
            await origin_message.answer(card, reply_markup=parser_kb.parser_gift_kb({**gift, 'username': user.get('username'), 'user_id': user.get('user_id')}))

    scanner = ChatScanner(client, db, result_callback=on_result)
    scan_task = asyncio.create_task(scanner.start_scan(mode=mode, max_users=limit))
    label = '🎁 любые NFT' if mode == 'all' else '💎 неулучшенные NFT'
    status_text = (f'🔎 <b>ПАРСИНГ ЗАПУЩЕН</b>\n\nРежим: <b>{label}</b>\n👥 Лимит: <b>{limit}</b> профилей\n\n🐢 Проверка последовательная и тщательная.\n⏳ Между профилями будут паузы, а при FloodWait Telegram парсер дождётся разрешённого времени.')
    if isinstance(message, CallbackQuery):
        status_message = message.message
        await status_message.edit_text(status_text, reply_markup=parser_kb.parser_running_keyboard())
    else:
        status_message = await message.answer(status_text, reply_markup=parser_kb.parser_running_keyboard())
    if call: await call.answer('Запущено')
    asyncio.create_task(monitor_scan_progress(status_message, scanner, db))


def format_gift_card(user, gift):
    username = f'@{user.get("username")}' if user.get('username') else 'без username'
    status = '💎 Неулучшенный' if not gift.get('is_upgraded') else '✨ Улучшенный / collectible'
    link = f'https://t.me/nft/{gift["gift_slug"]}' if gift.get('gift_slug') else 'ссылка недоступна для обычного подарка'
    return ('🎁 <b>НАЙДЕН ПОДАРОК</b>\n\n'
            f'👤 Владелец: <b>{username}</b>\n'
            f'🆔 Telegram ID: <code>{user.get("user_id")}</code>\n\n'
            f'🎁 Подарок: <b>{gift.get("gift_name") or "Telegram Gift"}</b>\n'
            f'🔢 ID типа: <code>{gift.get("gift_id") or "—"}</code>\n'
            f'🔢 Номер: <code>{gift.get("gift_num") or "—"}</code>\n'
            f'📌 Статус: <b>{status}</b>\n\n'
            f'🔗 {link}')


async def monitor_scan_progress(message, scanner, db):
    while scanner.is_running:
        await asyncio.sleep(5)
        s = scanner.get_stats()
        elapsed = datetime_delta(s.get('start_time'))
        text = ('🔎 <b>ПАРСИНГ ИДЁТ</b>\n\n'
                f'📂 Чат: <b>{s["current_chat_title"]}</b>\n'
                f'👥 Проверено: <b>{s["total_checked"]}/{s["limit"]}</b>\n'
                f'🎁 Пользователей с NFT: <b>{s["total_found"]}</b>\n'
                f'✨ Новых подарков: <b>{s["total_new_gifts"]}</b>\n'
                f'⏱ Время: <b>{elapsed}</b>\n\n'
                '🐢 Режим: тщательная последовательная проверка')
        try: await message.edit_text(text, reply_markup=parser_kb.parser_running_keyboard())
        except Exception: pass
    s = scanner.get_stats()
    await message.edit_text(('✅ <b>ПАРСИНГ ЗАВЕРШЁН</b>\n\n'
        f'👥 Проверено профилей: <b>{s["total_checked"]}</b>\n'
        f'🎁 Профилей с подарками: <b>{s["total_found"]}</b>\n'
        f'✨ Новых подарков: <b>{s["total_new_gifts"]}</b>\n\n'
        'Результаты сохранены в базе и не будут повторно показаны как новые.'), reply_markup=parser_kb.back_to_parser())


def datetime_delta(dt):
    if not dt: return '—'
    return str(datetime.now() - dt).split('.')[0]


@router.callback_query(F.data == 'parser:stop')
async def parser_stop_handler(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    if scanner and scanner.is_running:
        scanner.stop(); await call.answer('⏹ Останавливаю…')
    else: await call.answer('Парсинг не запущен')


@router.callback_query(F.data == 'parser:account')
async def parser_account_handler(call, db):
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    text = ('⚙️ <b>АККАУНТ ПАРСЕРА</b>\n\n'
            f'👤 @{session.get("username") or "без username"}\n'
            f'🆔 <code>{session.get("user_id")}</code>\n'
            f'📱 <code>{session.get("phone") or "—"}</code>\n\n'
            '🟢 Сохранённая StringSession используется автоматически.')
    await call.message.edit_text(text, reply_markup=parser_kb.parser_account_menu()); await call.answer()


@router.callback_query(F.data == 'parser:reconnect_existing')
async def parser_reconnect_existing(call, db):
    try:
        await ensure_client(db); await call.message.edit_text('🟢 <b>Подключение восстановлено.</b>\n\nНовый код не запрашивался.', reply_markup=parser_kb.parser_main_menu(True)); await call.answer('Подключено')
    except Exception as e:
        await call.answer(str(e)[:180], show_alert=True)


@router.callback_query(F.data == 'parser:disconnect')
async def parser_disconnect_handler(call, db):
    global parser_client
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id): return await call.answer('Нет доступа', show_alert=True)
    if parser_client: await parser_client.disconnect()
    parser_client = None
    db.clear_parser_session()
    await call.message.edit_text('🚪 <b>Аккаунт отключён</b>\n\nСессия удалена из базы. Для нового подключения потребуется код.', reply_markup=parser_kb.parser_main_menu(False)); await call.answer('Отключено')
