import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

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
        self.assertEqual(response.json["execution"]["primary"], "opencode-go")
        self.assertEqual(response.json["execution"]["fallbacks"], ["codex", "claude-code", "copilot"])
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

    def test_opencode_go_readiness_returns_model_count_only(self):
        with patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "test-key"}):
            with patch("main.opencode_go_model_count", return_value=23) as model_count:
                response = self.client.get("/readiness/opencode-go")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "READY", "provider": "opencode-go", "model_count": 23})
        model_count.assert_called_once_with("test-key")

    def test_opencode_go_readiness_blocks_without_key(self):
        with patch.dict(os.environ, {"OPENCODE_GO_API_KEY": ""}):
            response = self.client.get("/readiness/opencode-go")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "opencode_go_not_configured")

    def test_github_worker_readiness_returns_identity_only(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        installation = {"id": 158901090, "account": {"login": "nario0715masa0619-create"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value=installation) as verify:
            response = self.client.get("/readiness/github-worker")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "READY", "provider": "github-worker", "installation_id": 158901090, "account": "nario0715masa0619-create"})
        verify.assert_called_once_with("4823016", "158901090", "test-key")

    def test_github_worker_readiness_blocks_without_configuration(self):
        with patch.dict(os.environ, {"GITHUB_WORKER_APP_ID": "", "GITHUB_WORKER_INSTALLATION_ID": "", "GITHUB_WORKER_PRIVATE_KEY": ""}):
            response = self.client.get("/readiness/github-worker")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "github_worker_not_configured")

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
