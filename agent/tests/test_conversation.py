from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from agent import service
from agent.conversation import needs_history, relevant_history
from agent.tests.test_llm import reply


class ConversationTests(SimpleTestCase):
    def test_standalone_questions_reset_context(self):
        for question in ["Сколько экспортных сделок было в 2025 году? Ответь одним числом.",
                         "2025-yilda nechta eksport bitimi bor?", "Сколько сделок у брокера BK001?"]:
            self.assertFalse(needs_history(question), question)

    def test_explicit_followups_keep_context(self):
        for question in ["А за 2024 год?", "Сколько из них оплачено?", "Сколько из\u00a0них аннулировано?", "Ulardan nechtasi bajarilgan?", "2024-yildachi?"]:
            self.assertTrue(needs_history(question), question)

    def test_old_status_filter_is_not_sent_for_new_question(self):
        captured = []
        def chat(client, messages, **kwargs):
            captured.append(deepcopy(messages))
            return reply("Нужно проверить данные")
        old = [{"role": "user", "content": "Только статус Исполнен"}, {"role": "assistant", "content": "1187"}]
        with patch("agent.service.build_system_prompt", return_value="schema"), patch("agent.service.get_client", return_value=Mock()), patch("agent.service._chat", side_effect=chat):
            service.answer_question("Сколько экспортных сделок было в 2025 году?", old)
        self.assertEqual(len(captured[0]), 2)
        self.assertNotIn("Исполнен", str(captured[0]))
        self.assertNotIn("1187", str(captured[0]))

    def test_prior_context_is_sent_for_an_explicit_followup(self):
        captured = []
        def chat(client, messages, **kwargs):
            captured.append(deepcopy(messages))
            return reply("Нужно проверить данные")
        old = [{"role": "user", "content": "Только статус Исполнен"}]
        with patch("agent.service.build_system_prompt", return_value="schema"), patch("agent.service.get_client", return_value=Mock()), patch("agent.service._chat", side_effect=chat):
            service.answer_question("А за 2024 год?", old)
        self.assertIn("Только статус Исполнен", str(captured[0]))

    def test_followup_inherits_latest_question_not_stale_status(self):
        old = [{"role": "user", "content": "Топ экспортных сделок за 2025 со статусом Исполнен"},
               {"role": "assistant", "content": "Старый ответ"},
               {"role": "user", "content": "Сколько экспортных сделок было в 2025 году?"},
               {"role": "assistant", "content": "1367"}]
        selected = relevant_history("Сколько из них аннулировано?", old)
        self.assertEqual(selected, old[2:])
        self.assertNotIn("Исполнен", str(selected))

    def test_missing_followup_context_asks_instead_of_counting_entire_database(self):
        with patch("agent.service.get_client") as client:
            result = service.answer_question("Сколько из них аннулировано?")
        client.assert_not_called()
        self.assertTrue(result["needs_clarification"])
        self.assertEqual(result["evidence"], [])
