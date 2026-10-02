"""Оформление сообщений GiftParser v5.2 — ахуенный редизайн.

Telegram не умеет менять шрифты, поэтому «дизайн» здесь — это:
  • HTML-разметка: <b>, <i>, <code>, <blockquote>;
  • Unicode-алфавиты: ꜱᴍᴀʟʟ ᴄᴀᴘꜱ для лейблов и 𝗦𝗮𝗻𝘀 𝗕𝗼𝗹𝗱 для акцентов;
  • аккуратные прогресс-бары с эмодзи-градиентом ▰▱;
  • rarity-бейджи 💎🔥⭐📦;
  • эмодзи-анимация статусов.
"""
import html
import re

from peek_api import RARITY_ICON, RARITY_MAP, RARITY_NAME, gift_url, rarity_info

VERSION = '5.2'
TRIAL_DEFAULT = 25

# ───────────────────────── «шрифты» ─────────────────────────
_SMALL = dict(zip(
    'abcdefghijklmnopqrstuvwxyz',
    'ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ',
))


def small(text: str) -> str:
    """ɢɪғᴛᴘᴀʀsᴇʀ — маленькие капсы (латиница, остальное без изменений)."""
    return ''.join(_SMALL.get(ch.lower(), ch) for ch in str(text))


def sans(text: str) -> str:
    """𝗦𝗮𝗻𝘀 𝗕𝗼𝗹𝗱 — жирный гротеск (латиница и цифры)."""
    out = []
    for ch in str(text):
        if 'A' <= ch <= 'Z':
            out.append(chr(0x1D5D4 + ord(ch) - 65))
        elif 'a' <= ch <= 'z':
            out.append(chr(0x1D5EE + ord(ch) - 97))
        elif '0' <= ch <= '9':
            out.append(chr(0x1D7EC + ord(ch) - 48))
        else:
            out.append(ch)
    return ''.join(out)


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
    """Прогресс-бар ▰▱ с градиентом."""
    total = max(total, 1)
    filled = max(0, min(width, int(width * done / total)))
    return '🟩' * filled + '⬜' * (width - filled)


def bar_compact(done, total, width=8) -> str:
    """Компактный бар с цифрами."""
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

# ───────────────────────── RARITY BADGE ─────────────────────────
def rarity_badge(model_name: str) -> str:
    """Вернёт значок редкости для модели, напр. 💎 Legendary."""
    r = RARITY_MAP.get(model_name, 'common')
    icon = RARITY_ICON.get(r, '⭐')
    name = RARITY_NAME.get(r, 'Common')
    return f'{icon} <b>{name}</b>'


def rarity_color_tag(model_name: str) -> str:
    """Цветной тэг для карточки подарка."""
    r = RARITY_MAP.get(model_name, 'common')
    icon = RARITY_ICON.get(r, '⭐')
    name = RARITY_NAME.get(r, 'Common')
    # Эмодзи уже дают цвет, просто форматируем
    return f'{icon} {name}'


