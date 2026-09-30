import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict
import os

class Database:
    def __init__(self, db_path: str = "bot_database.db"):
        self.db_path = db_path
        self.stop_parsing_flags = {}  # {user_id: True/False}
        self.init_db()
    
    def get_connection(self):
        """Создаёт новое подключение к БД"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Для доступа по имени колонки
        return conn
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                is_banned BOOLEAN DEFAULT 0,
                is_premium BOOLEAN DEFAULT 0,
                is_nonupgraded_premium BOOLEAN DEFAULT 0,
                trial_searches_left INTEGER DEFAULT 25,
                nonupgraded_searches_left INTEGER DEFAULT 3,
                total_searches INTEGER DEFAULT 0,
                total_found INTEGER DEFAULT 0,
                registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица истории поисков
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                model TEXT,
                start_id INTEGER,
                end_id INTEGER,
                last_checked_id INTEGER,
                found_count INTEGER DEFAULT 0,
                checked_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'in_progress',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        
        # Таблица найденных подарков
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS found_gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id INTEGER,
                user_id INTEGER,
                gift_number INTEGER,
                model TEXT,
                pattern TEXT,
                backdrop TEXT,
                owner TEXT,
                username TEXT,
                price REAL,
                found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (search_id) REFERENCES search_history (id),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        
        # Таблица настроек бота
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    # === РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ===
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получить пользователя по ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def create_user(self, user_id: int, username: str = None, first_name: str = None):
        """Создать нового пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        """, (user_id, username, first_name))
        conn.commit()
        conn.close()
    
    def update_user_activity(self, user_id: int):
        """Обновить время последней активности"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET last_activity = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()
        conn.close()
    
    def ban_user(self, user_id: int):
        """Забанить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    def unban_user(self, user_id: int):
        """Разбанить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    def set_premium(self, user_id: int, is_premium: bool):
        """Установить премиум статус"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET is_premium = ?
            WHERE user_id = ?
        """, (1 if is_premium else 0, user_id))
        conn.commit()
        conn.close()
    
    def use_trial_search(self, user_id: int) -> bool:
        """Использовать один пробный поиск. Возвращает True если успешно"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        if user['is_premium']:
            return True
        
        if user['trial_searches_left'] <= 0:
            return False
        
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET trial_searches_left = trial_searches_left - 1,
                total_searches = total_searches + 1
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()
        conn.close()
        return True
    
    def increment_user_stats(self, user_id: int, found_count: int = 0):
        """Увеличить статистику пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET total_found = total_found + ?
            WHERE user_id = ?
        """, (found_count, user_id))
        conn.commit()
        conn.close()
    
    # === ИСТОРИЯ ПОИСКОВ ===
    
    def create_search(self, user_id: int, model: str, start_id: int, end_id: int) -> int:
        """Создать новый поиск. Возвращает ID поиска"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO search_history (user_id, model, start_id, end_id, last_checked_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, model, start_id, end_id, start_id - 1))
        search_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return search_id
    
    def update_search_progress(self, search_id: int, last_checked_id: int, checked_count: int, found_count: int):
        """Обновить прогресс поиска"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE search_history
            SET last_checked_id = ?,
                checked_count = ?,
                found_count = ?
            WHERE id = ?
        """, (last_checked_id, checked_count, found_count, search_id))
        conn.commit()
        conn.close()
    
    def complete_search(self, search_id: int):
        """Завершить поиск"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE search_history
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (search_id,))
        conn.commit()
        conn.close()
    
    def get_last_search(self, user_id: int) -> Optional[Dict]:
        """Получить последний незавершённый поиск"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM search_history
            WHERE user_id = ? AND status = 'in_progress'
            ORDER BY started_at DESC
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_user_searches(self, user_id: int, limit: int = 10, offset: int = 0) -> List[Dict]:
        """Получить историю поисков пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM search_history
            WHERE user_id = ?
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
        """, (user_id, limit, offset))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def count_user_searches(self, user_id: int) -> int:
        """Посчитать количество поисков пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM search_history WHERE user_id = ?", (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    # === НАЙДЕННЫЕ ПОДАРКИ ===
    
    def save_found_gift(self, search_id: int, user_id: int, gift_data: Dict):
        """Сохранить найденный подарок"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO found_gifts 
            (search_id, user_id, gift_number, model, pattern, backdrop, owner, username, price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            search_id, user_id,
            gift_data.get('giftNumber'),
            gift_data.get('model'),
            gift_data.get('pattern'),
            gift_data.get('backdrop'),
            gift_data.get('owner'),
            gift_data.get('username', '').replace('t.me/', '').lstrip('@'),
            gift_data.get('market', {}).get('price') if gift_data.get('market') else None
        ))
        conn.commit()
        conn.close()
    
    def get_search_gifts(self, search_id: int) -> List[Dict]:
        """Получить все подарки найденные в поиске"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM found_gifts
            WHERE search_id = ?
            ORDER BY found_at DESC
        """, (search_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # === НАСТРОЙКИ БОТА ===
    
    def get_setting(self, key: str, default: str = None) -> str:
        """Получить настройку бота"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    
    def set_setting(self, key: str, value: str):
        """Установить настройку бота"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO bot_settings (key, value)
            VALUES (?, ?)
        """, (key, value))
        conn.commit()
        conn.close()
    
    # === СТАТИСТИКА ===
    
    def get_total_stats(self) -> Dict:
        """Получить общую статистику бота"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")
        premium_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM search_history")
        total_searches = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM found_gifts")
        total_gifts_found = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'premium_users': premium_users,
            'total_searches': total_searches,
            'total_gifts_found': total_gifts_found
        }
    
    def set_stop_flag(self, user_id: int, stop: bool = True):
        """Установить флаг остановки парсинга"""
        self.stop_parsing_flags[user_id] = stop
    
    def should_stop_parsing(self, user_id: int) -> bool:
        """Проверить нужно ли остановить парсинг"""
        return self.stop_parsing_flags.get(user_id, False)
    
    def clear_stop_flag(self, user_id: int):
        """Очистить флаг остановки"""
        self.stop_parsing_flags.pop(user_id, None)
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        """Добавить нового пользователя (alias для create_user)"""
        self.create_user(user_id, username, first_name)
    
    def update_user_info(self, user_id: int, username: str = None, first_name: str = None):
        """Обновить информацию о пользователе"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET username = ?, first_name = ?, last_activity = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (username, first_name, user_id))
        conn.commit()
        conn.close()
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Получить пользователя по username"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def add_search(self, user_id: int, model: str, start: int, end: int) -> int:
        """Добавить поиск (alias для create_search)"""
        return self.create_search(user_id, model, start, end)
    
    def update_trial_searches(self, user_id: int, new_value: int):
        """Обновить количество пробных поисков"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET trial_searches_left = ?
            WHERE user_id = ?
        """, (new_value, user_id))
        conn.commit()
        conn.close()
    
    def add_found_gift(self, search_id: int, gift_number: int, model: str, 
                      owner_username: str, is_visible: bool, attributes: str):
        """Добавить найденный подарок"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Получаем user_id из search_id
        cursor.execute("SELECT user_id FROM search_history WHERE id = ?", (search_id,))
        row = cursor.fetchone()
        user_id = row[0] if row else None
        
        cursor.execute("""
            INSERT INTO found_gifts 
            (search_id, user_id, gift_number, model, username)
            VALUES (?, ?, ?, ?, ?)
        """, (search_id, user_id, gift_number, model, owner_username))
        
        conn.commit()
        conn.close()
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Получить статистику пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM search_history WHERE user_id = ?", (user_id,))
        total_searches = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM found_gifts WHERE user_id = ?", (user_id,))
        total_gifts_found = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_searches': total_searches,
            'total_gifts_found': total_gifts_found
        }
    
    def update_last_search(self, user_id: int, model: str, current_id: int, end_id: int):
        """Обновить состояние последнего поиска для продолжения"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE search_history
            SET last_checked_id = ?
            WHERE user_id = ? AND model = ? AND end_id = ? AND status = 'in_progress'
            ORDER BY started_at DESC
            LIMIT 1
        """, (current_id, user_id, model, end_id))
        conn.commit()
        conn.close()
    
    def get_last_search_state(self, user_id: int) -> Optional[Dict]:
        """Получить состояние для продолжения последнего поиска"""
        search = self.get_last_search(user_id)
        if not search:
            return None
        
        return {
            'search_id': search['id'],
            'model': search['model'],
            'current_id': search['last_checked_id'] + 1,
            'end_id': search['end_id']
        }
    
    def get_bot_settings(self) -> Dict:
        """Получить настройки бота"""
        access_mode = self.get_setting('access_mode', 'all')
        return {
            'access_mode': access_mode
        }
    
    def update_bot_settings(self, access_mode: str = None):
        """Обновить настройки бота"""
        if access_mode:
            self.set_setting('access_mode', access_mode)
    
    def set_nonupgraded_premium(self, user_id: int, is_premium: bool):
        """Установить подписку на неулучшенные подарки"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET is_nonupgraded_premium = ?
            WHERE user_id = ?
        """, (1 if is_premium else 0, user_id))
        conn.commit()
        conn.close()
    
    def update_nonupgraded_searches(self, user_id: int, new_value: int):
        """Обновить количество поисков неулучшенных подарков"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET nonupgraded_searches_left = ?
            WHERE user_id = ?
        """, (new_value, user_id))
        conn.commit()
        conn.close()
    
    def use_nonupgraded_search(self, user_id: int) -> bool:
        """Использовать один поиск неулучшенных. Возвращает True если успешно"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        if user['is_nonupgraded_premium']:
            return True
        
        if user['nonupgraded_searches_left'] <= 0:
            return False
        
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET nonupgraded_searches_left = nonupgraded_searches_left - 1
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()
        conn.close()
        return True
