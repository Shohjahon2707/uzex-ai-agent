"""Настоящий SDK и SQLite с локальным HTTP-сервером вместо платного провайдера."""
import json
import os
import sqlite3
import tempfile
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from agent import service


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, payload))
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
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict(os.environ, {
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