# ───────────────────────── карточка подарка (v2 — ахуенная) ─────────────────────────
def gift_card(g: dict) -> str:
    name = pretty(g.get('giftName') or g.get('model') or 'Telegram Gift')
    n = g.get('giftNumber')

    # Шапка с редкостью
    model_name = g.get('model') or g.get('giftName') or ''
    badge = rarity_color_tag(model_name) if model_name else '🎁 Gift'

    head = f'┏━━━ {badge} ━━━┓\n'
    head += f'<b>{esc(name)}</b>'
    if n is not None:
        head += f'  <code>#{n}</code>'
    head += '\n┗━━━━━━━━━━━━━━━━┛'

    rows = []
    if g.get('pattern'):
        rows.append(f'🌸 <b>Узор:</b> {esc(g["pattern"])}')
    if g.get('backdrop'):
        rows.append(f'🎨 <b>Фон:</b> {esc(g["backdrop"])}')

    # Редкость pattern/backdrop если есть
    p_rarity = g.get('rarityPattern')
    b_rarity = g.get('rarityBackdrop')
    if p_rarity:
        rows.append(f'✨ <b>Редкость узора:</b> {esc(p_rarity)}')
    if b_rarity:
        rows.append(f'✨ <b>Редкость фона:</b> {esc(b_rarity)}')

    price = (g.get('market') or {}).get('price')
    if price not in (None, ''):
        try:
            price = f'{float(price):g}'
        except (TypeError, ValueError):
            pass
        cur = (g.get('market') or {}).get('currency') or ''
        rows.append(f'💰 <b>Цена:</b> {esc(f"{price} {cur}").strip()}')

    parts = [head]
    if rows:
        parts.append(block(*rows))

    # Владелец
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
        return '👑 <b>Владелец</b> · безлимит'
    if user.get('is_premium'):
        return '💎 <b>Premium</b> · безлимит'
    left = user.get('trial_searches_left', TRIAL_DEFAULT)
    return f'🆓 <b>Trial</b> · {bar(left, TRIAL_DEFAULT, 8)} <b>{left}</b>/{TRIAL_DEFAULT}'


def welcome(user: dict, admin=False) -> str:
    return (
        f'{brand()}\n'
        '<i>Поиск Telegram NFT-подарков</i>\n\n'
        f'{block(account_line(user, admin))}\n'
        '👇 <b>Выберите действие</b>'
    )


def help_text() -> str:
    return (
        f'❓ <b>{small("Help")} · Как пользоваться</b>\n\n'
        + block(
            '🔎 <b>Новый поиск</b> — модель + диапазон ID',
            '🆔 <b>Поиск по ID</b> — один конкретный подарок',
            '🎲 <b>Случайный</b> — быстрый тест случайных ID',
            '🎁 <b>Модели</b> — каталог, выбор в одно касание',
            '📡 <b>Gift Radar</b> — сканировать вокруг ID',
            '📚 <b>История</b> — результаты, экспорт и продолжение',
            '⭐ <b>Избранное</b> — сохранённые находки',
        )
        + '\n\n<b>Формат диапазона</b>\n'
        + block('<code>13000-13500</code>  — от и до', '<code>777</code>  — один номер', expandable=False)
        + '\n💡 Большие диапазоны (до 10 000 ID) можно останавливать — '
          'прогресс сохранится, продолжить можно из истории.\n'
          '⏹ Остановка: кнопка под статусом или команда /stop'
    )


def profile(user: dict, uid: int, username, stats: dict, admin=False,
            achievements: list = None, collection_by_rarity: dict = None) -> str:
    lines = [
        f'🆔 <b>ID:</b> <code>{uid}</code>',
    ]
    if username:
        lines.append(f'👤 <b>Username:</b> @{esc(username)}')

    # Статус
    if admin:
        lines.append('👑 <b>Статус:</b> Владелец · безлимит')
    elif user.get('is_premium'):
        lines.append('💎 <b>Статус:</b> Premium · безлимит')
    else:
        left = user.get('trial_searches_left', TRIAL_DEFAULT)
        lines.append(f'🆓 <b>Статус:</b> Trial · {bar(left, TRIAL_DEFAULT, 10)} <b>{left}</b>/{TRIAL_DEFAULT}')

    # Статистика
    lines += [
        f'🔎 <b>Поисков:</b> {num(user.get("total_searches", 0))}',
        f'🎁 <b>Находок:</b> {num(user.get("total_found", 0))}',
    ]

    # Коллекция по редкости
    if collection_by_rarity:
        coll_lines = []
        for r in ('legendary', 'rare', 'common', 'mass'):
            cnt = collection_by_rarity.get(r, 0)
            total = {'legendary': 9, 'rare': 13, 'common': 24, 'mass': 2}.get(r, 0)
            icon = RARITY_ICON.get(r, '•')
            bar_coll = bar(cnt, total, 6)
            coll_lines.append(f'{icon} {bar_coll} <b>{cnt}</b>/{total}')
        if coll_lines:
            lines.append('')
            lines.append(f'📊 <b>Коллекция:</b>')
            lines.extend(coll_lines)

    text = (
        f'👤 <b>{small("Profile")} · Профиль</b>\n'
        f'<i>Пользователей в боте: {num(stats["users"])}</i>\n\n'
        + block(*lines)
    )

    # Ачивки
    if achievements:
        text += '\n\n🏆 <b>Достижения</b>'
        for a in achievements:
            text += f'\n{a["icon"]} <b>{esc(a["title"])}</b> — <i>{esc(a["desc"])}</i>'

    return text


