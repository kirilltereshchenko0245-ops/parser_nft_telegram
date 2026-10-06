"""Telegram handlers for the MTProto chat/NFT parser."""
import asyncio
import logging
import os
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
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

def _parser_access_allowed(db, uid):
    # Единая проверка: владелец/админы всегда имеют доступ; при закрытом
    # режиме доступ выдаётся адресно через админку. Старая привязка только
    # к владельцу parser-сессии больше не блокирует выданных пользователей.
    owner_id = 8564624250
    return db.parser_access_allowed(uid, owner_id)



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
    """Restore the pre-authorized account from env/DB without requesting a code."""
    global parser_client
    session = db.get_parser_session()
    env_session = os.getenv('PARSER_SESSION_STRING', '').strip()
    if env_session and (not session or session.get('session_string') != env_session):
        # Seed the DB from the configured pre-authorized session.
        try:
            probe = ParserClient(env_session)
            await probe.connect()
            me = await probe.get_me()
            if not me:
                raise RuntimeError('Не удалось получить профиль Telegram')
            owner_id = 8564624250
            db.save_parser_session(owner_id, env_session, me.get('phone'), me.get('username'), me.get('user_id'), me.get('first_name'))
            await probe.disconnect()
            session = db.get_parser_session()
            log.info('Pre-authorized parser session loaded from PARSER_SESSION_STRING')
        except Exception as e:
            log.exception('Configured PARSER_SESSION_STRING is invalid: %s', e)
            db.set_parser_active(False) if session else None
            return False
    if not session or not session.get('session_string'):
        return False
    try:
        parser_client = ParserClient(session['session_string'])
        await parser_client.connect()
        db.set_parser_active(True)
        await load_chats(parser_client, db)
        log.info('Parser account restored automatically')
        return True
    except Exception as e:
        log.warning('Parser session could not be restored: %s', e)
        db.set_parser_active(False)
        parser_client = None
        return False


@router.message(F.text == '🔎 Парсинг по чатам')
async def parser_menu_handler(message: Message, state: FSMContext, db):
    if not _parser_access_allowed(db, message.from_user.id):
        return await message.answer('🔒 Парсер доступен только администратору.')
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
    if not _parser_access_allowed(db, call.from_user.id):
        return await call.answer('Только для администратора', show_alert=True)
    await state.clear()
    session = db.get_parser_session()
    await call.message.edit_text(format_parser_menu(session), reply_markup=parser_kb.parser_main_menu(bool(session and session.get('is_active'))))
    await call.answer()


@router.callback_query(F.data == 'parser:connect')
async def parser_connect_handler(call: CallbackQuery, state: FSMContext, db):
    """Connect the pre-authorized parser account from PARSER_SESSION_STRING.

    Interactive phone/code login is intentionally disabled in this deployment.
    The Telegram account must be authorized beforehand and supplied as a Telethon
    StringSession through the PARSER_SESSION_STRING environment variable.
    """
    session = db.get_parser_session()
    if session and session.get('session_string') and _session_owned(session, call.from_user.id):
        try:
            await ensure_client(db)
            await call.message.edit_text(
                '🟢 <b>Аккаунт уже подключён.</b>\n\nНовая авторизация не требуется.',
                reply_markup=parser_kb.parser_main_menu(True),
            )
            return await call.answer('Уже подключён')
        except Exception:
            db.set_parser_active(False)

    configured = os.getenv('PARSER_SESSION_STRING', '').strip()
    if not configured:
        return await call.answer('PARSER_SESSION_STRING не настроен', show_alert=True)
    try:
        global parser_client
        parser_client = ParserClient(configured)
        await parser_client.connect()
        me = await parser_client.get_me()
        if not me:
            raise RuntimeError('Не удалось получить профиль Telegram')
        db.save_parser_session(call.from_user.id, configured, me.get('phone'), me.get('username'), me.get('user_id'), me.get('first_name'))
        db.set_parser_active(True)
        await load_chats(parser_client, db)
        await state.clear()
        await call.message.edit_text(
            '✅ <b>АККАУНТ ПОДКЛЮЧЁН</b>\n\n'
            f'👤 @{me.get("username") or "без username"}\n'
            f'🆔 <code>{me.get("user_id")}</code>\n\n'
            '🔐 Используется заранее авторизованная Telegram-сессия.\n'
            'Код подтверждения больше не нужен.',
            reply_markup=parser_kb.parser_main_menu(True),
        )
        await call.answer('Аккаунт подключён')
    except Exception as e:
        log.exception('Pre-authorized session connection failed')
        await call.answer(f'Ошибка сессии: {str(e)[:180]}', show_alert=True)


# Phone/code/2FA handlers intentionally removed.
# Parser authentication is performed once outside the bot and injected as
# PARSER_SESSION_STRING.

