from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


class AskTests(SimpleTestCase):
    def test_invalid_payloads_are_rejected(self):
        for payload in [[], {"question": 123}, {"question": " "},
                        {"question": "test", "history": "bad"},
                        {"question": "test", "history": [123]}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/ask", payload, content_type="application/json").status_code, 400)

    @override_settings(DEBUG=False)
    @patch("agent.views.service.answer_question")
    def test_api_failure_has_status_and_no_production_details(self, answer):
        answer.return_value = {"answer": "Неверный ключ (401)", "error_code": "invalid_api_key", "error": "private diagnostics"}
        response = self.client.post("/api/ask", {"question": "test"}, content_type="application/json")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error_code"], "invalid_api_key")
        self.assertNotIn("error", response.json())

    @override_settings(DEBUG=True)
    @patch("agent.views.service.answer_question")
    def test_local_diagnostics_are_visible(self, answer):
        answer.return_value = {"answer": "Неверный ключ (401)", "error_code": "invalid_api_key", "error": "safe details"}
        response = self.client.post("/api/ask", {"question": "test"}, content_type="application/json")
        self.assertEqual(response.json()["error"], "safe details")
