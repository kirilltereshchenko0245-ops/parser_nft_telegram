"""Thorough, sequential chat member scanner."""
import asyncio
import logging
import random
from datetime import datetime
from telethon.errors import FloodWaitError

log = logging.getLogger('giftparser.parser')

MIN_DELAY = 2.5
MAX_DELAY = 5.5
BATCH_DELAY_MIN = 8
BATCH_DELAY_MAX = 18
RECHECK_AFTER_HOURS = 168  # повторная полная проверка не чаще раза в 7 дней


class ChatScanner:
    def __init__(self, client, database, result_callback=None):
        self.client = client
        self.db = database
        self.result_callback = result_callback
        self.is_running = False
        self.stop_flag = False
        self.mode = 'all'
        self.max_users = 100
        self.stats = {
            'total_checked': 0, 'total_found': 0, 'total_new_gifts': 0,
            'current_chat': None, 'current_chat_title': '',
            'chat_checked': 0, 'chat_found': 0, 'start_time': None,
            'limit': 0,
        }
        self._checked_this_run = set()

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

        enabled_chats = self.db.get_enabled_parser_chats()
        if not enabled_chats:
            self.is_running = False
            return False

        try:
            for chat in enabled_chats:
                if self.stop_flag or self.stats['total_checked'] >= self.max_users:
                    break
                try:
                    await self._scan_chat(chat)
                except FloodWaitError as e:
                    log.warning('FloodWait in chat %s: %ss', chat['title'], e.seconds)
                    await asyncio.sleep(e.seconds)
                except Exception:
                    log.exception('Error scanning chat %s', chat['title'])
        finally:
            self.is_running = False
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
        offset = progress['last_user_offset'] if progress else 0
        checked = progress['checked_count'] if progress else 0
        found = progress['found_count'] if progress else 0
        self.db.update_parser_progress(chat_id, offset, checked, found, 'running')

        batch_size = 50
        since_batch = 0
        while not self.stop_flag and self.stats['total_checked'] < self.max_users:
            participants = await self.client.get_participants(chat_id, limit=batch_size, offset=offset)
            if not participants:
                self.db.update_parser_progress(chat_id, offset, checked, found, 'completed')
                break

            for user in participants:
                if self.stop_flag or self.stats['total_checked'] >= self.max_users:
                    break
                user_id = user['user_id']
                offset += 1

                if user_id in self._checked_this_run:
                    continue
                if self.db.is_user_checked(user_id, hours=RECHECK_AFTER_HOURS):
                    continue

                self._checked_this_run.add(user_id)
                since_batch += 1
                self.stats['total_checked'] += 1
                self.stats['chat_checked'] += 1

                gifts = await self._check_user_gifts(user, chat_id)
                matched = self._filter_mode(gifts)
                has_gifts = bool(matched)
                if has_gifts:
                    self.stats['total_found'] += 1
                    self.stats['chat_found'] += 1

                self.db.add_parser_user(
                    user_id, user.get('username'), user.get('first_name'),
                    user.get('last_name'), has_gifts
                )

                new_gifts = []
                for gift in matched:
                    if self.db.add_parser_gift(user_id, chat_id, gift):
                        new_gifts.append(gift)
                        self.stats['total_new_gifts'] += 1

                if new_gifts and self.result_callback:
                    await self.result_callback(user, new_gifts)

                self.db.update_parser_progress(chat_id, offset, checked + self.stats['chat_checked'], found + self.stats['chat_found'], 'running')

                # Deliberately slow and sequential: quality before speed.
                await self._polite_delay()

                if since_batch >= 20 and not self.stop_flag:
                    await asyncio.sleep(random.uniform(BATCH_DELAY_MIN, BATCH_DELAY_MAX))
                    since_batch = 0

        status = 'stopped' if self.stop_flag else ('completed' if self.stats['total_checked'] >= self.max_users else 'completed')
        self.db.update_parser_progress(chat_id, offset, checked + self.stats['chat_checked'], found + self.stats['chat_found'], status)

    async def _check_user_gifts(self, user, chat_id):
        """Fetch the complete available gift list for this profile, with retries."""
        for attempt in range(3):
            try:
                return await self.client.get_user_gifts(user['user_id'], limit=1000)
            except FloodWaitError as e:
                log.warning('FloodWait checking user %s: %ss', user['user_id'], e.seconds)
                await asyncio.sleep(e.seconds)
            except Exception as e:
                if attempt == 2:
                    log.warning('Gift lookup failed for %s: %s', user['user_id'], e)
                    return []
                await asyncio.sleep(5 * (attempt + 1))
        return []

    def _filter_mode(self, gifts):
        if self.mode == 'all':
            return gifts
        # "Неулучшенные": обычные received gifts, not collectible/unique.
        return [g for g in gifts if not g.get('is_upgraded') and not g.get('is_collectible')]

    async def _polite_delay(self):
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    def stop(self):
        self.stop_flag = True

    def get_stats(self):
        return self.stats.copy()
