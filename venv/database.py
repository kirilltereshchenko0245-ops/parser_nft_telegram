import json
import sqlite3
from contextlib import contextmanager


class Database:
    """SQLite-обёртка. Совместима со схемой прошлых версий (миграции — в _init)."""

    def __init__(self, path='bot_database.db'):
        self.path = path
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

            c.execute('''CREATE TABLE IF NOT EXISTS achievements(
                user_id INTEGER, badge TEXT, unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, badge))''')
            c.execute('''CREATE TABLE IF NOT EXISTS collection_progress(
                user_id INTEGER, model TEXT, found_count INTEGER DEFAULT 1,
                PRIMARY KEY(user_id, model))''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_achiev_user ON achievements(user_id)')

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
            c.execute('UPDATE users SET is_banned=? WHERE user_id=?', (int(value), uid))

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
                model = g.get('model') or g.get('giftName') or ''
                if model:
                    c.execute('''INSERT INTO collection_progress(user_id, model, found_count)
                        VALUES(?,?,1) ON CONFLICT(user_id, model)
                        DO UPDATE SET found_count=found_count+1''', (uid, model))

    _GIFT_SELECT = '''SELECT f.*, COALESCE(NULLIF(f.gift_name,''), s.model) AS collection
                      FROM found_gifts f JOIN search_history s ON s.id = f.search_id'''

    def get_gifts(self, sid):
        return self._all(f'{self._GIFT_SELECT} WHERE f.search_id=? ORDER BY f.gift_number', (sid,))

    def count_gifts(self, sid):
        return (self._one('SELECT COUNT(*) n FROM found_gifts WHERE search_id=?', (sid,)) or {}).get('n', 0)

    def gifts_page(self, sid, offset, limit):
        return self._all(f'{self._GIFT_SELECT} WHERE f.search_id=? ORDER BY f.gift_number LIMIT ? OFFSET ?',
                         (sid, limit, offset))

    # ───────────── коллекция / прогресс ─────────────
    def ensure_collection(self, uid, model):
        with self._db() as c:
            c.execute('''INSERT INTO collection_progress(user_id, model, found_count)
                VALUES(?,?,1) ON CONFLICT(user_id, model)
                DO UPDATE SET found_count=found_count+1''', (uid, model))

    def collection_stats(self, uid):
        """Вернёт {модель: кол-во} для всех находок пользователя."""
        rows = self._all('SELECT model, SUM(found_count) cnt FROM collection_progress '
                         'WHERE user_id=? GROUP BY model ORDER BY cnt DESC', (uid,))
        return {r['model']: r['cnt'] for r in rows}

    def collection_count_by_rarity(self, uid):
        """Вернёт {rarity: найденные_уникальные_модели}."""
        from peek_api import RARITY_MAP
        coll = self.collection_stats(uid)
        by_rarity = {'legendary': 0, 'rare': 0, 'common': 0, 'mass': 0}
        for m, cnt in coll.items():
            r = RARITY_MAP.get(m, 'common')
            by_rarity[r] += 1
        return by_rarity

    # ───────────── ачивки ─────────────
    ACHIEVEMENTS = [
        ('first_find',     '🎯 Первая находка',        'Найти первый подарок'),
        ('five_finds',     '🔥 Пять находок',           'Найти 5 подарков'),
        ('twenty_finds',   '💫 Двадцать находок',       'Найти 20 подарков'),
        ('fifty_finds',    '⭐ Пятьдесят находок',       'Найти 50 подарков'),
        ('hundred_finds',  '🏆 Сто находок',            'Найти 100 подарков'),
        ('legendary_find', '💎 Легендарная находка',    'Найти подарок Legendary'),
        ('rare_find',      '🔮 Редкая коллекция',        'Найти 3 разных Rare'),
        ('radar_scan',     '📡 Gift Radar',             'Использовать Gift Radar'),
        ('big_range',      '📏 Крупный улов',           'Проверить 1000+ ID за один поиск'),
    ]

    def check_achievements(self, uid):
        """Проверить и выдать новые ачивки. Вернёт список новеньких."""
        u = self.get_user(uid)
        if not u:
            return []
        earned = {r['badge'] for r in self._all('SELECT badge FROM achievements WHERE user_id=?', (uid,))}
        total_found = u.get('total_found', 0)
        total_searches = u.get('total_searches', 0)
        coll = self.collection_stats(uid)
        unique_models = len(coll)
        new_badges = []

        # first_find
        if total_found >= 1 and 'first_find' not in earned:
            self._add_achievement(uid, 'first_find')
            new_badges.append('first_find')
        # five_finds
        if total_found >= 5 and 'five_finds' not in earned:
            self._add_achievement(uid, 'five_finds')
            new_badges.append('five_finds')
        # twenty_finds
        if total_found >= 20 and 'twenty_finds' not in earned:
            self._add_achievement(uid, 'twenty_finds')
            new_badges.append('twenty_finds')
        # fifty_finds
        if total_found >= 50 and 'fifty_finds' not in earned:
            self._add_achievement(uid, 'fifty_finds')
            new_badges.append('fifty_finds')
        # hundred_finds
        if total_found >= 100 and 'hundred_finds' not in earned:
            self._add_achievement(uid, 'hundred_finds')
            new_badges.append('hundred_finds')
        # legendary_find
        if any(RARITY_MAP.get(m) == 'legendary' for m in coll) and 'legendary_find' not in earned:
            self._add_achievement(uid, 'legendary_find')
            new_badges.append('legendary_find')
        # rare_find (3+ different rare models)
        rare_models = sum(1 for m in coll if RARITY_MAP.get(m) == 'rare')
        if rare_models >= 3 and 'rare_find' not in earned:
            self._add_achievement(uid, 'rare_find')
            new_badges.append('rare_find')
        # big_range
        big = self._one('SELECT 1 x FROM search_history WHERE user_id=? AND '
                        '(end_id - start_id) >= 999 LIMIT 1', (uid,))
        if big and 'big_range' not in earned:
            self._add_achievement(uid, 'big_range')
            new_badges.append('big_range')

        return new_badges

    def _add_achievement(self, uid, badge):
        with self._db() as c:
            c.execute('INSERT OR IGNORE INTO achievements(user_id, badge) VALUES(?,?)', (uid, badge))

    def get_achievements(self, uid):
        rows = self._all('SELECT badge, unlocked_at FROM achievements WHERE user_id=? '
                         'ORDER BY unlocked_at DESC', (uid,))
        lookup = dict(self.ACHIEVEMENTS)
        return [{'id': r['badge'], 'icon': lookup[r['badge']][0] if r['badge'] in lookup else '🏅',
                 'title': lookup[r['badge']][1] if r['badge'] in lookup else r['badge'],
                 'desc': lookup[r['badge']][2] if r['badge'] in lookup else '',
                 'unlocked': r['unlocked_at']}
                for r in rows]

    def unlock_achievement(self, uid, badge):
        self._add_achievement(uid, badge)

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
