"""Оформление сообщений GiftParser.

Telegram не умеет менять шрифты, поэтому «шрифты» здесь — это:
  • HTML-разметка: <b>, <i>, <code>, <blockquote>;
  • Unicode-алфавиты: ꜱᴍᴀʟʟ ᴄᴀᴘꜱ для лейблов и 𝗦𝗮𝗻𝘀 𝗕𝗼𝗹𝗱 для акцентов (только латиница);
  • аккуратные прогресс-бары ▰▱ и цитатные блоки вместо ASCII-рамок.
Все тексты бота собираются здесь, чтобы стиль был единым.
"""
import html
import re

from peek_api import gift_url

VERSION = '5.2'
TRIAL_DEFAULT = 25

# ───────────────────────── «шрифты» ─────────────────────────
def small(text: str) -> str:
    return str(text)


def sans(text: str) -> str:
    return str(text)


# ───────────────────────── мелкие помощники ─────────────────────────
def esc(value) -> str:
    return html.escape(str(value))


def code(value) -> str:
    return f'<code>{esc(value)}</code>'


def pretty(name) -> str:
    """PlushPepe → Plush Pepe"""
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', str(name or ''))


def num(n) -> str:
    """12345 → 12 345"""
    try:
        return f'{int(n):,}'.replace(',', '\u2009')
    except (TypeError, ValueError):
        return str(n)


def bar(done, total, width=12) -> str:
    total = max(total, 1)
    filled = max(0, min(width, int(width * done / total)))
    return '▰' * filled + '▱' * (width - filled)


def percent(done, total) -> int:
    return int(done * 100 / total) if total else 0


def pretty_time(sec) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f'{sec} с'
    if sec < 3600:
        return f'{sec // 60} м {sec % 60} с'
    return f'{sec // 3600} ч {(sec % 3600) // 60} м'


def block(*lines: str, expandable=False) -> str:
    tag = 'blockquote expandable' if expandable else 'blockquote'
    return f'<{tag}>' + '\n'.join(lines) + '</blockquote>'


def kv(icon, label, value) -> str:
    return f'{icon} {label} · <b>{value}</b>'


STATUS_LABEL = {
    'completed': '✅ завершён',
    'stopped': '⏹ остановлен',
    'error': '⚠️ ошибка',
    'in_progress': '🔎 выполняется',
}

# ───────────────────────── карточка подарка ─────────────────────────
def gift_card(g: dict) -> str:
    name = pretty(g.get('giftName') or g.get('model') or 'Telegram Gift')
    n = g.get('giftNumber')
    head = f'🎁 <b>{esc(name)}</b>'
    if n is not None:
        head += f'  <code>#{n}</code>'

    rows = []
    if g.get('model'):
        rows.append(kv('✨', 'Модель', esc(g['model'])))
    if g.get('pattern'):
        rows.append(kv('🌸', 'Узор', esc(g['pattern'])))
    if g.get('backdrop'):
        rows.append(kv('🎨', 'Фон', esc(g['backdrop'])))
    price = (g.get('market') or {}).get('price')
    if price not in (None, ''):
        try:
            price = f'{float(price):g}'
        except (TypeError, ValueError):
            pass
        cur = (g.get('market') or {}).get('currency') or ''
        rows.append(kv('💰', 'Цена', esc(f'{price} {cur}'.strip())))

    parts = [head]
    if rows:
        parts.append(block(*rows))

    if g.get('username'):
        u = esc(g['username'])
        parts.append(f'👤 <a href="https://t.me/{u}">@{u}</a>')
    elif g.get('userId'):
        parts.append(f'👤 ID · <code>{esc(g["userId"])}</code>')
    elif g.get('owner'):
        parts.append(f'👤 {esc(g["owner"])}')
    return '\n'.join(parts)


def row_to_gift(r: dict) -> dict:
    """Строка из БД → словарь подарка для карточки."""
    g = {
        'giftNumber': r.get('gift_number'),
        'giftName': r.get('collection') or r.get('gift_name') or r.get('model'),
        'model': r.get('model'),
        'pattern': r.get('pattern'),
        'backdrop': r.get('backdrop'),
        'username': r.get('username'),
        'userId': r.get('user_id_owner'),
        'owner': r.get('owner'),
        'market': {},
    }
    if r.get('price') is not None:
        g['market'] = {'price': r['price']}
    return g


def gift_label(g: dict) -> str:
    """Подпись кнопки в списках."""
    label = f'#{g.get("giftNumber")} · {g.get("model") or pretty(g.get("giftName"))}'
    return label if len(label) <= 32 else label[:31] + '…'


# ───────────────────────── экраны ─────────────────────────
def brand() -> str:
    return f'🎁 <b>{small("GiftParser")}</b> <code>v{VERSION}</code>'


def account_line(user: dict, admin=False) -> str:
    if admin:
        return '👑 Владелец · <b>безлимит</b>'
    if user.get('is_premium'):
        return '💎 Premium · <b>безлимит</b>'
    return f'🆓 Trial · <b>{user.get("trial_searches_left", TRIAL_DEFAULT)}</b> проверок'


def welcome(user: dict, admin=False) -> str:
    return (
        f'{brand()}\n'
        '<i>Поиск Telegram NFT-подарков по моделям и диапазонам ID</i>\n\n'
        f'{block(account_line(user, admin))}\n'
        'Выберите действие в меню ниже 👇'
    )


