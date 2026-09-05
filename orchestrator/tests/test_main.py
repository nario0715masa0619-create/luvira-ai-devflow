import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
import main
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

    def test_control_plane_readiness_blocks_without_deployed_store(self):
        response = self.client.get("/readiness/control-plane")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "control_plane_not_configured")

    def test_control_plane_factory_requires_explicit_firestore_configuration(self):
        with patch("main.TASK_STORE_BACKEND", ""):
            with self.assertRaisesRegex(RuntimeError, "control_plane_backend_must_be_firestore"):
                main.create_control_plane_from_environment()

    def test_control_plane_readiness_uses_read_only_firestore_probe(self):
        store = unittest.mock.Mock(spec=main.FirestoreTaskStore)
        control_plane = unittest.mock.Mock(store=store)
        with patch("main.CONTROL_PLANE", control_plane), patch("main.FIRESTORE_TASK_COLLECTION", "devflow_control_plane_tasks"):
            response = self.client.get("/readiness/control-plane")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["backend"], "firestore")
        store.readiness_check.assert_called_once_with()

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

    def test_worker_eligibility_uses_github_workflow_records(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        proposal = {"repository": "nario0715masa0619-create/luvira-ai-devflow", "issue": 42, "source_branch": "worker/issue-42-safe-change"}
        evidence = {"head_sha": "abc123", "workflows": {"Context Lock tests": "success", "Orchestrator tests": "success"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_quality_evidence", return_value=evidence) as lookup:
            response = self.client.post("/worker/eligibility", json=proposal)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ELIGIBLE_FOR_DRAFT_PR", "issue": 42, "head_sha": "abc123"})
        lookup.assert_called_once_with("4823016", "158901090", "test-key", "worker/issue-42-safe-change")

    def test_worker_eligibility_blocks_missing_github_workflow(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        proposal = {"repository": "nario0715masa0619-create/luvira-ai-devflow", "issue": 42, "source_branch": "worker/issue-42-safe-change"}
        evidence = {"head_sha": "abc123", "workflows": {"Context Lock tests": "success", "Orchestrator tests": "missing"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_quality_evidence", return_value=evidence):
            response = self.client.post("/worker/eligibility", json=proposal)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["status"], "PENDING_QUALITY_GATES")
        self.assertEqual(response.json["missing"], ["Orchestrator tests"])

    def test_worker_eligibility_blocks_branch_outside_issue_scope(self):
        response = self.client.post("/worker/eligibility", json={"repository": "nario0715masa0619-create/luvira-ai-devflow", "issue": 42, "source_branch": "main"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["reason"], "source_branch_not_allowed")

    def test_blocks_other_repository(self):
        response = self.client.post("/events", json=event({"repository": "other/repository", "action": "opened", "issue": 1}))
        self.assertEqual(response.status_code, 403)

    def test_accepts_signed_github_issue(self):
        payload = self.approval_issue_payload(26)
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        task = unittest.mock.Mock(status=main.TaskStatus.AWAITING_HUMAN_APPROVAL, task_id="task-26", spec_hash="abc", approval_binding="binding")
        with patch("main.CONTROL_PLANE", unittest.mock.Mock()), patch("main.approval_issue_spec", return_value={"repository": "nario0715masa0619-create/luvira-ai-devflow"}), patch("main.spec_hash", return_value="a" * 64), patch("main.github_default_branch_sha", return_value="base"):
            main.CONTROL_PLANE.create_draft.return_value = task
            response = self.client.post("/github/webhook", data=raw, content_type="application/json", headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "AWAITING_HUMAN_APPROVAL")
        self.assertEqual(response.json["task_id"], "task-26")

    def approval_issue_payload(self, number):
        body = """### Project ID

devflow
### Repository

nario0715masa0619-create/luvira-ai-devflow
### 承認すること

Read the repository
### タスク種別

documentation
### 受入条件

- README is reviewed
### 最大コスト（USD）

1.00
### 影響

Read only
### しないこと

No writes
### 許可を求める最初のアクション

read
### 有効期限（UTC）

2026-12-31T00:00:00Z
"""
        return {"action": "opened", "repository": {"full_name": "nario0715masa0619-create/luvira-ai-devflow"}, "issue": {"number": number, "node_id": "issue-node", "labels": [{"name": "ai-approval"}], "body": body}}

    def test_approval_issue_form_becomes_control_plane_spec(self):
        payload = self.approval_issue_payload(31)
        with patch("main.github_default_branch_sha", return_value="f" * 40):
            spec = main.approval_issue_spec(payload, "nario0715masa0619-create/luvira-ai-devflow", 31)

        self.assertEqual(spec["base_commit"], "f" * 40)
        self.assertEqual(spec["task_type"], "documentation")
        self.assertEqual(spec["acceptance_criteria"], ["README is reviewed"])
        self.assertEqual(spec["budget"], {"max_cost_usd": 1.0})
        self.assertEqual(spec["source"]["issue_number"], 31)

    def test_approval_issue_form_requires_ai_approval_label(self):
        payload = self.approval_issue_payload(32)
        payload["issue"]["labels"] = []
        with self.assertRaisesRegex(ValueError, "approval_label_required"):
            main.approval_issue_spec(payload, "nario0715masa0619-create/luvira-ai-devflow", 32)

    def test_blocks_unsigned_github_issue(self):
        response = self.client.post("/github/webhook", json={"action": "opened"}, headers={"X-GitHub-Event": "issues"})
        self.assertEqual(response.status_code, 401)

    def test_accepts_signed_github_ping_without_orchestrating(self):
        raw = json.dumps({"zen": "Keep it logically awesome."}).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        response = self.client.post("/github/webhook", data=raw, content_type="application/json", headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "OK", "event": "ping"})

    def test_public_webhook_ingress_rejects_every_route_except_webhook(self):
        with patch("main.PUBLIC_WEBHOOK_INGRESS_ONLY", True):
            response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json, {"status": "BLOCKED", "reason": "public_ingress_route_not_allowed"})

    def test_public_webhook_ingress_forwards_verified_issue_to_private_orchestrator(self):
        payload = {"action": "opened", "repository": {"full_name": "nario0715masa0619-create/luvira-ai-devflow"}, "issue": {"number": 7}}
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, "sha256").hexdigest()
        with patch("main.PUBLIC_WEBHOOK_INGRESS_ONLY", True), patch("main.ORCHESTRATOR_URL", "https://private.example"):
            with patch("main.forward_signed_webhook", return_value=(200, {"status": "PENDING_CONTEXT_LOCK"})) as forward:
                response = self.client.post("/github/webhook", data=raw, content_type="application/json", headers={"X-Hub-Signature-256": signature, "X-GitHub-Event": "issues"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "PENDING_CONTEXT_LOCK"})
        forward.assert_called_once_with(raw, signature, "issues")
