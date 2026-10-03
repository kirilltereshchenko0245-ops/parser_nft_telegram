"""MTProto клиент через Telethon с авторизацией через бот."""
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError

log = logging.getLogger('giftparser.parser')

# Публичные credentials от Telegram (безопасно использовать)
API_ID = 2040
API_HASH = 'b18441a1ff607e10a989891a5462e627'


class ParserClient:
    """Обёртка над Telethon для авторизации и работы с чатами."""
    
    def __init__(self, session_string: str = None):
        self.session_string = session_string or ''
        self.client = None
        self._phone = None
        self._phone_code_hash = None
    
    async def start_auth(self, phone: str):
        """Шаг 1: отправка кода на номер телефона."""
        self._phone = phone
        
        # Если клиент уже создан, используем его
        if not self.client:
            self.client = TelegramClient(StringSession(self.session_string), API_ID, API_HASH)
            await self.client.connect()
        
        if not await self.client.is_user_authorized():
            result = await self.client.send_code_request(phone)
            self._phone_code_hash = result.phone_code_hash
            log.info(f'Код отправлен на {phone}')
            return True
        else:
            log.info('Аккаунт уже авторизован')
            return False
    
    async def verify_code(self, code: str):
        """Шаг 2: проверка кода из Telegram."""
        try:
            await self.client.sign_in(self._phone, code, phone_code_hash=self._phone_code_hash)
            return {'success': True, 'need_2fa': False}
        except SessionPasswordNeededError:
            log.info('Требуется 2FA пароль')
            return {'success': False, 'need_2fa': True}
        except PhoneCodeInvalidError:
            log.warning('Неверный код')
            return {'success': False, 'error': 'invalid_code'}
        except Exception as e:
            error_msg = str(e)
            log.error(f'Ошибка при проверке кода: {error_msg}')
            
            # Обработка истёкшего кода
            if 'expired' in error_msg.lower() or 'confirmation code has expired' in error_msg.lower():
                return {'success': False, 'error': 'code_expired', 'message': 'Код истёк. Начните подключение заново'}
            
            return {'success': False, 'error': 'unknown', 'message': error_msg}
    
    async def verify_2fa(self, password: str):
        """Шаг 3: проверка 2FA пароля."""
        try:
            await self.client.sign_in(password=password)
            return {'success': True}
        except PasswordHashInvalidError:
            log.warning('Неверный 2FA пароль')
            return {'success': False, 'error': 'invalid_password'}
        except Exception as e:
            log.error(f'Ошибка при проверке 2FA: {e}')
            return {'success': False, 'error': str(e)}
    
    async def get_session_string(self):
        """Получить StringSession после успешной авторизации."""
        if self.client and await self.client.is_user_authorized():
            return self.client.session.save()
        return None
    
    async def get_me(self):
        """Получить информацию о текущем аккаунте."""
        if self.client and await self.client.is_user_authorized():
            me = await self.client.get_me()
            return {
                'user_id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
        return None
    
    async def connect(self):
        """Подключиться с существующей сессией."""
        if not self.session_string:
            raise ValueError('Session string не установлен')
        
        self.client = TelegramClient(StringSession(self.session_string), API_ID, API_HASH)
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            raise RuntimeError('Сессия недействительна')
        
        log.info('MTProto клиент подключён')
        return True
    
    async def disconnect(self):
        """Отключиться."""
        if self.client:
            await self.client.disconnect()
            log.info('MTProto клиент отключён')
    
    async def get_dialogs(self):
        """Получить список всех чатов."""
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        
        dialogs = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                dialogs.append({
                    'chat_id': dialog.id,
                    'title': dialog.title,
                    'username': dialog.entity.username if hasattr(dialog.entity, 'username') else None,
                    'type': 'channel' if dialog.is_channel else 'group',
                    'participants_count': getattr(dialog.entity, 'participants_count', 0)
                })
        
        log.info(f'Найдено {len(dialogs)} чатов')
        return dialogs
    
    async def get_participants(self, chat_id, limit=None, offset=0):
        """Получить участников чата."""
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        
        participants = []
        try:
            async for user in self.client.iter_participants(chat_id, limit=limit, offset=offset):
                if user.bot:
                    continue
                
                participants.append({
                    'user_id': user.id,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'is_bot': user.bot
                })
            
            return participants
        except Exception as e:
            log.error(f'Ошибка получения участников чата {chat_id}: {e}')
            return []
    
    async def get_user_profile_photos(self, user_id):
        """Проверить есть ли у пользователя подарки через анализ профиля."""
        # TODO: Реализовать получение подарков через Telegram API
        # Пока заглушка - вернёт пустой список
        return []
    
    def is_connected(self):
        """Проверка подключения."""
        return self.client and self.client.is_connected()