def premium_text() -> str:
    return (
        f'💎 <b>{small("Premium")}</b>\n\n'
        + block(
            '♾ <b>Безлимитные</b> проверки',
            '⚡ <b>Приоритетный</b> доступ',
            '📄 <b>Экспорт</b> результатов в TXT',
            '🏆 <b>Достижения</b> и статистика',
        )
        + '\n👑 Напишите владельцу для подключения.'
    )


# ───────────────────────── поиск ─────────────────────────
_SEARCH_TITLES = {
    'running': '🔎 <b>Поиск идёт</b>',
    'done': '🎉 <b>Поиск завершён!</b>',
    'stopped': '⏹ <b>Поиск остановлен</b>',
    'error': '⚠️ <b>Поиск прерван</b>',
    'limit': '🆓 <b>Лимит Trial исчерпан</b>',
}


def search_card(state, model, start, end, checked, total, found,
                rate=0.0, eta=0.0, elapsed=0.0, skipped=0, shown_cards=None) -> str:
    pct = percent(checked, total)
    stats = [
        f'📦 <b>Проверено:</b> {num(checked)} / {num(total)}',
        f'✨ <b>Найдено:</b> {num(found)}',
    ]
    if state == 'running':
        stats += [f'⚡ <b>Скорость:</b> {rate:.1f} ID/с', f'⏱ <b>Осталось:</b> ~{pretty_time(eta)}']
    elif state == 'done':
        stats += [f'⚡ <b>Скорость:</b> {rate:.1f} ID/с', f'⏱ <b>Время:</b> {pretty_time(elapsed)}']
    if skipped:
        stats.append(f'⚠️ <b>Пропущено (ошибки сети):</b> {num(skipped)}')

    text = (
        f'{_SEARCH_TITLES[state]}\n'
        f'🎁 <b>{esc(pretty(model))}</b> · <code>{start} → {end}</code>\n\n'
        f'{bar(checked, total)}  <b>{pct}%</b>\n'
        f'{block(*stats)}'
    )
    if shown_cards is not None and found > shown_cards:
        text += f'\n📝 <i>Показаны первые {shown_cards} из {found} находок</i>'
    if state == 'done':
        text += '\n📚 <i>Результаты в истории</i>'
    elif state == 'stopped':
        text += '\n▶️ <i>Продолжить из «📚 История»</i>'
    elif state == 'error':
        text += '\n💾 <i>Прогресс сохранён</i>'
    elif state == 'limit':
        text += '\n💎 <i>Premium для продолжения</i>'
    return text


def history_list(rows: list) -> str:
    return (
        f'📚 <b>{small("History")} · История поисков</b>\n'
        f'<i>Нажмите, чтобы открыть детали</i>'
    )


def history_button_label(x: dict) -> str:
    icon = {'completed': '✅', 'stopped': '⏹', 'error': '⚠️', 'in_progress': '🔎'}.get(x['status'], '•')
    label = f'{icon} #{x["id"]} · {pretty(x["model"])} · ✨{x["found_count"]}'
    return label if len(label) <= 40 else label[:39] + '…'


