# GiftParser 6.0 — Telegram chat NFT parser

Обновлённый MTProto-парсер для проверки участников выбранных Telegram-чатов и поиска подарков в профилях.

## Что изменено в 6.0

- Исправлена авторизация через Telegram-код.
- После успешного входа сохраняется `StringSession`.
- При перезапуске бота сохранённая сессия восстанавливается автоматически — новый код не запрашивается.
- Новый код не запрашивается автоматически после ошибки/истечения старого кода.
- После подключения загружается список групп/супергрупп/каналов аккаунта.
- Для каждого чата есть отдельный переключатель включён/выключен.
- Добавлены «Включить все» и «Выключить все».
- Добавлена пагинация списка чатов.
- Доступ к управлению MTProto-аккаунтом ограничен владельцем сохранённой сессии.
- Добавлены два режима:
  - `🎁 Любые NFT` — все доступные Telegram Gifts, включая collectible/unique;
  - `💎 Неулучшенные` — обычные подарки, которые ещё не являются collectible/unique.
- Можно выбрать количество новых профилей: 50 / 100 / 250 / 500 / 1000 / свой лимит до 5000.
- Проверка идёт последовательно и специально медленно.
- Между профилями случайная задержка 2.5–5.5 секунды.
- После каждых 20 реально проверенных профилей — пауза 8–18 секунд.
- `FloodWait` Telegram обрабатывается ожиданием указанного Telegram времени.
- Временные ошибки проверки подарков повторяются с увеличивающейся задержкой.
- Профили запоминаются в SQLite. Один профиль не проверяется повторно чаще одного раза в 7 дней.
- Найденные подарки имеют отдельную дедупликацию по пользователю + типу/номеру/slug.
- Исправлена старая проблема `UNIQUE(user_id, gift_id)`, из-за которой разные экземпляры одного типа подарка могли теряться.
- Добавлена миграция старой таблицы `parser_gifts`.
- Реальная проверка подарков выполняется через Telegram MTProto `payments.getSavedStarGifts`, а не через случайную заглушку.
- Получаются и сохранённые, и несохранённые подарки профиля.
- Для collectible-подарков сохраняется slug и формируется ссылка `t.me/nft/...`.
- Найденные подарки сразу отправляются красивыми карточками: владелец, Telegram ID, название, ID типа, номер, статус и кнопки профиля/подарка.
- После завершения показывается итог: проверенные профили, профили с подарками и новые находки.

## Важное ограничение

Парсер использует официальные MTProto-механизмы Telegram. Telegram может ограничивать частоту запросов, поэтому скорость намеренно не максимальная. При `FloodWait` приложение должно ждать указанное Telegram время.

Telegram документирует `payments.getSavedStarGifts` как метод для получения полного списка подарков, принадлежащих/полученных/размещённых у профиля; он умеет возвращать как сохранённые, так и несохранённые подарки.

## Запуск

Требуется Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python bot.py
```

Заполните `.env`:

```env
BOT_TOKEN=...
OWNER_ID=...
DATABASE_PATH=bot_database.db
```

Для Railway рекомендуется Volume и:

```env
DATABASE_PATH=/data/bot_database.db
```

## Безопасность сессии

`StringSession` является секретом, фактически дающим доступ к авторизованной MTProto-сессии. Не публикуйте `.env`, SQLite-базу или StringSession в GitHub и не передавайте их посторонним.

## Файлы MTProto-парсера

- `parsing/telegram_client.py` — авторизация, восстановление сессии, чаты, участники и получение подарков.
- `parsing/scanner.py` — последовательный тщательный сканер, задержки, FloodWait, дедупликация и лимиты.
- `parsing/handlers.py` — интерфейс Telegram-бота и FSM авторизации/настройки запуска.
- `parsing/keyboards.py` — интерфейс кнопок.
- `database.py` — SQLite, сессия, чаты, пользователи, подарки и прогресс.

## Зависимости

Telethon должен быть достаточно свежим для актуального слоя Telegram Gifts; в проекте установлен `telethon>=1.41,<2`.


## Pre-authorized Telegram account (recommended)

This version does not perform phone/code login inside the bot. Use a one-time Telethon StringSession, then store it as the Railway variable `PARSER_SESSION_STRING`. Also set `OWNER_ID`. `TG_API_ID` and `TG_API_HASH` identify the Telegram application; the StringSession is what authorizes the actual account.

To generate a session locally:

```bash
python create_session.py
```

Complete the one-time Telegram login in the terminal. Copy the printed `PARSER_SESSION_STRING` to Railway Variables. Do not commit it to Git or send it in chat.
