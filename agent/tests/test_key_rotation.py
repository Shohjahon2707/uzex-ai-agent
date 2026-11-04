import io
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from agent import llm


class KeyRotationTests(SimpleTestCase):
    def setUp(self):
        self.keys = {f"LLM_API_KEY_{i}": f"private-test-value-{i}" for i in range(1, 6)}
        self.env = patch.dict(os.environ, {**self.keys, "LLM_BASE_URL": "https://api.groq.com/openai/v1"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        with llm._rotation_lock:
            llm._rotation_signature = ()
            llm._rotation_position = 0

    def test_five_keys_wrap_in_numeric_order(self):
        self.assertEqual([llm.next_config().key_name for _ in range(12)],
                         [f"LLM_API_KEY_{i}" for i in [1,2,3,4,5,1,2,3,4,5,1,2]])

    def test_reading_config_does_not_advance_rotation(self):
        llm.get_config()
        llm.get_config()
        self.assertEqual(llm.next_config().key_name, "LLM_API_KEY_1")

    def test_numbered_keys_take_precedence_and_duplicates_are_removed(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "old-test-value", "LLM_API_KEY_2": self.keys["LLM_API_KEY_1"]}):
            self.assertEqual([name for name, _ in llm.key_pool()], ["LLM_API_KEY_1", "LLM_API_KEY_3", "LLM_API_KEY_4", "LLM_API_KEY_5"])

    def test_parallel_questions_get_balanced_slots(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            labels = list(executor.map(lambda _: llm.next_config().key_name, range(100)))
        self.assertEqual(Counter(labels), {f"LLM_API_KEY_{i}": 20 for i in range(1,6)})

    def test_all_configured_keys_are_redacted(self):
        cleaned = llm.redact(" ".join(self.keys.values()))
        self.assertNotIn("private-test-value", cleaned)
        self.assertEqual(cleaned.count("[REDACTED]"), 5)

    def test_show_rotation_does_not_make_api_calls(self):
        stdout = io.StringIO()
        with patch("agent.management.commands.check_llm.create_client") as factory:
            call_command("check_llm", "--show-rotation", stdout=stdout)
        factory.assert_not_called()
        self.assertIn("LLM_API_KEY_5 → LLM_API_KEY_1", stdout.getvalue())
        self.assertNotIn("private-test-value", stdout.getvalue())
        self.assertEqual(llm.next_config().key_name, "LLM_API_KEY_1")

    def test_check_command_can_select_a_specific_key(self):
        stdout = io.StringIO()
        with patch("agent.management.commands.check_llm.create_client") as factory:
            factory.return_value.__enter__.return_value.models.list.return_value = []
            call_command("check_llm", "--key", "3", "--list-models", stdout=stdout)
        self.assertEqual(factory.call_args.kwargs["config"].key_name, "LLM_API_KEY_3")
        self.assertNotIn("private-test-value", stdout.getvalue())

    def test_additional_keys_sort_numerically(self):
        with patch.dict(os.environ, {"LLM_API_KEY_10": "private-test-value-10", "LLM_API_KEY_6": "private-test-value-6"}):
            self.assertEqual([name for name, _ in llm.key_pool()][-2:], ["LLM_API_KEY_6", "LLM_API_KEY_10"])
