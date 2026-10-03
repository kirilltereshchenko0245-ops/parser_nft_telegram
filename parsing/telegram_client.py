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

    async def iter_random_participants(self, chat_id, participant_count=0, batch_size=100):
        """Yield participants from randomized API windows instead of walking from offset 0.

        Telegram does not expose a true random-member API. We approximate random
        sampling by requesting ChannelParticipantsSearch windows at random offsets.
        This avoids restarting every deployment at the first member and works
        much better with a persistent seen-user table in the scanner.
        """
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        import random
        from telethon.errors import FloodWaitError
        entity = await self.client.get_input_entity(chat_id)
        total = int(participant_count or 0)
        if total <= 0:
            try:
                probe = await self.client(functions.channels.GetParticipantsRequest(
                    channel=entity,
                    filter=types.ChannelParticipantsSearch(q=''),
                    offset=0, limit=1, hash=0))
                total = int(getattr(probe, 'count', 0) or 0)
            except Exception:
                total = 10000
        seen_ids = set()
        attempts = 0
        max_attempts = max(30, min(500, total // max(1, batch_size) * 4 + 20))
        while attempts < max_attempts:
            attempts += 1
            max_offset = max(0, total - batch_size)
            offset = random.randint(0, max_offset) if max_offset else 0
            try:
                response = await self.client(functions.channels.GetParticipantsRequest(
                    channel=entity,
                    filter=types.ChannelParticipantsSearch(q=''),
                    offset=offset,
                    limit=batch_size,
                    hash=0))
            except FloodWaitError:
                raise
            users = getattr(response, 'users', None) or []
            if not users:
                continue
            random.shuffle(users)
            for user in users:
                uid = getattr(user, 'id', None)
                if not uid or uid in seen_ids:
                    continue
                seen_ids.add(uid)
                if getattr(user, 'bot', False) or getattr(user, 'deleted', False):
                    continue
                yield {
                    'user_id': uid,
                    'username': getattr(user, 'username', None),
                    'first_name': getattr(user, 'first_name', None),
                    'last_name': getattr(user, 'last_name', None),
                    'is_bot': bool(getattr(user, 'bot', False)),
                }

    async def iter_random_message_authors(self, chat_id, max_messages=50000):
        """Randomly sample message windows in a hidden-member chat."""
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        import random
        from telethon.errors import FloodWaitError
        probe = await self.client.get_messages(chat_id, limit=1)
        max_id = 0
        if probe:
            first = probe[0] if isinstance(probe, (list, tuple)) else probe
            max_id = int(getattr(first, 'id', 0) or 0)
        if max_id <= 0:
            return
        seen_ids = set()
        seen_offsets = set()
        attempts = 0
        while attempts < 300 and len(seen_offsets) < 300:
            attempts += 1
            offset = random.randint(1, max_id)
            if offset in seen_offsets:
                continue
            seen_offsets.add(offset)
            try:
                messages = await self.client.get_messages(chat_id, limit=100, offset_id=offset)
            except FloodWaitError:
                raise
            for message in messages or []:
                sender = getattr(message, 'sender', None)
                if sender is None:
                    try:
                        sender = await message.get_sender()
                    except Exception:
                        sender = None
                if not isinstance(sender, types.User):
                    continue
                uid = getattr(sender, 'id', None)
                if not uid or uid in seen_ids or getattr(sender, 'bot', False) or getattr(sender, 'deleted', False):
                    continue
                seen_ids.add(uid)
                yield {
                    'user_id': uid,
                    'username': getattr(sender, 'username', None),
                    'first_name': getattr(sender, 'first_name', None),
                    'last_name': getattr(sender, 'last_name', None),
                    'is_bot': bool(getattr(sender, 'bot', False)),
                }

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

    async def participant_access(self, chat_id):
        """Return Telegram's participant-list visibility for a supergroup/channel.

        Hidden-member groups intentionally do not expose the full member list to
        clients. In that case the scanner must use the message-author fallback
        instead of treating an empty participant iterator as a completed scan.
        """
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        try:
            entity = await self.client.get_input_entity(chat_id)
            full = await self.client(functions.channels.GetFullChannelRequest(entity))
            full_chat = getattr(full, 'full_chat', None)
            hidden = bool(getattr(full_chat, 'participants_hidden', False))
            can_view = getattr(full_chat, 'can_view_participants', None)
            return {'hidden': hidden, 'can_view': can_view}
        except Exception as e:
            log.warning('Could not determine participant visibility for %s: %s', chat_id, e)
            return {'hidden': False, 'can_view': None}

    async def iter_message_authors(self, chat_id, max_messages=50000):
        """Yield unique human users who authored messages in a chat.

        This is a fallback for supergroups whose participant list is hidden.
        It cannot reveal silent/unknown members; it only discovers users whose
        messages are visible to the connected account.
        """
        if not self.client:
            raise RuntimeError('Клиент не подключён')
        seen = set()
        scanned_messages = 0
        async for message in self.client.iter_messages(chat_id, limit=max_messages):
            scanned_messages += 1
            sender = getattr(message, 'sender', None)
            if sender is None:
                try:
                    sender = await message.get_sender()
                except Exception:
                    sender = None
            if not isinstance(sender, types.User):
                continue
            if getattr(sender, 'bot', False) or getattr(sender, 'deleted', False):
                continue
            user_id = getattr(sender, 'id', None)
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            yield {
                'user_id': user_id,
                'username': getattr(sender, 'username', None),
                'first_name': getattr(sender, 'first_name', None),
                'last_name': getattr(sender, 'last_name', None),
                'is_bot': bool(getattr(sender, 'bot', False)),
            }
        log.info('Hidden-member fallback exhausted chat=%s messages=%s unique_users=%s',
                 chat_id, scanned_messages, len(seen))

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

    async def get_user_gifts(self, user_id: int, limit: int = 1000, only_unupgraded: bool = False):
        """Return relevant gifts owned by a Telegram user.

        For the unupgraded mode Telegram can filter server-side:
        exclude_unupgradable=True leaves gifts that can actually be upgraded
        to collectible. This avoids downloading ordinary gifts and is much
        faster for large gift inventories.
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
                # In 'all NFT' mode keep unique collectibles and gifts that can
                # be upgraded. Ordinary non-upgradable gifts are not relevant.
                exclude_unique=False,
                exclude_upgradable=False,
                exclude_unupgradable=True,
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
