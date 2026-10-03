"""Telethon client for the chat/NFT parser.

The user account is authenticated once and persisted as a StringSession.
Gift lookup uses Telegram's MTProto payments.getSavedStarGifts method.
"""
import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
)

log = logging.getLogger('giftparser.parser')

# Telegram application credentials. Account authorization is provided by SESSION_STRING.
API_ID = int(os.getenv('TG_API_ID', '2040'))
API_HASH = os.getenv('TG_API_HASH', 'b18441a1ff607e10a989891a5462e627')
DEFAULT_SESSION_STRING = os.getenv('PARSER_SESSION_STRING', '').strip()



class ParserClient:
    def __init__(self, session_string: str | None = None):
        self.session_string = (session_string or DEFAULT_SESSION_STRING).strip()
        self.client: TelegramClient | None = None
        self._phone: str | None = None
        self._phone_code_hash: str | None = None

    async def _ensure_client(self):
        if self.client is None:
            self.client = TelegramClient(StringSession(self.session_string), API_ID, API_HASH)
        if not self.client.is_connected():
            await self.client.connect()

    async def start_auth(self, phone: str):
        self._phone = phone
        await self._ensure_client()
        if await self.client.is_user_authorized():
            return False
        result = await self.client.send_code_request(phone)
        self._phone_code_hash = result.phone_code_hash
        log.info('Telegram login code sent')
        return True

    async def verify_code(self, code: str):
        if not self.client or not self._phone or not self._phone_code_hash:
            return {'success': False, 'error': 'auth_session_missing', 'message': 'Сессия авторизации потеряна. Начните подключение заново.'}
        try:
            await self.client.sign_in(self._phone, code, phone_code_hash=self._phone_code_hash)
            return {'success': True, 'need_2fa': False}
        except SessionPasswordNeededError:
            return {'success': False, 'need_2fa': True}
        except PhoneCodeInvalidError:
            return {'success': False, 'error': 'invalid_code'}
        except PhoneCodeExpiredError:
            return {'success': False, 'error': 'code_expired', 'message': 'Код истёк. Нажмите отмену и запросите новый код.'}
        except Exception as e:
            log.exception('Code verification failed')
            return {'success': False, 'error': 'unknown', 'message': str(e)}

    async def verify_2fa(self, password: str):
        try:
            await self.client.sign_in(password=password)
            return {'success': True}
        except PasswordHashInvalidError:
            return {'success': False, 'error': 'invalid_password'}
        except Exception as e:
            log.exception('2FA verification failed')
            return {'success': False, 'error': str(e)}

    async def get_session_string(self):
        if self.client and await self.client.is_user_authorized():
            self.session_string = self.client.session.save()
            return self.session_string
        return None

    async def get_me(self):
        if self.client and await self.client.is_user_authorized():
            me = await self.client.get_me()
            return {
                'user_id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone,
            }
        return None

    async def connect(self):
        if not self.session_string:
            raise ValueError('Session string не установлен')
        self.client = TelegramClient(StringSession(self.session_string), API_ID, API_HASH)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            self.client = None
            raise RuntimeError('Сессия Telegram недействительна или истекла')
        log.info('MTProto client restored from StringSession')
        return True

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def get_dialogs(self):
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        dialogs = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                entity = dialog.entity
                dialogs.append({
                    'chat_id': dialog.id,
                    'title': dialog.title or 'Без названия',
                    'username': getattr(entity, 'username', None),
                    'type': 'channel' if dialog.is_channel else 'group',
                    'participants_count': getattr(entity, 'participants_count', 0) or 0,
                })
        return dialogs

    async def iter_participants(self, chat_id, limit=None):
        """Yield participants using Telethon's supported iterator signature."""
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        count = 0
        async for user in self.client.iter_participants(chat_id, limit=limit):
            if getattr(user, 'bot', False) or getattr(user, 'deleted', False):
                continue
            yield {
                'user_id': user.id,
                'username': getattr(user, 'username', None),
                'first_name': getattr(user, 'first_name', None),
                'last_name': getattr(user, 'last_name', None),
                'is_bot': bool(getattr(user, 'bot', False)),
            }
            count += 1
            if limit is not None and count >= limit:
                break

    async def get_participants(self, chat_id, limit=None, offset=0):
        """Compatibility wrapper; offset is applied locally because Telethon's
        installed iter_participants does not accept an offset keyword."""
        participants = []
        skipped = 0
        async for user in self.iter_participants(chat_id, limit=None):
            if skipped < max(0, int(offset or 0)):
                skipped += 1
                continue
            participants.append(user)
            if limit is not None and len(participants) >= limit:
                break
        return participants

    async def get_user_gifts(self, user_id: int, limit: int = 1000):
        """Return gifts owned/received/hosted by a Telegram user.

        Telegram's current API exposes both saved and unsaved gifts through
        payments.getSavedStarGifts. Pagination is followed until exhausted.
        """
        if not self.client:
            raise RuntimeError('Клиент не подключён')

        peer = await self.client.get_input_entity(user_id)
        offset = ''
        result = []
        seen = set()

        while True:
            response = await self.client(functions.payments.GetSavedStarGiftsRequest(
                peer=peer,
                offset=offset,
                limit=min(100, limit),
                exclude_unsaved=False,
                exclude_saved=False,
                exclude_unlimited=False,
                exclude_unique=False,
                exclude_upgradable=False,
                exclude_unupgradable=False,
                exclude_hosted=False,
            ))

            gifts = getattr(response, 'gifts', None) or []
            for saved in gifts:
                data = self._normalize_saved_gift(saved, user_id)
                key = data.get('unique_key') or (data.get('gift_id'), data.get('gift_num'), data.get('slug'))
                if key in seen:
                    continue
                seen.add(key)
                result.append(data)
                if len(result) >= limit:
                    return result

            next_offset = getattr(response, 'next_offset', None)
            if not next_offset or next_offset == offset or not gifts:
                break
            offset = next_offset

        return result

    @staticmethod
    def _normalize_saved_gift(saved: Any, owner_id: int) -> dict:
        gift = getattr(saved, 'gift', None)
        gift_id = getattr(gift, 'id', None)
        title = getattr(gift, 'title', None)
        gift_num = getattr(saved, 'gift_num', None) or getattr(gift, 'num', None)
        slug = getattr(gift, 'slug', None)
        unique = isinstance(gift, types.StarGiftUnique)
        attributes = []
        for attr in getattr(gift, 'attributes', None) or []:
            attributes.append(GiftParserAttribute.normalize(attr))

        return {
            'gift_id': gift_id,
            'gift_num': gift_num,
            'gift_name': title or getattr(gift, 'title', None) or 'Telegram Gift',
            'gift_slug': slug,
            'is_collectible': unique,
            'is_upgraded': unique,
            # Telegram sets savedStarGift.can_upgrade only for non-collectible
            # gifts that can be upgraded into a collectible gift.
            'can_upgrade': bool(getattr(saved, 'can_upgrade', False)),
            'is_nft_candidate': bool(unique or getattr(saved, 'can_upgrade', False)),
            'attributes': attributes,
            'owner_id': owner_id,
            'unique_key': (getattr(gift, 'id', None), gift_num, slug),
            'raw_type': type(gift).__name__ if gift else type(saved).__name__,
        }

    def is_connected(self):
        return bool(self.client and self.client.is_connected())


class GiftParserAttribute:
    @staticmethod
    def normalize(attr: Any) -> dict:
        name = type(attr).__name__
        if hasattr(attr, 'name'):
            value = getattr(attr, 'name')
        elif hasattr(attr, 'pattern'):
            value = getattr(attr, 'pattern')
        elif hasattr(attr, 'backdrop'):
            value = getattr(attr, 'backdrop')
        else:
            value = None
        return {'type': name, 'value': value}
