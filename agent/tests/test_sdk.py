"""Настоящий SDK и SQLite с локальным HTTP-сервером вместо платного провайдера."""
import json
import io
import os
import sqlite3
import tempfile
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from django.core.management import call_command

from agent import service


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.auth_headers.append(self.headers.get("Authorization"))
        self.server.requests.append((self.path, payload))
        if self.server.reject_forced_thinking_tools and payload.get("tool_choice") == "required":
            status, body = 400, {"error": {"message": "Thinking mode does not support this tool_choice", "type": "invalid_request_error"}}
        else:
            status, body = self.server.replies.popleft()
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def completion(message):
    return {"id": "test", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}]}


class SDKTests(SimpleTestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.replies = deque()
        self.server.requests = []
        self.server.auth_headers = []
        self.server.reject_forced_thinking_tools = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict(os.environ, {
            **{name: "" for name in os.environ if name.startswith("LLM_API_KEY_")},
            "LLM_PROVIDER": "openai-compatible",
            "LLM_API_KEY": "fake-key-for-sdk-test",
            "LLM_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/openai/v1",
            "LLM_MODEL": "test-model",
        })
        self.env.start()
        self.prompt = patch("agent.service.build_system_prompt", return_value="Use run_sql to query deals(deal_date).")
        self.prompt.start()

    def tearDown(self):
        self.prompt.stop()
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_real_sdk_error_is_readable_and_redacted(self):
        self.server.replies.append((401, {"error": {"message": "Invalid API Key fake-key-for-sdk-test", "code": "invalid_api_key"}}))
        result = service.answer_question("Сколько сделок?")
        self.assertEqual(result["error_code"], "invalid_api_key")
        self.assertIn("Invalid API Key", result["error"])
        self.assertNotIn("fake-key-for-sdk-test", result["error"])
        self.assertEqual(len(self.server.requests), 1)

    def test_real_sdk_tool_cycle_queries_sqlite(self):
        self.server.replies.extend([
            (200, completion({"content": None, "tool_calls": [{"id": "sql1", "type": "function", "function": {
                "name": "run_sql", "arguments": json.dumps({"query": "SELECT COUNT(*) FROM deals WHERE deal_date LIKE '2025-%'"}),
            }}]})),
            (200, completion({"content": "В 2025 году — 2 сделки."})),
        ])
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "test.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE deals (deal_date TEXT)")
                conn.executemany("INSERT INTO deals VALUES (?)", [("2025-01-01",), ("2025-12-31",), ("2024-01-01",)])
            with patch("agent.tools.DB_PATH", db):
                result = service.answer_question("Сколько сделок в 2025 году?")
        self.assertNotIn("error_code", result)
        self.assertEqual(result["evidence"][0]["rows"], [[2]])
        self.assertEqual(len(self.server.requests), 2)
        path, payload = self.server.requests[1]
        self.assertEqual(path, "/openai/v1/chat/completions")
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(json.loads(payload["messages"][-1]["content"])["rows"], [[2]])

    def test_rotation_is_per_question_not_per_model_call(self):
        keys = {f"LLM_API_KEY_{i}": f"sdk-private-slot-{i}" for i in range(1,6)}
        for index in range(6):
            self.server.replies.extend([
                (200, completion({"content": None, "tool_calls": [{"id": f"call{index}", "type": "function", "function": {
                    "name": "run_sql", "arguments": '{"query":"SELECT COUNT(*) FROM deals"}',
                }}]})),
                (200, completion({"content": "В реестре 3 сделки."})),
            ])
        with patch.dict(os.environ, keys), patch("agent.tools.dispatch", return_value={"columns": ["cnt"], "rows": [[3]], "row_count": 1}):
            for _ in range(6):
                self.assertNotIn("error_code", service.answer_question("Сколько сделок?"))
        self.assertEqual(self.server.auth_headers, [f"Bearer sdk-private-slot-{index}" for index in [1,1,2,2,3,3,4,4,5,5,1,1]])

    def test_rate_limit_does_not_probe_remaining_keys(self):
        keys = {f"LLM_API_KEY_{i}": f"sdk-quota-slot-{i}" for i in range(1,6)}
        self.server.replies.extend([(429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}})] * 2)
        with patch.dict(os.environ, keys):
            for _ in range(2):
                self.assertEqual(service.answer_question("Сколько сделок?")["error_code"], "rate_limit")
        self.assertEqual(self.server.auth_headers, ["Bearer sdk-quota-slot-1", "Bearer sdk-quota-slot-2"])

    def test_deepseek_tool_cycle_preserves_protocol_field_and_isolates_credentials(self):
        self.server.replies.extend([
            (200, completion({"content": None, "reasoning_content": "test protocol field", "tool_calls": [
                {"id": "deepseek-call", "type": "function", "function": {
                    "name": "run_sql", "arguments": '{"query":"SELECT COUNT(*) FROM deals"}',
                }}]})),
            (200, completion({"content": "1367"})),
        ])
        env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sdk-deepseek-private-value",
               "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
               "DEEPSEEK_MODEL": "deepseek-v4-pro", "LLM_API_KEY_1": "old-groq-test-value"}
        with patch.dict(os.environ, env), patch("agent.tools.dispatch", return_value={"columns": ["cnt"], "rows": [[1367]], "row_count": 1}):
            result = service.answer_question("Сколько экспортных сделок в 2025 году?")
        self.assertNotIn("error_code", result)
        self.assertEqual(result["answer"], "1 367")
        self.assertEqual(self.server.auth_headers, ["Bearer sdk-deepseek-private-value"] * 2)
        for path, payload in self.server.requests:
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(payload["model"], "deepseek-v4-pro")
            self.assertEqual(payload["tool_choice"], "auto")
        messages = self.server.requests[1][1]["messages"]
        self.assertEqual(messages[-2]["reasoning_content"], "test protocol field")
        self.assertNotIn("test protocol field", str(result))

    def test_deepseek_diagnostic_works_with_thinking_mode_that_rejects_required(self):
        self.server.reject_forced_thinking_tools = True
        self.server.replies.extend([
            (200, completion({"content": None, "reasoning_content": "test diagnostic protocol field", "tool_calls": [
                {"id": "check-thinking", "type": "function", "function": {"name": "connection_check", "arguments": "{}"}}]})),
            (200, completion({"content": "OK"})),
        ])
        env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sdk-thinking-test-value",
               "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1", "DEEPSEEK_MODEL": "deepseek-v4-pro"}
        output = io.StringIO()
        with patch.dict(os.environ, env):
            call_command("check_llm", stdout=output)
        self.assertIn("OK: API", output.getvalue())
        self.assertEqual(self.server.requests[0][1]["tool_choice"], "auto")
        self.assertEqual(self.server.requests[1][1]["messages"][-2]["reasoning_content"], "test diagnostic protocol field")
        self.assertEqual(len(self.server.requests), 2)
        self.assertNotIn("sdk-thinking-test-value", output.getvalue())