def help_text() -> str:
    return (
        f'❓ <b>{small("Help")} · Как пользоваться</b>\n\n'
        + block(
            '🔎 <b>Новый поиск</b> — модель + диапазон ID',
            '🆔 <b>Поиск по ID</b> — один конкретный подарок',
            '🎲 <b>Случайный</b> — быстрый тест случайных ID',
            '🎁 <b>Модели</b> — каталог, выбор в одно касание',
            '📚 <b>История</b> — результаты, экспорт и продолжение',
            '⭐ <b>Избранное</b> — сохранённые находки',
        )
        + '\n\n<b>Формат диапазона</b>\n'
        + block('<code>13000-13500</code>  — от и до', '<code>777</code>  — один номер', expandable=False)
        + '\n💡 Большие диапазоны (до 10 000 ID) можно останавливать — '
          'прогресс сохранится, продолжить можно из истории.\n'
          '⏹ Остановка: кнопка под статусом или команда /stop'
    )


def profile(user: dict, uid: int, username, stats: dict, admin=False) -> str:
    left = user.get('trial_searches_left', TRIAL_DEFAULT)
    lines = [
        kv('🆔', 'ID', uid),
        kv('👤', 'Username', esc('@' + username) if username else '—'),
        account_line(user, admin),
    ]
    if not (admin or user.get('is_premium')):
        lines.append(f'🎫 {bar(left, TRIAL_DEFAULT, 10)} <b>{left}</b>/{TRIAL_DEFAULT}')
    return (
        f'👤 <b>{small("Profile")} · Профиль</b>\n\n'
        + block(*lines)
        + '\n'
        + block(
            kv('🔎', 'Поисков', num(user.get('total_searches', 0))),
            kv('🎁', 'Находок', num(user.get('total_found', 0))),
        )
        + f'\n<i>Пользователей в боте: {num(stats["users"])}</i>'
    )


def premium_text() -> str:
    return (
        f'💎 <b>{small("Premium")}</b>\n\n'
        + block(
            '♾ Безлимитные проверки',
            '⚡ Приоритетные возможности',
            '📄 Экспорт результатов',
        )
        + '\nДля подключения напишите владельцу бота.'
    )


# ───────────────────────── поиск ─────────────────────────
_SEARCH_TITLES = {
    'running': '🔎 <b>Поиск идёт</b>',
    'done': '✅ <b>Поиск завершён</b>',
    'stopped': '⏹ <b>Поиск остановлен</b>',
    'error': '⚠️ <b>Поиск прерван</b>',
    'limit': '🆓 <b>Лимит Trial исчерпан</b>',
}


def search_card(state, model, start, end, checked, total, found,
                rate=0.0, eta=0.0, elapsed=0.0, skipped=0, shown_cards=None, search_id=None) -> str:
    stats = [
        kv('📦', 'Проверено', f'{num(checked)} / {num(total)}'),
        kv('✨', 'Найдено', num(found)),
    ]
    if state == 'running':
        stats += [kv('⚡', 'Скорость', f'{rate:.1f} ID/с'), kv('⏱', 'Осталось', f'~{pretty_time(eta)}')]
    elif state == 'done':
        stats += [kv('⚡', 'Скорость', f'{rate:.1f} ID/с'), kv('⏱', 'Время', pretty_time(elapsed))]
    if skipped:
        stats.append(kv('⚠️', 'Пропущено (ошибки сети)', num(skipped)))

    text = (
        f'{_SEARCH_TITLES[state]}\n'
        f'🎁 <b>{esc(pretty(model))}</b> · <code>{start} → {end}</code>\n'
        + (f'📌 Поиск · <code>#{search_id}</code>\n\n' if search_id else '\n')
        + f'{bar(checked, total)}  <b>{percent(checked, total)}%</b>\n'
        f'{block(*stats)}'
    )
    if shown_cards is not None and found > shown_cards:
        text += f'\n<i>Показаны первые {shown_cards} карточек — остальное в «📚 История».</i>'
    if state == 'done':
        text += '\n📚 Результаты сохранены в истории.'
    elif state == 'stopped':
        text += '\n▶️ Продолжить можно из «📚 История».'
    elif state == 'error':
        text += '\nПрогресс сохранён — продолжите из «📚 История».'
    elif state == 'limit':
        text += '\n💎 Подключите Premium, чтобы продолжить с этого места.'
    return text


def history_list(rows: list) -> str:
    return (
        f'📚 <b>{small("History")} · История</b>\n'
        f'<i>Последние поиски — нажмите, чтобы открыть</i>'
    )


def history_button_label(x: dict) -> str:
    icon = {'completed': '✅', 'stopped': '⏹', 'error': '⚠️', 'in_progress': '🔎'}.get(x['status'], '•')
    label = f'{icon} #{x["id"]} · {pretty(x["model"])} · ✨{x["found_count"]}'
    return label if len(label) <= 40 else label[:39] + '…'


def search_detail(x: dict) -> str:
    total = x['end_id'] - x['start_id'] + 1
    return (
        f'📌 <b>Поиск #{x["id"]}</b>\n'
        f'🎁 <b>{esc(pretty(x["model"]))}</b> · <code>{x["start_id"]} → {x["end_id"]}</code>\n\n'
        f'{bar(x["checked_count"], total)}  <b>{percent(x["checked_count"], total)}%</b>\n'
        + block(
            kv('📦', 'Проверено', f'{num(x["checked_count"])} / {num(total)}'),
            kv('✨', 'Найдено', num(x['found_count'])),
            kv('📍', 'Статус', STATUS_LABEL.get(x['status'], esc(x['status']))),
        )
    )


def list_header(title: str, count: int, page: int, pages: int) -> str:
    return f'{title}\n<i>Всего: {num(count)} · стр. {page}/{pages}</i>'


def models_text(page: int, pages: int) -> str:
    return (
        f'🎁 <b>{small("Models")} · Каталог моделей</b>\n'
        f'<i>Нажмите на модель — и сразу введёте диапазон · стр. {page}/{pages}</i>'
    )
