from django.test import SimpleTestCase

from agent.presentation import format_answer
from agent.service import _evidence_item


class PresentationTests(SimpleTestCase):
    def test_only_grounded_measurements_are_grouped(self):
        evidence = [{"tool": "run_sql", "columns": ["cnt", "seller_inn", "deal_id"], "rows": [[115763, 123456789, 100001]]}]
        text = "За 2025 год — **115763 сделки**. ИНН 123456789, номер 100001. Неизвестное 987654."
        result = format_answer(text, evidence)
        self.assertIn("**115 763 сделки**", result)
        self.assertIn("ИНН 123456789", result)
        self.assertIn("номер 100001", result)
        self.assertIn("Неизвестное 987654", result)

    def test_money_strings_keep_all_digits(self):
        evidence = [{"tool": "run_sql", "columns": ["Сумма, сум"], "rows": [["220477157554431.7891"]]}]
        self.assertEqual(format_answer("Итого 220477157554431.7891 сум.", evidence), "Итого 220 477 157 554 431.7891 сум.")

    def test_sentence_punctuation_and_years(self):
        evidence = [{"tool": "run_sql", "columns": ["cnt", "year"], "rows": [[115763, 2025]]}]
        self.assertEqual(format_answer("2025: всего 115763.", evidence), "2025: всего 115 763.")

    def test_evidence_does_not_silently_drop_rows(self):
        rows = [[i] for i in range(30)]
        item = _evidence_item("run_sql", {"query": "SELECT n FROM t"}, {"rows": rows, "columns": ["n"], "row_count": 30})
        self.assertEqual(item["rows"], rows)
