"""Offline smoke tests for GiftParser's local components (no Telegram token needed)."""
import os
import tempfile
import unittest

from database import Database
from peek_api import gift_url, is_known_model, normalize_gift, normalize_model_name
from ui import bar, percent


class CoreTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(self.path)
        self.db = Database(self.path)

    def tearDown(self):
        for suffix in ('', '-shm', '-wal'):
            try:
                os.remove(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_user_and_admin_updates(self):
        self.db.upsert_user(1001, 'tester', 'Tester')
        self.assertEqual(self.db.get_user(1001)['trial_searches_left'], 25)
        self.assertTrue(self.db.set_ban(1001, True))
        self.assertTrue(self.db.is_banned(1001))
        self.assertFalse(self.db.set_ban(9999, True))

    def test_search_checkpoint(self):
        self.db.upsert_user(1001)
        sid = self.db.create_search(1001, 'PlushPepe', 1, 3)
        self.db.update_search(sid, 2, 2, 0, 'stopped')
        self.assertEqual(self.db.get_search(sid, 1001)['last_checked_id'], 2)

    def test_model_and_gift_helpers(self):
        self.assertEqual(normalize_model_name('plush pepe'), 'PlushPepe')
        self.assertTrue(is_known_model('PlushPepe'))
        gift = normalize_gift({'giftNumber': 42, 'model': 'PlushPepe'}, 42, 'PlushPepe')
        self.assertEqual(gift_url(gift), 'https://t.me/nft/PlushPepe-42')

    def test_ui_helpers(self):
        self.assertEqual(bar(5, 10, 10), '▰▰▰▰▰▱▱▱▱▱')
        self.assertEqual(percent(1, 4), 25)


if __name__ == '__main__':
    unittest.main()
