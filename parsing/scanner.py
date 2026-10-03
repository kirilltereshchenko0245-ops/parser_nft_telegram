"""Long-running, sequential Telegram chat/NFT scanner."""
import asyncio
import logging
import random
from datetime import datetime
from telethon.errors import FloodWaitError

log = logging.getLogger('giftparser.parser')

MIN_DELAY = 2.5
MAX_DELAY = 5.5
BATCH_DELAY_MIN = 10
BATCH_DELAY_MAX = 30
RECHECK_AFTER_HOURS = 168
MAX_RETRIES = 5


class ChatScanner:
    def __init__(self, client, database, result_callback=None):
        self.client = client
        self.db = database
        self.result_callback = result_callback
        self.is_running = False
        self.stop_flag = False
        self.mode = 'all'
        self.max_users = 100
        self.stats = {'total_checked': 0, 'total_found': 0, 'total_new_gifts': 0,
                      'current_chat': None, 'current_chat_title': '',
                      'start_time': None, 'limit': 0}
        self._checked_this_run = set()
        self.self_user_id = None

    async def start_scan(self, mode='all', max_users=100):
        if self.is_running:
            return False
        self.is_running = True
        self.stop_flag = False
        self.mode = mode
        self.max_users = max(1, int(max_users))
        self.stats['start_time'] = datetime.now()
        self.stats['limit'] = self.max_users
        self._checked_this_run.clear()
        # Never scan the Telegram account used by the parser itself.
        try:
            me = await self.client.get_me()
            self.self_user_id = (me or {}).get('user_id') if isinstance(me, dict) else getattr(me, 'id', None)
            log.info('Parser account id=%s will be excluded from participant scanning', self.self_user_id)
        except Exception:
            log.exception('Could not determine parser account id; self-account protection is unavailable')
            self.self_user_id = None
        enabled_chats = self.db.get_enabled_parser_chats()
        if not enabled_chats:
            self.is_running = False
            self.db.set_parser_scan_status('idle')
            return False
        interrupted = False
        try:
            for chat in enabled_chats:
                if self.stop_flag or self.stats['total_checked'] >= self.max_users:
                    break
                try:
                    await self._scan_chat(chat)
                except FloodWaitError as e:
                    log.warning('FloodWait in chat %s: %ss', chat['title'], e.seconds)
                    await asyncio.sleep(e.seconds + random.uniform(1, 3))
                except Exception:
                    log.exception('Error scanning chat %s; continuing with next chat', chat['title'])
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            interrupted = True
            self.db.set_parser_scan_status('running')
            raise
        finally:
            self.is_running = False
            if not interrupted:
                status = 'stopped' if self.stop_flag else 'completed'
                self.db.set_parser_scan_status(status)
            log.info('Scan finished: checked=%s found=%s new_gifts=%s',
                     self.stats['total_checked'], self.stats['total_found'], self.stats['total_new_gifts'])
        return True

    async def _scan_chat(self, chat):
        chat_id = chat['chat_id']
        self.stats['current_chat'] = chat_id
        self.stats['current_chat_title'] = chat['title']
        self.stats['chat_checked'] = 0
        self.stats['chat_found'] = 0
        progress = self.db.get_parser_progress(chat_id)
        offset = int(progress['last_user_offset']) if progress else 0
        checked_before = int(progress['checked_count']) if progress else 0
        found_before = int(progress['found_count']) if progress else 0
        self.db.update_parser_progress(chat_id, offset, checked_before, found_before, 'running')
        processed_since_pause = 0

        # Do not pass offset to Telethon: current installed versions reject it.
        # We consume the iterator once and locally skip the saved position.
        index = 0
        async for user in self.client.iter_participants(chat_id, limit=None):
            if self.stop_flag or self.stats['total_checked'] >= self.max_users:
                break
            if index < offset:
                index += 1
                continue
            index += 1
            offset = index

            if getattr(user, 'bot', False) or getattr(user, 'deleted', False):
                continue
            # ParserClient.iter_participants() normally yields dictionaries,
            # but keep compatibility with raw Telethon User objects too.
            if isinstance(user, dict):
                user_data = {
                    'user_id': user.get('user_id') or user.get('id'),
                    'username': user.get('username'),
                    'first_name': user.get('first_name'),
                    'last_name': user.get('last_name'),
                    'is_bot': bool(user.get('is_bot') or user.get('bot')),
                }
            else:
                user_data = {
                    'user_id': getattr(user, 'id', None),
                    'username': getattr(user, 'username', None),
                    'first_name': getattr(user, 'first_name', None),
                    'last_name': getattr(user, 'last_name', None),
                    'is_bot': bool(getattr(user, 'bot', False)),
                }
            user_id = user_data['user_id']
            if not user_id:
                log.warning('Participant without user_id; skipping: %r', user)
                continue
            # Do not ever parse the account that is used for MTProto access.
            if self.self_user_id and int(user_id) == int(self.self_user_id):
                log.info('Skipping parser account itself: %s', user_id)
                continue

            # A profile may be checked again. The database is responsible for
            # deduplicating gifts, while the run limit must mean a real number
            # of profiles actually inspected during THIS run.
            if user_id in self._checked_this_run:
                continue

            self._checked_this_run.add(user_id)
            self.stats['total_checked'] += 1
            self.stats['chat_checked'] += 1
            processed_since_pause += 1

            gifts = await self._check_user_gifts(user_data)
            matched = self._filter_mode(gifts)
            has_gifts = bool(matched)
            if has_gifts:
                self.stats['total_found'] += 1
                self.stats['chat_found'] += 1

            self.db.add_parser_user(user_id, user_data.get('username'), user_data.get('first_name'),
                                    user_data.get('last_name'), has_gifts)
            new_gifts = []
            for gift in matched:
                try:
                    if self.db.add_parser_gift(user_id, chat_id, gift):
                        new_gifts.append(gift)
                        self.stats['total_new_gifts'] += 1
                except Exception:
                    log.exception('Failed saving gift for user %s; continuing', user_id)

            if new_gifts and self.result_callback:
                try:
                    await self.result_callback(user_data, new_gifts)
                except Exception:
                    log.exception('Result callback failed for user %s; scanner continues', user_id)

            self.db.update_parser_progress(chat_id, offset,
                                           checked_before + self.stats['chat_checked'],
                                           found_before + self.stats['chat_found'], 'running')
            await self._polite_delay()
            if processed_since_pause >= 20 and not self.stop_flag:
                await asyncio.sleep(random.uniform(BATCH_DELAY_MIN, BATCH_DELAY_MAX))
                processed_since_pause = 0

        status = 'stopped' if self.stop_flag else 'completed'
        self.db.update_parser_progress(chat_id, offset,
                                       checked_before + self.stats['chat_checked'],
                                       found_before + self.stats['chat_found'], status)

    async def _check_user_gifts(self, user):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await self.client.get_user_gifts(user['user_id'], limit=1000)
            except FloodWaitError as e:
                log.warning('FloodWait checking user %s: %ss', user['user_id'], e.seconds)
                await asyncio.sleep(e.seconds + random.uniform(1, 3))
            except (ConnectionError, TimeoutError) as e:
                log.warning('Network error for %s (%s/%s): %s', user['user_id'], attempt, MAX_RETRIES, e)
                await self._recover_connection()
                await asyncio.sleep(min(60, 3 * attempt))
            except Exception as e:
                if attempt == MAX_RETRIES:
                    log.warning('Gift lookup failed for %s after %s attempts: %s', user['user_id'], attempt, e)
                    return []
                await asyncio.sleep(min(60, 4 * attempt))
        return []

    async def _recover_connection(self):
        try:
            if not self.client.is_connected():
                await self.client.connect()
        except Exception:
            log.exception('Telegram reconnect failed; retrying later')

    def _filter_mode(self, gifts):
        # 'NFT' here means a collectible gift or a non-collectible gift that
        # Telegram explicitly marks as upgradeable to a collectible. Ordinary
        # Telegram gifts must never be reported as NFT.
        nft_gifts = [g for g in gifts if g.get('is_nft_candidate')]
        if self.mode == 'all':
            return nft_gifts
        # Unupgraded NFT = not collectible yet, but explicitly upgradeable.
        return [g for g in nft_gifts if g.get('can_upgrade') and not g.get('is_collectible')]

    async def _polite_delay(self):
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    def stop(self):
        self.stop_flag = True

    def get_stats(self):
        return self.stats.copy()
