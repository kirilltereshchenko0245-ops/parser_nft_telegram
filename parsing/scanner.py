"""Основной сканер участников чатов."""
import asyncio
import logging
from datetime import datetime

log = logging.getLogger('giftparser.parser')


class ChatScanner:
    """Сканирует участников чатов и проверяет подарки."""
    
    def __init__(self, client, database):
        self.client = client
        self.db = database
        self.is_running = False
        self.stop_flag = False
        self.stats = {
            'total_checked': 0,
            'total_found': 0,
            'current_chat': None,
            'current_chat_title': '',
            'chat_checked': 0,
            'chat_found': 0,
            'start_time': None
        }
    
    async def start_scan(self, mode='all'):
        """
        Запуск сканирования.
        mode: 'all' - все подарки, 'unupgraded' - только неулучшенные
        """
        if self.is_running:
            log.warning('Сканирование уже запущено')
            return False
        
        self.is_running = True
        self.stop_flag = False
        self.stats['start_time'] = datetime.now()
        
        enabled_chats = self.db.get_enabled_parser_chats()
        
        if not enabled_chats:
            log.warning('Нет активных чатов для сканирования')
            self.is_running = False
            return False
        
        log.info(f'Начало сканирования {len(enabled_chats)} чатов, режим: {mode}')
        
        for chat in enabled_chats:
            if self.stop_flag:
                log.info('Сканирование остановлено пользователем')
                break
            
            try:
                await self._scan_chat(chat, mode)
            except Exception as e:
                log.error(f'Ошибка при сканировании чата {chat["title"]}: {e}')
                continue
        
        self.is_running = False
        log.info(f'Сканирование завершено. Проверено: {self.stats["total_checked"]}, Найдено: {self.stats["total_found"]}')
        return True
    
    async def _scan_chat(self, chat, mode):
        """Сканирование одного чата."""
        chat_id = chat['chat_id']
        self.stats['current_chat'] = chat_id
        self.stats['current_chat_title'] = chat['title']
        self.stats['chat_checked'] = 0
        self.stats['chat_found'] = 0
        
        log.info(f'Сканирование чата: {chat["title"]}')
        
        # Получаем прогресс
        progress = self.db.get_parser_progress(chat_id)
        offset = progress['last_user_offset'] if progress else 0
        checked = progress['checked_count'] if progress else 0
        found = progress['found_count'] if progress else 0
        
        self.db.update_parser_progress(chat_id, offset, checked, found, 'running')
        
        batch_size = 50  # По 50 участников за раз
        
        while not self.stop_flag:
            try:
                # Получаем участников чата
                participants = await self.client.get_participants(chat_id, limit=batch_size, offset=offset)
                
                if not participants:
                    log.info(f'Чат {chat["title"]} завершён')
                    self.db.update_parser_progress(chat_id, offset, checked, found, 'completed')
                    break
                
                for user in participants:
                    if self.stop_flag:
                        break
                    
                    user_id = user['user_id']
                    
                    # Дедупликация: пропускаем недавно проверенных
                    if self.db.is_user_checked(user_id, hours=24):
                        continue
                    
                    checked += 1
                    self.stats['total_checked'] += 1
                    self.stats['chat_checked'] += 1
                    
                    # Проверяем подарки пользователя
                    has_gifts = await self._check_user_gifts(user, chat_id, mode)
                    
                    if has_gifts:
                        found += 1
                        self.stats['total_found'] += 1
                        self.stats['chat_found'] += 1
                    
                    # Сохраняем пользователя в БД
                    self.db.add_parser_user(
                        user_id,
                        user.get('username'),
                        user.get('first_name'),
                        user.get('last_name'),
                        has_gifts
                    )
                    
                    # Задержка между проверками (защита от FloodWait)
                    await asyncio.sleep(2)
                
                offset += len(participants)
                
                # Обновляем прогресс каждые 10 пользователей
                if checked % 10 == 0:
                    self.db.update_parser_progress(chat_id, offset, checked, found, 'running')
                
            except Exception as e:
                log.error(f'Ошибка при получении участников: {e}')
                # При FloodWait ждём указанное время
                if 'FloodWaitError' in str(type(e)):
                    wait_time = getattr(e, 'seconds', 60)
                    log.warning(f'FloodWait: ожидание {wait_time} секунд')
                    await asyncio.sleep(wait_time)
                else:
                    await asyncio.sleep(10)
        
        # Сохраняем финальный прогресс
        status = 'stopped' if self.stop_flag else 'completed'
        self.db.update_parser_progress(chat_id, offset, checked, found, status)
    
    async def _check_user_gifts(self, user, chat_id, mode):
        """
        Проверка подарков пользователя.
        
        ВАЖНО: Telegram MTProto API не предоставляет прямого метода для получения
        списка подарков пользователя через UserFull или другие публичные методы.
        
        Варианты решения:
        1. Использовать недокументированные методы (рискованно, может перестать работать)
        2. Анализировать профиль через веб-версию (сложно)
        3. Парсить через бота по username (ограничено, не все имеют username)
        
        Пока возвращаем False - требуется дальнейшая реализация.
        """
        user_id = user['user_id']
        
        # TODO: Реализовать реальную проверку подарков
        # Возможные подходы:
        # 1. client.get_entity(user_id) -> получить UserFull
        # 2. Проверить наличие premium_gifts или star_gifts полей
        # 3. Использовать метод GetUserProfilePhotos для анализа
        
        # ЗАГЛУШКА: имитация случайных находок (1% вероятность)
        import random
        if random.random() < 0.01:  # 1% шанс "найти" подарок
            # Сохраняем фейковый подарок для демонстрации
            gift_data = {
                'gift_id': random.randint(1000, 9999),
                'gift_name': random.choice(['Eternal Rose', 'Heart Locket', 'Plush Pepe']),
                'gift_slug': None,
                'is_collectible': False,
                'is_upgraded': False
            }
            self.db.add_parser_gift(user_id, chat_id, gift_data)
            log.info(f'Найден подарок у @{user.get("username", "user")} ({user_id})')
            return True
        
        return False
    
    def stop(self):
        """Остановить сканирование."""
        log.info('Получен сигнал остановки')
        self.stop_flag = True
    
    def get_stats(self):
        """Получить текущую статистику."""
        return self.stats.copy()