async def resume_interrupted_scan(db):
    """Resume a scan left in 'running' state after a container/process restart."""
    global scanner, scan_task
    state = db.get_parser_scan_state()
    if not state or state.get('status') != 'running':
        return False
    session = db.get_parser_session()
    if not session or not session.get('session_string'):
        return False
    try:
        client = await ensure_client(db)
        owner_id = int(state['owner_id'])
        mode = state.get('mode') or 'all'
        limit = int(state.get('max_users') or 100)
        async def on_result(user, gifts):
            for gift in gifts[:5]:
                try:
                    card = format_gift_card(user, gift)
                    await bot.send_message(owner_id, card, reply_markup=parser_kb.parser_gift_kb({**gift, 'username': user.get('username'), 'user_id': user.get('user_id')}))
                except Exception:
                    log.exception('Failed to send resumed result')
        scanner = ChatScanner(client, db, result_callback=on_result)
        scan_task = asyncio.create_task(scanner.start_scan(mode=mode, max_users=limit))
        log.info('Resumed interrupted parser scan: owner=%s mode=%s limit=%s', owner_id, mode, limit)
        return True
    except Exception:
        log.exception('Failed to resume parser scan')
        return False


async def save_session(message, state, db, client):
    global parser_client
    session_string = await client.get_session_string()
    me = await client.get_me()
    if not session_string or not me:
        return await message.answer('❌ Telegram не вернул сохранённую сессию.')
    data = await state.get_data()
    db.save_parser_session(message.from_user.id, session_string, data.get('phone'), me.get('username'), me.get('user_id'), me.get('first_name'))
    db.set_parser_active(True)
    await load_chats(client, db)
    await state.clear()
    await message.answer(
        '✅ <b>АККАУНТ ПОДКЛЮЧЁН</b>\n\n'
        f'👤 @{me.get("username") or "без username"}\n'
        f'🆔 <code>{me.get("user_id")}</code>',
        reply_markup=parser_kb.parser_main_menu(True),
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
        data = await state.get_data()
        await state.update_data(scan_mode=data.get('scan_mode', 'all'))
        await state.set_state(ParserStates.waiting_limit)
        await call.answer('Введите свой лимит', show_alert=False)
        await call.message.answer(
            '✏️ <b>СВОЙ ЛИМИТ</b>\n\n'
            'Введите количество профилей одним числом.\n'
            'Например: <code>350</code>\n'
            'Максимум: <code>5000</code>.',
            reply_markup=parser_kb.parser_cancel_keyboard()
        )
        return
    await start_scan_with_limit(call, call.from_user.id, state, db, int(value), call)


@router.message(ParserStates.waiting_limit)
async def parser_custom_limit(message: Message, state: FSMContext, db):
    if message.text == '✖️ Отмена':
        await state.clear()
        return await parser_menu_handler(message, state, db)
    try:
        limit = int((message.text or '').strip())
        if not 1 <= limit <= 5000:
            raise ValueError
    except ValueError:
        return await message.answer('❌ Введи целое число от 1 до 5000.')

    data = await state.get_data()
    mode = data.get('scan_mode', 'all')
    await state.clear()
    await message.answer(
        f'✅ Лимит принят: <b>{limit}</b> профилей. Запускаю парсер…',
        reply_markup=ReplyKeyboardRemove()
    )
    await start_scan_with_limit(message, message.from_user.id, state, db, limit, None, explicit_mode=mode)


async def start_scan_with_limit(message, uid, state, db, limit, call, explicit_mode=None):
    global parser_client, scanner, scan_task
    if scanner and scanner.is_running:
        if call: await call.answer('⚠️ Парсинг уже идёт', show_alert=True)
        return
    data = await state.get_data(); mode = explicit_mode or data.get('scan_mode', 'all')
    await state.clear()
    client = await ensure_client(db)
    enabled = db.get_enabled_parser_chats()

    origin_message = message.message if isinstance(message, CallbackQuery) else message

    async def on_result(user, gifts):
        for gift in gifts[:5]:
            card = format_gift_card(user, gift)
            await origin_message.answer(card, reply_markup=parser_kb.parser_gift_kb({**gift, 'username': user.get('username'), 'user_id': user.get('user_id')}))

    db.save_parser_scan_state(uid, mode, limit, 'running')
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
    asyncio.create_task(monitor_scan_progress(status_message, scanner, db, scan_task))


def format_gift_card(user, gift):
    username = f'@{user.get("username")}' if user.get('username') else 'без username'
    if gift.get('can_upgrade') and not gift.get('is_collectible'):
        status = '💎 Неулучшенный NFT (доступно улучшение)'
    elif gift.get('is_collectible'):
        status = '✨ NFT / collectible'
    else:
        status = '—'
    link = f'https://t.me/nft/{gift["gift_slug"]}' if gift.get('gift_slug') else 'ссылка недоступна для обычного подарка'
    return ('🎁 <b>НАЙДЕН ПОДАРОК</b>\n\n'
            f'👤 Владелец: <b>{username}</b>\n'
            f'🆔 Telegram ID: <code>{user.get("user_id")}</code>\n'
            f'💬 Найден в чате: <b>{user.get("_source_chat_title") or "—"}</b>\n\n'
            f'🎁 Подарок: <b>{gift.get("gift_name") or "Telegram Gift"}</b>\n'
            f'🔢 ID типа: <code>{gift.get("gift_id") or "—"}</code>\n'
            f'🔢 Номер: <code>{gift.get("gift_num") or "—"}</code>\n'
            f'📌 Статус: <b>{status}</b>\n\n'
            f'🔗 {link}')


async def monitor_scan_progress(message, scanner, db, scan_task=None):
    while scanner.is_running:
        await asyncio.sleep(5)
        s = scanner.get_stats()
        elapsed = datetime_delta(s.get('start_time'))
        text = ('🔎 <b>ПАРСИНГ ИДЁТ</b>\n\n'
                f'📂 Чат: <b>{s["current_chat_title"]}</b>\n'
                f'👥 Проверено: <b>{s["total_checked"]}/{s["limit"]}</b>\n'
                f'🎁 Пользователей с NFT: <b>{s["total_found"]}</b>\n'
                f'✨ Отправлено подарков: <b>{s["total_new_gifts"]}</b>\n'
                f'⏱ Время: <b>{elapsed}</b>\n\n'
                '🎲 Режим: случайная выборка + без повторов')
        try: await message.edit_text(text, reply_markup=parser_kb.parser_running_keyboard())
        except Exception: pass
    # Wait for the actual scan task to finish so the terminal message cannot
    # race ahead of live NFT result messages.
    if scan_task and not scan_task.done():
        try:
            await scan_task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception('Scan task failed while finalizing progress message')
    s = scanner.get_stats()
    terminal = '⏹ <b>ПАРСИНГ ОСТАНОВЛЕН</b>' if scanner.stop_flag else '✅ <b>ПАРСИНГ ЗАВЕРШЁН</b>'
    final_text = (terminal + '\n\n'
        f'👥 Проверено профилей: <b>{s["total_checked"]}</b>\n'
        f'🎁 Профилей с NFT: <b>{s["total_found"]}</b>\n'
        f'✨ Отправлено новых подарков: <b>{s["total_new_gifts"]}</b>\n\n'
        'Результаты сохранены в базе.')
    nft_users = s.get('nft_users') or []
    # The progress message is above the live result cards. Delete it and send
    # the terminal summary as a NEW message so the completion notice is always
    # physically below every gift card in Telegram.
    try:
        await message.delete()
    except Exception:
        log.exception('Could not delete final progress message')
    try:
        await message.bot.send_message(message.chat.id, final_text, reply_markup=parser_kb.back_to_parser())
        if nft_users:
            lines = ['👤 <b>СПИСОК ПОЛЬЗОВАТЕЛЕЙ С NFT</b>', '']
            for u in nft_users:
                name = f'@{u["username"]}' if u.get('username') else 'без username'
                lines.append(f'{name} — <code>{u["user_id"]}</code> — 🎁 <b>{u["gift_count"]}</b>')
                if len(lines) >= 45:
                    await message.bot.send_message(message.chat.id, '\n'.join(lines))
                    lines = ['👤 <b>СПИСОК ПОЛЬЗОВАТЕЛЕЙ С NFT</b>', '']
            if len(lines) > 2:
                await message.bot.send_message(message.chat.id, '\n'.join(lines))
    except Exception:
        log.exception('Could not send final scan message')


def datetime_delta(dt):
    if not dt: return '—'
    return str(datetime.now() - dt).split('.')[0]


@router.callback_query(F.data == 'parser:stop')
async def parser_stop_handler(call, db):
    global scanner, scan_task
    session = db.get_parser_session()
    if not _session_owned(session, call.from_user.id):
        return await call.answer('Нет доступа', show_alert=True)
    if not scanner or not scanner.is_running:
        return await call.answer('Парсинг не запущен', show_alert=True)

    # Set the scanner flag first so its finally block records 'stopped'.
    scanner.stop()
    task = scan_task
    if task and not task.done():
        task.cancel()
    await call.answer('⏹ Останавливаю…')
    try:
        if task and not task.done():
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        log.warning('Scanner task did not finish within stop timeout')
    except Exception:
        log.exception('Scanner stop failed')


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