def search_detail(x: dict) -> str:
    total = x['end_id'] - x['start_id'] + 1
    pct = percent(x['checked_count'], total)
    return (
        f'📌 <b>Поиск #{x["id"]}</b>\n'
        f'🎁 <b>{esc(pretty(x["model"]))}</b> · <code>{x["start_id"]} → {x["end_id"]}</code>\n\n'
        f'{bar(x["checked_count"], total)}  <b>{pct}%</b>\n'
        + block(
            f'📦 <b>Проверено:</b> {num(x["checked_count"])} / {num(total)}',
            f'✨ <b>Найдено:</b> {num(x["found_count"])}',
            f'📍 <b>Статус:</b> {STATUS_LABEL.get(x["status"], esc(x["status"]))}',
        )
    )


def list_header(title: str, count: int, page: int, pages: int) -> str:
    return f'{title}\n<i>Всего: {num(count)} · стр. {page}/{pages}</i>'


# ───────────────────────── каталог моделей ─────────────────────────
def models_text(page: int, pages: int) -> str:
    return (
        f'🎁 <b>{small("Models")} · Каталог моделей</b>\n'
        f'<i>Редкость указана значком · стр. {page}/{pages}\n'
        f'Нажмите на модель для поиска</i>'
    )


def models_rarity_text(rarity: str, models: list[str], page: int, pages: int, found: dict = None) -> str:
    """Каталог, отфильтрованный по редкости."""
    icon = RARITY_ICON.get(rarity, '•')
    name = RARITY_NAME.get(rarity, 'Unknown')
    total = len(models)
    found_count = sum(1 for m in models if found and m in found) if found else 0
    text = (
        f'{icon} <b>{name}</b> · {total} моделей\n'
        f'{bar_compact(found_count, total)} <b>{found_count}</b>/{total} найдено\n\n'
        f'<i>стр. {page}/{pages}</i>'
    )
    return text


# ───────────────────────── ачивки ─────────────────────────
def achievements_text(achievements: list) -> str:
    if not achievements:
        return f'🏆 <b>{small("Achievements")} · Достижения</b>\n\n<i>Пока пусто. Находите подарки, чтобы открывать достижения!</i>'
    text = f'🏆 <b>{small("Achievements")} · Достижения ({len(achievements)})</b>\n\n'
    for a in achievements:
        text += f'{a["icon"]} <b>{esc(a["title"])}</b>\n└ <i>{esc(a["desc"])}</i>\n\n'
    return text


# ───────────────────────── Gift Radar ─────────────────────────
def radar_header(center: int, radius: int, found: int, total: int) -> str:
    pct = percent(found, total)
    density = '🔥 Высокая' if found > 3 else ('⚡ Средняя' if found > 0 else '💨 Пусто')
    return (
        f'📡 <b>{small("Radar")} · Gift Radar</b>\n'
        f'🎯 Центр: <code>#{num(center)}</code> · Радиус: ±{radius}\n\n'
        f'{bar(found, total)}  <b>{found}</b>/{total} находок · {density}'
    )


def radar_gift_line(g: dict) -> str:
    n = g.get('giftNumber')
    m = pretty(g.get('giftName') or g.get('model') or '')
    u = g.get('username')
    icon = '🟢' if u else '🔴'
    owner = f'👤 @{esc(u)}' if u else '🔒 скрыт'
    return f'{icon} <code>#{num(n)}</code> <b>{esc(m)}</b> · {owner}'


# ───────────────────────── категории моделей ─────────────────────────
CATEGORY_ICONS = {
    'legendary': '💎', 'rare': '🔥', 'common': '⭐', 'mass': '📦',
}
CATEGORY_LABELS = {
    'legendary': 'Legendary', 'rare': 'Rare', 'common': 'Common', 'mass': 'Mass',
}


def category_menu_text(stats: dict) -> str:
    """Текст меню выбора редкости."""
    text = f'🎁 <b>{small("Rarity")} · Категории редкости</b>\n\n'
    for rarity in ('legendary', 'rare', 'common', 'mass'):
        icon = CATEGORY_ICONS[rarity]
        cnt = stats.get(rarity, 0)
        text += f'{icon} <b>{CATEGORY_LABELS[rarity]}</b>  · {cnt} моделей\n'
    text += '\n<i>Выберите категорию для просмотра</i>'
    return text