import json
import os
import sqlite3
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
                user_id INTEGER, gift_number INTEGER, model TEXT, username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, gift_number, model))''')
            
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

    # ───────────── пользователи ─────────────
    def upsert_user(self, uid, username=None, first_name=None):
        with self._db() as c:
            c.execute('''INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                first_name=excluded.first_name, last_activity=CURRENT_TIMESTAMP''',
                      (uid, username, first_name))

    def get_user(self, uid):
        return self._one('SELECT * FROM users WHERE user_id=?', (uid,))

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

    def parser_admin_only(self):
        return self.get_parser_setting('admin_only', '0') == '1'

    def set_parser_admin_only(self, value):
        self.set_parser_setting('admin_only', '1' if value else '0')

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
