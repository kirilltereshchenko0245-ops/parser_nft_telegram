"""
Модуль для логирования действий пользователей в канал
"""
import logging
import os
from aiogram import Bot
from aiogram.enums import ParseMode
from datetime import datetime

# ID канала для логов (можно переопределить через .env)
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1003864916255"))

class ChannelLogger:
    """Логирование в Telegram канал"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def log_action(self, action: str, user_id: int, username: str = None, details: str = ""):
        """Логировать действие пользователя"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            user_info = f"@{username}" if username else f"ID: {user_id}"
            
            message = (
                f"📊 <b>Лог действия</b>\n"
                f"⏰ {timestamp}\n"
                f"👤 {user_info}\n"
                f"🔹 Действие: <b>{action}</b>\n"
            )
            
            if details:
                message += f"📝 Детали: {details}\n"
            
            await self.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Failed to send log to channel: {e}")
    
    async def log_search(self, user_id: int, username: str, model: str, range_start: int, range_end: int):
        """Логировать начало поиска"""
        await self.log_action(
            action="Начат поиск",
            user_id=user_id,
            username=username,
            details=f"Модель: {model}, Диапазон: {range_start}-{range_end}"
        )
    
    async def log_payment(self, user_id: int, username: str, amount: float, currency: str):
        """Логировать платёж"""
        await self.log_action(
            action="💳 Платёж",
            user_id=user_id,
            username=username,
            details=f"Сумма: {amount} {currency}"
        )
    
    async def log_premium_activated(self, user_id: int, username: str):
        """Логировать активацию Premium"""
        await self.log_action(
            action="⭐ Premium активирован",
            user_id=user_id,
            username=username
        )
    
    async def log_registration(self, user_id: int, username: str, first_name: str):
        """Логировать регистрацию нового пользователя"""
        await self.log_action(
            action="✅ Новый пользователь",
            user_id=user_id,
            username=username,
            details=f"Имя: {first_name}"
        )
