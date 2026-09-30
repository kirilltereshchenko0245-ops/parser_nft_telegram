TELEGRAM GIFT PARSER — БЫСТРЫЙ ЗАПУСК
======================================

1. Установи Python 3.11 или новее.

2. Открой PowerShell в этой папке.

3. Создай виртуальное окружение:
   python -m venv venv

4. Активируй:
   .\venv\Scripts\Activate.ps1

Если PowerShell запрещает запуск:
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

После этого:
   .\venv\Scripts\Activate.ps1

5. Установи библиотеки:
   pip install -r requirements.txt

6. Создай файл .env рядом с bot.py.

В .env:
   BOT_TOKEN=токен_от_BotFather

7. Запусти:
   python bot.py

8. Открой своего Telegram-бота и отправь:
   /start

9. Затем:
   /parse

10. Введи:
   EternalRose
   13000
   13100

ВАЖНО
======
Этот MVP использует endpoint Peek:
https://server.peek.tg/api/nft/gifts/search

API может изменить формат или ограничить доступ. Код специально вынес работу с Peek в отдельный файл peek_api.py, чтобы его можно было быстро заменить.

Telegram официально описывает collectible gifts через starGiftUnique:
- gift_id — ID базового подарка
- num — уникальный номер collectible-подарка
- owner_id — владелец, если поле присутствует
- slug — идентификатор collectible gift

Не вводи API_ID/API_HASH для этой первой версии: они не нужны, потому что бот пока работает через Peek API.

ЕСЛИ ХОЧЕШЬ ИМЕННО ПОЛНЫЙ ПАРСЕР
==================================
Следующим этапом можно добавить:
- сохранение результатов SQLite
- продолжение после остановки
- CSV/TXT экспорт
- кнопки вместо команд
- поиск по model/pattern/backdrop
- статистику
- историю запусков
- повторный запуск без дублей
- обработку Telegram owner_id через MTProto
