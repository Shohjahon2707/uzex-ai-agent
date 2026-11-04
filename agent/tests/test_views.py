from unittest.mock import patch
import threading

from django.test import SimpleTestCase, override_settings
from agent import views


class AskTests(SimpleTestCase):
    def test_http_wait_is_bounded_even_if_provider_stalls(self):
        release = threading.Event()
        done = threading.Event()

        def stalled(*args):
            release.wait(2)
            done.set()
            return {"answer": "late answer"}

        slots = threading.BoundedSemaphore(1)
        try:
            with patch.object(views, "_answer_slots", slots), patch.object(views, "RESPONSE_TIMEOUT", 0), patch("agent.views.service.answer_question", side_effect=stalled):
                result, status = views._bounded_answer("test", [])
                self.assertEqual(status, 504)
                self.assertEqual(result["error_code"], "timeout")
                self.assertNotIn("late answer", result["answer"])
                busy_result, busy_status = views._bounded_answer("test", [])
                self.assertEqual(busy_status, 503)
                self.assertEqual(busy_result["error_code"], "busy")
        finally:
            release.set()
            done.wait(2)

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
