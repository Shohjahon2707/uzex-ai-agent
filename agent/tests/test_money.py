import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from agent import tools


class MoneyTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "money.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE items (amount NUMERIC, rate NUMERIC, is_usd INTEGER)")
            conn.executemany("INSERT INTO items VALUES (?,?,?)", [(0.1, 10000.01, 1), (0.2, 10000.02, 1), (0.3, None, 0)])
        self.patch = patch("agent.tools.DB_PATH", self.db)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_exact_original_currency_sum(self):
        self.assertEqual(tools.run_sql("SELECT sum_money(amount) FROM items")["rows"], [["0.6"]])

    def test_rate_is_applied_per_deal(self):
        self.assertEqual(tools.run_sql("SELECT sum_uzs(amount,rate,is_usd) FROM items")["rows"], [["3000.305"]])

    def test_missing_usd_rate_is_not_silently_skipped(self):
        self.assertEqual(tools.run_sql("SELECT sum_uzs(amount,NULL,1) FROM items")["rows"], [[None]])

    def test_decimal_order_is_numeric(self):
        self.assertEqual(tools.run_sql("SELECT '9.9' AS amount UNION ALL SELECT '100.01' ORDER BY amount COLLATE decimal DESC")["rows"], [["100.01"], ["9.9"]])
