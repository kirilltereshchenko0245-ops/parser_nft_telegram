"""
Модуль для работы с платежами через CryptoBot и XRocket
"""
import aiohttp
import logging
from typing import Optional, Dict

# API ключи
CRYPTOBOT_TOKEN = "629296:AAw96aFWy4zy2Wz6t96lZU2HcccHN3CVyI5"
XROCKET_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHBJZCI6IjMwMDQ4MyIsImp0aSI6ImFwcDozMDA0ODM6ZjE1N2MyNzktOTMzZC00ZDI2LTk5MDctOWYzMzMxMjQ1ZWQyIiwiaWF0IjoxNzkwNzkxMjE5fQ.A4DFBmiCt5BD6TqOzA_ed_jRuSa0XWWRGu8PSN9X54k"

# Цены подписок
SUBSCRIPTION_PRICE_TON = 5
NONUPGRADED_SUBSCRIPTION_PRICE_TON = 5

class PaymentManager:
    """Управление платежами"""
    
    @staticmethod
    async def create_cryptobot_invoice(user_id: int, username: str = None, is_nonupgraded: bool = False) -> Optional[str]:
        """Создать счёт в CryptoBot"""
        url = "https://pay.crypt.bot/api/createInvoice"
        
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
        }
        
        price = NONUPGRADED_SUBSCRIPTION_PRICE_TON if is_nonupgraded else SUBSCRIPTION_PRICE_TON
        description = "Подписка на неулучшенные подарки" if is_nonupgraded else "Premium подписка - Telegram Gift Parser"
        payload_prefix = "nonupgraded_premium" if is_nonupgraded else "premium"
        
        payload = {
            "amount": price,
            "asset": "TON",
            "description": description,
            "payload": f"{payload_prefix}_{user_id}",
            "allow_comments": False,
            "allow_anonymous": False
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("ok"):
                            return data["result"]["pay_url"]
                    else:
                        logging.error(f"CryptoBot error: {response.status}")
        except Exception as e:
            logging.error(f"CryptoBot exception: {e}")
        
        return None
    
    @staticmethod
    async def create_xrocket_invoice(user_id: int, is_nonupgraded: bool = False) -> Optional[str]:
        """Создать счёт в XRocket"""
        url = "https://pay.xrocket.tg/api/v1/invoices"
        
        headers = {
            "X-Api-Key": XROCKET_TOKEN,
            "Content-Type": "application/json"
        }
        
        price = NONUPGRADED_SUBSCRIPTION_PRICE_TON if is_nonupgraded else SUBSCRIPTION_PRICE_TON
        description = "Подписка на неулучшенные подарки" if is_nonupgraded else "Premium подписка - Gift Parser"
        external_prefix = "nonupgraded_premium" if is_nonupgraded else "premium"
        
        payload = {
            "amount": price,
            "currency": "TON",
            "description": description,
            "externalId": f"{external_prefix}_{user_id}",
            "successUrl": "https://t.me/your_bot",
            "failUrl": "https://t.me/your_bot"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("payUrl")
                    else:
                        logging.error(f"XRocket error: {response.status}")
        except Exception as e:
            logging.error(f"XRocket exception: {e}")
        
        return None
    
    @staticmethod
    async def check_cryptobot_payment(invoice_id: str) -> bool:
        """Проверить статус платежа CryptoBot"""
        # Эта функция будет вызываться при webhook от CryptoBot
        # Пока заглушка
        return False
    
    @staticmethod
    async def check_xrocket_payment(invoice_id: str) -> bool:
        """Проверить статус платежа XRocket"""
        # Эта функция будет вызываться при webhook от XRocket
        # Пока заглушка
        return False
