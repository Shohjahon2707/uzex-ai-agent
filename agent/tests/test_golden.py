from django.test import SimpleTestCase
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import golden
from scripts.golden import contains_value


class GoldenCheckerTests(SimpleTestCase):
    def test_rate_limit_stops_the_run_without_accuracy_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "questions.csv"
            path.write_text("question,expected,mode\nQuestion one,1,contains\nQuestion two,2,contains\n")
            output = io.StringIO()
            with patch("sys.argv", ["golden.py", str(path), "http://localhost"]), patch("sys.stdout", output), patch("scripts.golden.ask", return_value={"error_code": "rate_limit", "answer": "quota"}) as ask:
                self.assertEqual(golden.main(), 2)
            ask.assert_called_once()
            self.assertIn("BLOCKED", output.getvalue())
            self.assertNotIn("FAIL", output.getvalue())

    def test_whole_numbers_not_substrings(self):
        self.assertFalse(contains_value("120000 сделок за 2025 год", "120"))
        self.assertFalse(contains_value("2025 год", "0"))
        self.assertTrue(contains_value("За 2025 год — 115 763 сделки.", "115763"))

    def test_money_separators_and_codes(self):
        self.assertTrue(contains_value("220 524 617,85 USD", "220524617.85"))
        self.assertTrue(contains_value("220,524,617.85 USD", "220524617.85"))
        self.assertTrue(contains_value("Брокер BK111 — 32 422 сделки", "BK111"))
