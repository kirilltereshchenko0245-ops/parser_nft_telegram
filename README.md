# 🎁 Telegram NFT Gift Parser Bot v2.1

## 🚀 Быстрый старт

### 1. Локальный запуск:
```powershell
cd c:\parser\parser_nft_telegram
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

### 2. Деплой на Railway/Render:
```powershell
git add .
git commit -m "v2.1: Full rewrite with payments"
git push
```

## ✨ Основные функции:

- 🔍 **Поиск по диапазону** - парсинг подарков
- 🔎 **Поиск по ID** - найти конкретный подарок
- 📋 **Список моделей** - все доступные подарки
- ⏹️ **/stop** - остановка парсинга
- ⭐ **Premium** - безлимитные поиски (5 TON)
- ⚙️ **Админка** - управление пользователями
- 📊 **Логи** - все действия в канал

## 🎫 Лимиты:

- **Trial:** 25 поисков бесплатно
- **Premium:** безлимитно (5 TON)

## 🔑 Настройка:

1. Создать `.env`:
```env
BOT_TOKEN=ваш_токен_бота
```

2. В `bot.py` изменить `OWNER_ID` на ваш Telegram ID

3. Запустить: `python bot.py`

## 📋 API:

- **CryptoBot:** 629296:AAw96aFWy4zy2Wz6t96lZU2HcccHN3CVyI5
- **XRocket:** JWT токен (в payments.py)
- **Логи:** -1003864916255

## 🛠️ Техподдержка:

- @kod30

## 📝 Подробнее:

См. `UPDATE_V2.1.md`

---

**v2.1** | 2026-09-30 | @kod30
