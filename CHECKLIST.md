# ✅ Чеклист перед деплоем v2.1

## 🔧 Обязательно сделать:

### 1. **Заменить OWNER_ID в bot.py**

Файл: `bot.py`, строка 44

```python
OWNER_ID = 6969906545  # ⚠️ ЗАМЕНИТЕ НА ВАШ TELEGRAM ID!
```

**Как узнать свой ID:**
1. Напишите боту: [@userinfobot](https://t.me/userinfobot)
2. Скопируйте ваш ID
3. Вставьте в `bot.py`

### 2. **Проверить .env файл**

Файл: `.env`

```env
BOT_TOKEN=ваш_реальный_токен_бота
```

**Где взять токен:**
1. Откройте [@BotFather](https://t.me/BotFather)
2. `/newbot` или `/token`
3. Скопируйте токен
4. Вставьте в `.env`

### 3. **Проверить API ключи в payments.py**

Файл: `payments.py`, строки 8-9

```python
CRYPTOBOT_TOKEN = "629296:AAw96aFWy4zy2Wz6t96lZU2HcccHN3CVyI5"
XROCKET_TOKEN = "eyJhbGciOi..."
```

✅ Уже настроено!

### 4. **Проверить канал логирования**

Файл: `logger_channel.py`, строка 7

```python
LOG_CHANNEL_ID = -1003864916255
```

✅ Уже настроено!

## 🧪 Тестирование локально:

```powershell
# 1. Запустить тест
cd c:\parser\parser_nft_telegram
python test_bot.py

# 2. Запустить бота
python bot.py
```

### Что проверить:
- [ ] `/start` - бот отвечает
- [ ] "⚙️ Админка" - видна только вам
- [ ] "🔍 Начать поиск" - работает
- [ ] Ввод "eternal rose" (маленькими) - находит
- [ ] `/stop` - останавливает парсинг
- [ ] "▶️ Продолжить" - продолжает
- [ ] После 25 поисков - просит Premium
- [ ] "⭐ Купить Premium" - показывает платежи

## 🚀 Деплой на Railway/Render:

### Вариант 1: Git (рекомендуется)

```powershell
cd c:\parser\parser_nft_telegram

# Проверить изменения
git status

# Добавить все файлы
git add .

# Закоммитить
git commit -m "v2.1: payments, logging, stop command, admin fixes, case-insensitive search"

# Запушить
git push
```

### Вариант 2: Ручная загрузка

1. Открыть GitHub репозиторий
2. Upload files:
   - `bot.py`
   - `database.py`
   - `peek_api.py`
   - `keyboards.py`
   - `payments.py` ← новый
   - `logger_channel.py` ← новый
   - `requirements.txt`
3. Commit changes

## ⚙️ Настройки на Railway/Render:

### Environment Variables (Переменные окружения):

#### Обязательные:
```
BOT_TOKEN = ваш_токен_бота
OWNER_ID = ваш_telegram_id
```

#### Опциональные (уже есть дефолты в коде):
```
LOG_CHANNEL_ID = -1003864916255
```

**Где взять BOT_TOKEN:**
1. [@BotFather](https://t.me/BotFather)
2. `/token` или `/newbot`

**Где узнать OWNER_ID:**
1. [@userinfobot](https://t.me/userinfobot)
2. Скопировать ваш ID

**Подробнее:** см. `RAILWAY_SETUP.md`

### Start Command:

```
python bot.py
```

## 📊 После деплоя проверить:

1. **Бот запустился:**
   - Открыть Railway/Render логи
   - Найти: "Бот запускается..."
   - Найти: "Polling запущен"

2. **Логи работают:**
   - Написать боту `/start`
   - Открыть канал -1003864916255
   - Должно появиться сообщение о регистрации

3. **Админка работает:**
   - Написать боту `/start`
   - Нажать "⚙️ Админка"
   - Должна открыться админ-панель

4. **Поиск работает:**
   - "🔍 Начать поиск"
   - Ввести "evil eye"
   - Ввести "1-5"
   - Должен начаться парсинг

## 🐛 Если что-то не работает:

### Бот не отвечает:
```powershell
# Проверить логи Railway/Render
# Ищите ошибки типа:
# - "BOT_TOKEN не указан"
# - "RuntimeError"
# - "ModuleNotFoundError"
```

### Админка не видна:
```python
# Проверьте OWNER_ID в bot.py
# Должно быть ВАШ Telegram ID
```

### Логи не приходят:
```python
# Проверьте LOG_CHANNEL_ID в logger_channel.py
# Бот должен быть админом канала -1003864916255
```

### Платежи не работают:
```python
# Проверьте API ключи в payments.py
# CryptoBot и XRocket должны быть корректны
```

## 📞 Поддержка:

- Telegram: @kod30
- Канал: -1003864916255

## ✅ Готово!

После выполнения всех пунктов:
- [x] OWNER_ID заменён
- [x] BOT_TOKEN добавлен в .env
- [x] Локально протестировано
- [x] Запушено в Git
- [x] Задеплоено на Railway/Render
- [x] Проверено в продакшене

🎉 **Бот v2.1 готов к работе!**
