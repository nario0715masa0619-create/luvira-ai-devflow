import base64
import hashlib
import hmac
import json
import os
import unittest

os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
from main import app


def event(payload):
    return {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}}


class EventTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_accepts_bound_issue_event(self):
        response = self.client.post("/events", json=event({"repository": "nario0715masa0619-create/luvira-ai-devflow", "action": "opened", "issue": 1}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "PENDING_CONTEXT_LOCK")
        self.assertEqual(response.json["execution"]["primary"], "kimi")
        self.assertEqual(response.json["execution"]["fallbacks"], ["qwen", "copilot", "codex", "claude-code"])
        self.assertIn("codex_independent_review", response.json["quality_gates"])
        self.assertIn("claude_code_independent_review", response.json["quality_gates"])
        self.assertEqual(response.json["monitoring_layers"]["governance"], "context lock, policy, credentials and audit integrity")

    def test_blocks_when_runner_order_has_no_supported_runner(self):
        from main import RUNNER_ORDER
        import main
        old_order = main.RUNNER_ORDER
        main.RUNNER_ORDER = ("unknown",)
        try:
            response = self.client.post("/events", json=event({"repository": "nario0715masa0619-create/luvira-ai-devflow", "action": "opened", "issue": 1}))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json["reason"], "no_valid_runner")
        finally:
            main.RUNNER_ORDER = old_order

    def test_blocks_other_repository(self):
        response = self.client.post("/events", json=event({"repository": "other/repository", "action": "opened", "issue": 1}))
        self.assertEqual(response.status_code, 403)

    def test_accepts_signed_github_issue(self):
        payload = {"action": "opened", "repository": {"full_name": "nario0715masa0619-create/luvira-ai-devflow"}, "issue": {"number": 26}}
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        response = self.client.post("/github/webhook", data=raw, content_type="application/json", headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "PENDING_CONTEXT_LOCK")

    def test_blocks_unsigned_github_issue(self):
        response = self.client.post("/github/webhook", json={"action": "opened"}, headers={"X-GitHub-Event": "issues"})
        self.assertEqual(response.status_code, 401)

    def test_accepts_signed_github_ping_without_orchestrating(self):
        raw = json.dumps({"zen": "Keep it logically awesome."}).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        response = self.client.post("/github/webhook", data=raw, content_type="application/json", headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "OK", "event": "ping"})
