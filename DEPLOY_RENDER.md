# 🚀 Деплой Telegram-бота на Render.com

## Почему Render?
На Render (и других зарубежных хостингах) Telegram API **НЕ заблокирован**, поэтому бот будет работать без прокси!

## Шаг 1: Подготовка репозитория

1. Создай репозиторий на GitHub (если ещё нет)
2. Запуш файлы проекта:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/твой-username/твой-репозиторий.git
git push -u origin main
```

## Шаг 2: Регистрация на Render

1. Перейди на [render.com](https://render.com)
2. Зарегистрируйся (можно через GitHub)
3. Подтверди email

## Шаг 3: Создание Web Service

1. Нажми **"New +"** → **"Web Service"**
2. Подключи свой GitHub репозиторий
3. Выбери этот проект
4. Настрой параметры:
   - **Name**: `telegram-gift-parser-bot` (или своё название)
   - **Region**: `Frankfurt (EU Central)` (ближе к России)
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: `Free` (бесплатно)

## Шаг 4: Добавление переменных окружения

В разделе **Environment Variables** добавь:

```
BOT_TOKEN=твой_токен_от_BotFather
```

**Важно:** НЕ добавляй `PROXY_URL` - на Render прокси не нужен!

## Шаг 5: Деплой

1. Нажми **"Create Web Service"**
2. Подожди 2-3 минуты, пока Render соберёт и запустит бота
3. В логах увидишь `INFO:aiogram.dispatcher:Start polling` - бот работает!

## ⚠️ Важные моменты

### Бесплатный план Render
- Сервис "засыпает" после 15 минут неактивности
- Автоматически "просыпается" при обращении
- Бот будет отвечать с задержкой ~30 секунд после сна
- 750 часов в месяц бесплатно

### Как держать бота активным
Можно использовать сервис мониторинга (например, UptimeRobot) для пинга каждые 10 минут.

### Альтернативы Render

Если Render не подходит, попробуй:
- **Railway.app** - тоже бесплатный план
- **Fly.io** - хороший для Telegram ботов
- **Heroku** - платный, но надёжный
- **VPS** (DigitalOcean, Linode) - от $5/месяц

## 🔧 Обновление бота

После изменения кода:

```bash
git add .
git commit -m "Описание изменений"
git push
```

Render автоматически пересоберёт и перезапустит бота!

## 📊 Мониторинг

В Render Dashboard можно смотреть:
- Логи в реальном времени
- Статус сервиса
- Использование ресурсов

## 🐛 Проблемы?

Если бот не запускается:
1. Проверь логи в Render Dashboard
2. Убедись, что `BOT_TOKEN` правильный
3. Проверь, что `requirements.txt` содержит все зависимости
4. Убедись, что в `.env` НЕТ, его быть не должно в репозитории!
