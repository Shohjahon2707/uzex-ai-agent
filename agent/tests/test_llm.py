import io
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from agent import llm, service


def reply(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=tool_calls,
    ))])


class ProviderError(Exception):
    def __init__(self, status, body):
        super().__init__(str(body))
        self.status_code = status
        self.body = body


class ConfigTests(SimpleTestCase):
    def test_missing_key_is_actionable(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(llm.ConfigurationError, "LLM_API_KEY"):
                llm.get_config()

    def test_groq_variable_is_supported(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "fake-test-key"}, clear=True):
            config = llm.get_config()
            self.assertEqual(config.base_url, "https://api.groq.com/openai/v1")
            self.assertEqual(config.model, "openai/gpt-oss-20b")
            self.assertNotIn("fake-test-key", repr(config))

    def test_rejects_full_completion_path(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "fake", "LLM_BASE_URL": "https://example.com/v1/chat/completions"}, clear=True):
            with self.assertRaisesRegex(llm.ConfigurationError, "SDK"):
                llm.get_config()

    def test_provider_details_and_key_redaction(self):
        key = "fake-test-private-value"
        with patch.dict(os.environ, {"LLM_API_KEY": key}, clear=True):
            error = llm.describe_error(ProviderError(401, {"error": {"message": f"Invalid API Key: {key}"}}))
        self.assertEqual(error["code"], "invalid_api_key")
        self.assertIn("401", error["message"])
        self.assertIn("Invalid API Key", error["detail"])
        self.assertNotIn(key, error["detail"])

    def test_provider_status_categories(self):
        for status, code in [(400, "invalid_request"), (402, "insufficient_balance"), (403, "access_denied"),
                             (404, "model_not_found"), (429, "rate_limit"), (503, "provider_unavailable")]:
            with self.subTest(status=status):
                self.assertEqual(llm.describe_error(ProviderError(status, "test"))["code"], code)


class AgentTests(SimpleTestCase):
    def test_oversized_results_remain_valid_json(self):
        result = {"columns": ["name"], "rows": [["я" * 16000], ["value"]], "row_count": 2}
        clipped = service._clip(result)
        out = json.loads(clipped)
        self.assertLessEqual(len(clipped), service.TOOL_JSON_LIMIT)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["row_count"], len(out["rows"]))
        self.assertEqual(len(result["rows"]), 2)

    @patch("agent.service.build_system_prompt", return_value="test schema")
    @patch("agent.service.get_client")
    @patch("agent.service._chat", return_value=reply("115763"))
    def test_ungrounded_answers_are_not_shown(self, chat, client, prompt):
        result = service.answer_question("Сколько сделок?")
        self.assertEqual(result["error_code"], "unverified_answer")
        self.assertNotIn("115763", result["answer"])
        client.return_value.close.assert_called_once()

    @patch("agent.service.build_system_prompt", return_value="test schema")
    @patch("agent.service.get_client")
    @patch("agent.service.tools.dispatch", return_value={"columns": ["n"], "rows": [[115763]], "row_count": 1})
    @patch("agent.service._chat")
    def test_sql_result_can_be_used_for_answer(self, chat, dispatch, client, prompt):
        tc = SimpleNamespace(id="call1", function=SimpleNamespace(name="run_sql", arguments='{"query":"SELECT COUNT(*) FROM deals"}'))
        chat.side_effect = [reply(tool_calls=[tc]), reply("115 763 сделки")]
        result = service.answer_question("Сколько сделок?")
        self.assertNotIn("error_code", result)
        self.assertEqual(result["evidence"][0]["rows"], [[115763]])
        self.assertIn("deadline", dispatch.call_args.kwargs)

    def test_exhausted_deadline_does_not_start_api_call(self):
        client = Mock()
        with patch("agent.service.get_config", return_value=SimpleNamespace(model="test")):
            with self.assertRaises(TimeoutError):
                service._chat(client, [], deadline=0)
        client.with_options.assert_not_called()

    @patch("agent.service.build_system_prompt", return_value="test schema")
    @patch("agent.service.get_client")
    @patch("agent.service._chat", side_effect=ProviderError(401, "Invalid API Key"))
    def test_api_failure_is_actionable(self, chat, client, prompt):
        result = service.answer_question("Сколько сделок?")
        self.assertEqual(result["error_code"], "invalid_api_key")
        self.assertIn("401", result["answer"])
        client.return_value.close.assert_called_once()


class DiagnosticCommandTests(SimpleTestCase):
    @patch("agent.management.commands.check_llm.get_config", return_value=llm.LLMConfig("fake-secret", "https://example.com/v1", "test-model"))
    @patch("agent.management.commands.check_llm.create_client")
    def test_lists_models_without_revealing_key(self, factory, config):
        factory.return_value.__enter__.return_value.models.list.return_value = [SimpleNamespace(id="available-model")]
        out = io.StringIO()
        call_command("check_llm", "--list-models", stdout=out)
        self.assertIn("available-model", out.getvalue())
        self.assertNotIn("fake-secret", out.getvalue())

    @patch("agent.management.commands.check_llm.get_config", return_value=llm.LLMConfig("fake", "https://example.com/v1", "test-model"))
    @patch("agent.management.commands.check_llm.create_client")
    def test_checks_tool_call_round_trip(self, factory, config):
        tc = SimpleNamespace(id="check1", function=SimpleNamespace(name="connection_check", arguments="{}"))
        client = factory.return_value.__enter__.return_value
        client.chat.completions.create.side_effect = [reply(tool_calls=[tc]), reply("OK")]
        out = io.StringIO()
        call_command("check_llm", stdout=out)
        self.assertIn("OK:", out.getvalue())
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(sent[-1]["role"], "tool")

    @patch("agent.management.commands.check_llm.get_config", side_effect=llm.ConfigurationError("Нет ключа"))
    def test_config_failure_is_command_error(self, config):
        with self.assertRaisesRegex(CommandError, "Нет ключа"):
            call_command("check_llm", stdout=io.StringIO())
