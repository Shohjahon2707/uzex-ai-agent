from django.test import SimpleTestCase

from scripts.golden import contains_value


class GoldenCheckerTests(SimpleTestCase):
    def test_whole_numbers_not_substrings(self):
        self.assertFalse(contains_value("120000 сделок за 2025 год", "120"))
        self.assertFalse(contains_value("2025 год", "0"))
        self.assertTrue(contains_value("За 2025 год — 115 763 сделки.", "115763"))

    def test_money_separators_and_codes(self):
        self.assertTrue(contains_value("220 524 617,85 USD", "220524617.85"))
        self.assertTrue(contains_value("220,524,617.85 USD", "220524617.85"))
        self.assertTrue(contains_value("Брокер BK111 — 32 422 сделки", "BK111"))
