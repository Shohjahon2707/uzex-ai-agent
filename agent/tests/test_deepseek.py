import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from agent.configuration import configure_deepseek
from agent.llm import ConfigurationError, get_config, next_config, redact


class DeepSeekConfigTests(SimpleTestCase):
    def test_provider_does_not_use_old_groq_keys_url_or_model(self):
        env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "test-deepseek-private-value",
               "LLM_API_KEY_1": "test-old-groq-value", "LLM_BASE_URL": "https://api.groq.com/openai/v1",
               "LLM_MODEL": "openai/gpt-oss-20b"}
        with patch.dict(os.environ, env, clear=True):
            config = get_config()
            self.assertEqual(config.key_name, "DEEPSEEK_API_KEY")
            self.assertEqual(config.api_key, env["DEEPSEEK_API_KEY"])
            self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(config.model, "deepseek-v4-pro")
            self.assertEqual({next_config().key_name for _ in range(5)}, {"DEEPSEEK_API_KEY"})
            self.assertNotIn(env["DEEPSEEK_API_KEY"], repr(config))

    def test_missing_deepseek_key_does_not_fall_back_to_groq(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepseek", "LLM_API_KEY_1": "old-groq-value"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "DEEPSEEK_API_KEY"):
                get_config()

    def test_named_provider_can_be_switched_back(self):
        env = {"LLM_PROVIDER": "openai-compatible", "DEEPSEEK_API_KEY": "old-deepseek-value",
               "LLM_API_KEY": "new-generic-value", "LLM_BASE_URL": "https://api.groq.com/openai/v1"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(get_config().key_name, "LLM_API_KEY")
            self.assertEqual(get_config().base_url, env["LLM_BASE_URL"])

    def test_deepseek_key_is_redacted(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-opaque-provider-secret"}, clear=True):
            self.assertEqual(redact("invalid: test-opaque-provider-secret"), "invalid: [REDACTED]")

    def test_configuration_preserves_other_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("DEBUG=0\nLLM_API_KEY_1=old-test-value\nDEEPSEEK_MODEL=old-model\n")
            configure_deepseek(path, "new-test-private-value")
            first = path.read_text()
            configure_deepseek(path, "new-test-private-value")
            self.assertEqual(first, path.read_text())
            self.assertIn("DEBUG=0\n", first)
            self.assertIn("LLM_API_KEY_1=old-test-value\n", first)
            self.assertIn("LLM_PROVIDER=deepseek\n", first)
            self.assertIn("DEEPSEEK_MODEL=deepseek-v4-pro\n", first)
            self.assertNotIn("old-model", first)
            self.assertEqual(list(Path(folder).glob(".env.*.tmp")), [])

    def test_invalid_input_does_not_overwrite_env(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("DEBUG=0\n")
            with self.assertRaises(ValueError):
                configure_deepseek(path, "invalid\nDEBUG=1")
            self.assertEqual(path.read_text(), "DEBUG=0\n")

    def test_setup_command_keeps_key_out_of_output(self):
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            with override_settings(BASE_DIR=Path(folder)), patch("agent.management.commands.configure_deepseek.getpass", return_value="private-command-test-value"):
                call_command("configure_deepseek", stdout=out)
            self.assertIn("deepseek-v4-pro", out.getvalue())
            self.assertNotIn("private-command-test-value", out.getvalue())
            self.assertIn("DEEPSEEK_API_KEY=private-command-test-value", (Path(folder) / ".env").read_text())
