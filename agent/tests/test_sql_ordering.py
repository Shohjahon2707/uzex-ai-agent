import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from agent import tools
from agent.service import _evidence_item
from agent.sql_ordering import with_decimal_collation


class ExactMoneyOrderingTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "registry.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE deals (region TEXT, amount NUMERIC)")
            conn.executemany("INSERT INTO deals VALUES (?,?)", [("Samarqand", "65.70"), ("Surxondaryo", "629.85"), ("Jizzax", "575.04"), ("Sirdaryo", "460.91")])
        database = patch("agent.tools.DB_PATH", path)
        database.start()
        self.addCleanup(database.stop)

    def assert_top(self, query):
        result = tools.run_sql(query)
        self.assertNotIn("error", result)
        self.assertEqual([row[0] for row in result["rows"]], ["Surxondaryo", "Jizzax", "Sirdaryo"])
        return result

    def test_quoted_alias_sort_happens_before_limit(self):
        self.assert_top('SELECT region,sum_money(amount) AS "Сумма, сум" FROM deals GROUP BY region ORDER BY "Сумма, сум" DESC LIMIT 3')

    def test_ordinal_ordering(self):
        self.assert_top("SELECT region,sum_money(amount) AS total FROM deals GROUP BY region ORDER BY 2 DESC LIMIT 3")

    def test_nested_coalesce_and_cte_inherit_numeric_collation(self):
        self.assert_top("WITH totals AS (SELECT region,COALESCE(sum_money(amount),'0') AS total FROM deals GROUP BY region) SELECT region,total FROM totals ORDER BY total DESC LIMIT 3")

    def test_direct_aggregate_in_order_by(self):
        self.assert_top("SELECT region FROM deals GROUP BY region ORDER BY sum_money(amount) DESC LIMIT 3")

    def test_existing_collation_is_idempotent(self):
        query = "SELECT region,sum_money(amount) COLLATE decimal AS total FROM deals GROUP BY region ORDER BY total DESC LIMIT 3"
        self.assertEqual(with_decimal_collation(query), query)
        self.assert_top(query)

    def test_explicit_binary_on_aggregate_is_corrected_without_losing_alias(self):
        self.assert_top("SELECT region,sum_money(amount) COLLATE binary AS total FROM deals GROUP BY region ORDER BY total DESC LIMIT 3")

    def test_literals_and_comments_are_not_functions(self):
        query = "SELECT 'sum_money(amount)' AS example /* sum_uzs(a,r,u) */"
        self.assertEqual(with_decimal_collation(query), query)

    def test_evidence_contains_the_query_actually_executed(self):
        query = "SELECT region,sum_money(amount) AS total FROM deals GROUP BY region ORDER BY total DESC LIMIT 3"
        result = self.assert_top(query)
        evidence = _evidence_item("run_sql", {"query": query}, result)
        self.assertIn("COLLATE decimal", evidence["sql"])
        self.assertEqual(evidence["sql"], result["executed_sql"])
