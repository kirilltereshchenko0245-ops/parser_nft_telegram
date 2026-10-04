"""Long-running Telegram chat/NFT scanner with round-robin chat scheduling."""
import asyncio
import logging
import random
import os
from datetime import datetime
from telethon.errors import FloodWaitError

log = logging.getLogger('giftparser.parser')

# Faster, but still deliberately paced. A FloodWait from Telegram always wins.
MIN_DELAY = float(os.getenv('PARSER_DELAY_MIN', '0.8'))
MAX_DELAY = float(os.getenv('PARSER_DELAY_MAX', '1.8'))
BATCH_DELAY_MIN = float(os.getenv('PARSER_BATCH_DELAY_MIN', '4'))
BATCH_DELAY_MAX = float(os.getenv('PARSER_BATCH_DELAY_MAX', '8'))
RECHECK_AFTER_HOURS = 168
MAX_RETRIES = 4
CHAT_QUOTA = 2  # Exactly two profiles per enabled chat per round.


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
                      'nft_users': [],
                      'current_chat': None, 'current_chat_title': '',
                      'start_time': None, 'limit': 0}
        self._checked_this_run = set()
        self._sent_this_run = set()
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
        self.stats['current_chat'] = None
        self.stats['current_chat_title'] = ''
        self.stats['total_checked'] = 0
        self.stats['total_found'] = 0
        self.stats['total_new_gifts'] = 0
        self.stats['nft_users'] = []
        self._checked_this_run.clear()
        self._sent_this_run.clear()

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
            await self._scan_round_robin(enabled_chats)
        except asyncio.CancelledError:
            interrupted = True
            # A user-requested stop is a normal terminal state, not an interrupted scan.
            self.db.set_parser_scan_status('stopped' if self.stop_flag else 'running')
            raise
        except Exception:
            log.exception('Unexpected scanner error')
        finally:
            self.is_running = False
            if not interrupted:
                status = 'stopped' if self.stop_flag else 'completed'
                self.db.set_parser_scan_status(status)
            log.info('Scan finished: checked=%s found=%s new_gifts=%s',
                     self.stats['total_checked'], self.stats['total_found'], self.stats['total_new_gifts'])
        return True

    async def _scan_round_robin(self, chats):
        """Check users in batches of two per chat, cycling through all enabled chats."""
        states = []
        for chat in chats:
            try:
                states.append(await self._make_chat_state(chat))
            except Exception:
                log.exception('Could not initialize chat %s; skipping', chat.get('title'))

        active = [s for s in states if s]
        if not active:
            log.warning('No enabled chat produced a usable participant/message iterator')
            return

        log.info('Round-robin scan initialized: chats=%s quota_per_chat=%s limit=%s',
                 len(active), CHAT_QUOTA, self.max_users)

        while active and not self.stop_flag and self.stats['total_checked'] < self.max_users:
            next_active = []
            made_progress = False
            for state in active:
                if self.stop_flag or self.stats['total_checked'] >= self.max_users:
                    break
                processed_here = 0
                for _ in range(CHAT_QUOTA):
                    if self.stop_flag or self.stats['total_checked'] >= self.max_users:
                        break
                    user = await self._next_user(state)
                    if user is None:
                        break
                    processed = await self._process_user(state, user)
                    if processed:
                        made_progress = True
                        processed_here += 1
                if not state['exhausted']:
                    next_active.append(state)
                log.info('Round chat=%s checked_this_round=%s total=%s/%s',
                         state['chat']['title'], processed_here,
                         self.stats['total_checked'], self.max_users)
            active = next_active
            if active and not made_progress:
                log.warning('Round-robin made no progress; stopping to avoid an infinite loop')
                break

        for state in states:
            self._save_state(state, 'stopped' if self.stop_flag else 'completed')

    async def _make_chat_state(self, chat):
        chat_id = chat['chat_id']
        progress = self.db.get_parser_progress(chat_id)
        offset = int(progress['last_user_offset']) if progress else 0
        checked_before = int(progress['checked_count']) if progress else 0
        found_before = int(progress['found_count']) if progress else 0

        access = await self.client.participant_access(chat_id) if hasattr(self.client, 'participant_access') else {'hidden': False, 'can_view': None}
        hidden = bool(access.get('hidden')) or access.get('can_view') is False
        if hidden:
            log.warning('Chat %s has hidden/inaccessible members; using batched randomized message-author discovery', chat_id)
            if hasattr(self.client, 'iter_random_message_authors'):
                user_iter = self.client.iter_random_message_authors(chat_id, max_messages=50000)
            else:
                user_iter = self.client.iter_message_authors(chat_id, max_messages=50000)
        else:
            log.info('Chat %s uses RANDOM participant sampling; reported_count=%s', chat_id, chat.get('total_participants'))
            if hasattr(self.client, 'iter_random_participants'):
                user_iter = self.client.iter_random_participants(chat_id, chat.get('total_participants', 0), batch_size=100)
            else:
                user_iter = self.client.iter_participants(chat_id, limit=None)

        state = {
            'chat': chat,
            'chat_id': chat_id,
            'offset': offset,
            'checked_before': checked_before,
            'found_before': found_before,
            'chat_checked': 0,
            'chat_found': 0,
            'index': 0,
            'iter': user_iter,
            'exhausted': False,
            'hidden': hidden,
        }
        self.db.update_parser_progress(chat_id, offset, checked_before, found_before, 'running')
        return state

    async def _next_user(self, state):
        while not state['exhausted']:
            try:
                user = await state['iter'].__anext__()
            except StopAsyncIteration:
                state['exhausted'] = True
                self._save_state(state, 'completed')
                return None
            except FloodWaitError as e:
                log.warning('FloodWait while reading chat %s: %ss', state['chat']['title'], e.seconds)
                await asyncio.sleep(e.seconds + random.uniform(1, 3))
                continue
            except Exception:
                log.exception('Participant/message iterator failed for chat %s', state['chat']['title'])
                state['exhausted'] = True
                self._save_state(state, 'error')
                return None

            state['index'] += 1
            state['offset'] = state['index']

            if self._is_bot_or_deleted(user):
                continue
            user_data = self._normalize_user(user)
            user_id = user_data.get('user_id')
            if not user_id:
                continue
            if self.self_user_id and int(user_id) == int(self.self_user_id):
                log.info('Skipping parser account itself: %s', user_id)
                continue
            if int(user_id) in self._checked_this_run:
                continue
            # Persistent global de-duplication: a user already checked in a previous
            # run is never selected again, even after a bot restart, provided the DB
            # itself is persistent (Railway Volume / external DB).
            if hasattr(self.db, 'is_parser_user_seen') and self.db.is_parser_user_seen(user_id):
                log.info('Skipping previously scanned user=%s chat=%s', user_id, state['chat_id'])
                continue
            return user_data
        return None

    @staticmethod
    def _is_bot_or_deleted(user):
        if isinstance(user, dict):
            return bool(user.get('is_bot') or user.get('bot') or user.get('deleted'))
        return bool(getattr(user, 'bot', False) or getattr(user, 'deleted', False))

    @staticmethod
    def _normalize_user(user):
        if isinstance(user, dict):
            return {
                'user_id': user.get('user_id') or user.get('id'),
                'username': user.get('username'),
                'first_name': user.get('first_name'),
                'last_name': user.get('last_name'),
                'is_bot': bool(user.get('is_bot') or user.get('bot')),
                'access_hash': user.get('access_hash'),
            }
        return {
            'user_id': getattr(user, 'id', None),
            'username': getattr(user, 'username', None),
            'first_name': getattr(user, 'first_name', None),
            'last_name': getattr(user, 'last_name', None),
            'is_bot': bool(getattr(user, 'bot', False)),
            'access_hash': getattr(user, 'access_hash', None),
        }

    async def _process_user(self, state, user_data):
        user_id = user_data['user_id']
        self._checked_this_run.add(int(user_id))
        self.stats['total_checked'] += 1
        state['chat_checked'] += 1
        self.stats['current_chat'] = state['chat_id']
        self.stats['current_chat_title'] = state['chat']['title']
        log.info('Checking profile %s/%s in chat %s: user_id=%s username=%s',
                 self.stats['total_checked'], self.max_users, state['chat_id'],
                 user_id, user_data.get('username'))

        gifts = await self._check_user_gifts(user_data)
        matched = self._filter_mode(gifts)
        has_gifts = bool(matched)
        if has_gifts:
            self.stats['total_found'] += 1
            state['chat_found'] += 1
            self.stats.setdefault('nft_users', []).append({
                'username': user_data.get('username'),
                'user_id': int(user_id),
                'gift_count': len(matched),
            })

        self.db.add_parser_user(user_id, user_data.get('username'), user_data.get('first_name'),
                                user_data.get('last_name'), has_gifts)
        # Persist the user as seen before sending result cards. This makes the
        # "one person only once" rule independent of chat order and prevents a
        # second occurrence from another selected chat in the same run.
        if hasattr(self.db, 'mark_parser_user_seen'):
            self.db.mark_parser_user_seen(user_id, state['chat_id'])
        # Only gifts that were actually inserted into the persistent DB are
        # allowed to be sent to the user. Previously we sent every matched gift
        # even when add_parser_gift() reported that it already existed, which
        # caused the same person's gifts to appear again on later scans.
        live_gifts = []
        for gift in matched:
            try:
                inserted = self.db.add_parser_gift(user_id, state['chat_id'], gift)
            except Exception:
                inserted = False
                log.exception('Failed saving gift for user %s; continuing', user_id)
            if not inserted:
                log.info('Skipping already stored gift user=%s gift_id=%s gift_num=%s slug=%s',
                         user_id, gift.get('gift_id'), gift.get('gift_num'), gift.get('gift_slug'))
                continue
            key = (int(user_id), gift.get('gift_id'), gift.get('gift_num'), gift.get('gift_slug'))
            if key not in self._sent_this_run:
                self._sent_this_run.add(key)
                live_gifts.append(gift)
        if live_gifts and self.result_callback:
            user_data['_source_chat_title'] = state['chat'].get('title') or str(state['chat_id'])
            user_data['_source_chat_id'] = state['chat_id']
            try:
                await self.result_callback(user_data, live_gifts)
                self.stats['total_new_gifts'] += len(live_gifts)
                log.info('Sent live NFT result for user %s: gifts=%s chat=%s', user_id, len(live_gifts), state['chat']['title'])
            except Exception:
                log.exception('Result callback failed for user %s; scanner continues', user_id)

        self._save_state(state, 'running')
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
        return True

    def _save_state(self, state, status='running'):
        self.db.update_parser_progress(
            state['chat_id'], state['offset'],
            state['checked_before'] + state['chat_checked'],
            state['found_before'] + state['chat_found'], status)

    async def _check_user_gifts(self, user):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await self.client.get_user_gifts(user['user_id'], limit=1000, only_unupgraded=(self.mode == 'unupgraded'), access_hash=user.get('access_hash'))
            except FloodWaitError as e:
                log.warning('FloodWait checking user %s: %ss', user['user_id'], e.seconds)
                await asyncio.sleep(e.seconds + random.uniform(1, 3))
            except (ConnectionError, TimeoutError) as e:
                log.warning('Network error for %s (%s/%s): %s', user['user_id'], attempt, MAX_RETRIES, e)
                await self._recover_connection()
                await asyncio.sleep(min(30, 2 * attempt))
            except Exception as e:
                msg = str(e)
                if 'Could not find the input entity' in msg or 'InputPeerUser' in msg:
                    log.warning('Gift lookup skipped for %s: missing Telegram access_hash/entity', user['user_id'])
                    return []
                if attempt == MAX_RETRIES:
                    log.warning('Gift lookup failed for %s after %s attempts: %s', user['user_id'], attempt, e)
                    return []
                await asyncio.sleep(min(8, 1.5 * attempt))
        return []

    async def _recover_connection(self):
        try:
            if not self.client.is_connected():
                await self.client.connect()
        except Exception:
            log.exception('Telegram reconnect failed; retrying later')

    def _filter_mode(self, gifts):
        nft_gifts = [g for g in gifts if g.get('is_nft_candidate')]
        if self.mode == 'all':
            return nft_gifts
        return [g for g in nft_gifts if g.get('can_upgrade') and not g.get('is_collectible')]

    def stop(self):
        self.stop_flag = True

    def get_stats(self):
        return self.stats.copy()
