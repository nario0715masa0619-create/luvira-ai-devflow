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
from execution_platform import V3Status
from project_onboarding import InMemoryProjectRegistry, ProjectOnboardingError, ProjectOnboardingService


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

    def test_staging_generation_canary_is_opt_in_and_does_not_publish(self):
        artifact = {
            "schema": "luvira.devflow.implementation-artifact.v2",
            "task_id": "staging-opencode-generation-canary",
            "spec_hash": hashlib.sha256(b"staging-opencode-generation-canary-v1").hexdigest(),
            "base_commit": "0" * 40,
            "files": [{"path": "canary/staging-opencode-canary.md", "content": "# staging-only OpenCode generation canary\n"}],
            "changed_paths": ["canary/staging-opencode-canary.md"],
            "tests": [],
            "publication": "verification-only",
        }
        payload = json.dumps(artifact).encode()
        client = unittest.mock.Mock()
        client.generate_artifact.return_value = payload
        with patch.dict(os.environ, {"STAGING_OPENCODE_CANARY_ENABLED": "true", "OPENCODE_GO_API_KEY": "test-key"}):
            with patch("main.OpenCodeImplementationClient", return_value=client):
                response = self.client.post("/internal/staging/opencode-generation-canary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "READY", "artifact": "VERIFIED", "changed_path_count": 1})

    def test_staging_generation_canary_is_disabled_by_default(self):
        with patch.dict(os.environ, {"STAGING_OPENCODE_CANARY_ENABLED": ""}, clear=False):
            response = self.client.post("/internal/staging/opencode-generation-canary")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["reason"], "staging_generation_canary_disabled")

    def test_control_plane_readiness_blocks_without_deployed_store(self):
        response = self.client.get("/readiness/control-plane")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "v3_control_plane_not_configured")

    def test_control_plane_readiness_uses_read_only_firestore_probe(self):
        store = unittest.mock.Mock()
        with patch("main.V3_TASK_STORE", store), patch("main.V3_TASK_COLLECTION", "devflow_execution_tasks"):
            response = self.client.get("/readiness/control-plane")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["backend"], "firestore")
        self.assertEqual(response.json["lifecycle"], "v3-only")
        store.readiness_check.assert_called_once_with()

    def test_private_authorization_and_queue_use_one_v3_control_plane_operation(self):
        task_id = "github-issue-26-aaaaaaaaaaaaaaaa"
        approval_binding = "b" * 64
        record = unittest.mock.Mock(execution_id="execution-1", attempt=1)
        report = unittest.mock.Mock(public_dict=lambda: {"passed": True, "checks": []})
        control_plane = unittest.mock.Mock()
        control_plane.authorize.return_value = unittest.mock.Mock(task_id=task_id)
        with patch("main.V3_CONTROL_PLANE", control_plane):
            response = self.client.post(
                f"/control-plane/v3/tasks/{task_id}/authorize",
                json={"approval_binding": approval_binding, "actor": "nario0715masa0619-create"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "AUTHORIZED")
        control_plane.authorize.assert_called_once_with(task_id, approval_binding, "nario0715masa0619-create")

    def test_private_authorization_rejects_missing_binding(self):
        response = self.client.post(
            "/control-plane/v3/tasks/github-issue-26-aaaaaaaaaaaaaaaa/authorize",
            json={"actor": "nario0715masa0619-create"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["reason"], "approval_binding_required")

    def test_pending_approval_issue_returns_server_owned_task_facts(self):
        task = unittest.mock.Mock(task_id="github-issue-26-aaaaaaaaaaaaaaaa", approval_binding="b" * 64)
        control_plane = unittest.mock.Mock()
        control_plane.pending_for_issue.return_value = task
        with patch("main.V3_CONTROL_PLANE", control_plane):
            response = self.client.get("/control-plane/v3/approval-issues/26/pending")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["task_id"], task.task_id)
        control_plane.pending_for_issue.assert_called_once_with(26)

    def test_approval_issue_status_returns_requester_safe_published_result(self):
        execution = unittest.mock.Mock(
            status=V3Status.PUBLISHED,
            failure_code=None,
            publication_url="https://github.com/example/product/pull/42",
        )
        task = unittest.mock.Mock(task_id="github-issue-26-aaaaaaaaaaaaaaaa", status=V3Status.PUBLISHED,
                                  execution=execution)
        store = unittest.mock.Mock()
        store.task_for_issue.return_value = task
        with patch("main.V3_TASK_STORE", store):
            response = self.client.get("/control-plane/v3/approval-issues/26/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "status": "PUBLISHED", "task_id": task.task_id,
            "execution_status": "PUBLISHED", "failure_code": None,
            "publication_url": "https://github.com/example/product/pull/42",
            "checkpoint": "DRAFT_PR", "recovery_action": "AWAIT_HUMAN_MERGE",
            "terminal": True,
        })
        store.task_for_issue.assert_called_once_with(26)

    def test_approval_issue_status_rejects_unknown_issue_without_task_data(self):
        store = unittest.mock.Mock()
        store.task_for_issue.side_effect = ValueError("v3_task_not_found")
        with patch("main.V3_TASK_STORE", store):
            response = self.client.get("/control-plane/v3/approval-issues/26/status")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json, {"status": "BLOCKED", "reason": "task_not_found"})

    def test_status_projects_safe_retry_checkpoint_without_task_input(self):
        execution = unittest.mock.Mock(
            status=V3Status.EXECUTION_FAILED_RETRYABLE, attempt=1,
            failure_code="WORKER_EXECUTION_RETRYABLE", publication_url=None,
        )
        task = unittest.mock.Mock(task_id="github-issue-26-aaaaaaaaaaaaaaaa",
                                  status=V3Status.EXECUTION_FAILED_RETRYABLE, execution=execution)
        store = unittest.mock.Mock()
        store.task_for_issue.return_value = task
        with patch("main.V3_TASK_STORE", store):
            response = self.client.get("/control-plane/v3/approval-issues/26/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["checkpoint"], "WORKER_DISPATCH")
        self.assertEqual(response.json["recovery_action"], "RESUME_SAFE_WORKER_RETRY")

    def test_private_authorization_rejects_unsafe_actor(self):
        response = self.client.post(
            "/control-plane/v3/tasks/github-issue-26-aaaaaaaaaaaaaaaa/authorize",
            json={"approval_binding": "b" * 64, "actor": "not an actor"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["reason"], "approval_actor_invalid")

    def test_private_authorization_rejects_task_id_outside_control_plane_format(self):
        response = self.client.post(
            "/control-plane/v3/tasks/task-26/authorize",
            json={"approval_binding": "b" * 64, "actor": "nario0715masa0619-create"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["reason"], "task_id_invalid")

    def test_v3_authorize_queue_requires_private_runtime_wiring(self):
        response = self.client.post("/control-plane/v3/tasks/github-issue-26-aaaaaaaaaaaaaaaa/authorize", json={"approval_binding": "b" * 64, "actor": "nario0715masa0619-create"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "v3_control_plane_not_configured")

    def test_scheduler_broker_sweep_returns_durable_outcomes(self):
        broker = unittest.mock.Mock()
        broker.sweep.return_value = [("github-issue-26-aaaaaaaaaaaaaaaa", "QUEUED", "execution-1")]
        with patch("main.V3_AUTONOMOUS_BROKER", broker):
            response = self.client.post("/internal/broker/sweep")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "OK")
        self.assertEqual(response.json["outcomes"][0]["execution_id"], "execution-1")

    def test_scheduler_broker_sweep_fails_closed_without_runtime(self):
        with patch("main.V3_AUTONOMOUS_BROKER", None):
            response = self.client.post("/internal/broker/sweep")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "v3_broker_not_configured")

    def test_legacy_bootstrap_is_disabled_before_any_identity_or_worker_access(self):
        task_id = "github-issue-26-aaaaaaaaaaaaaaaa"
        response = self.client.post(f"/control-plane/tasks/{task_id}/bootstrap", json={"allowed_paths": ["README.md"]})

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json["reason"], "legacy_execution_route_retired")

    def test_github_worker_readiness_returns_alert_token_readiness(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        installation = {"id": 158901090, "account": {"login": "nario0715masa0619-create"}, "permissions": {"issues": "write"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value=installation) as verify, patch("main.github_worker_installation_token", return_value="token"), patch("main.github_api_request", return_value={"full_name": "nario0715masa0619-create/luvira-ai-devflow"}) as repository:
            response = self.client.get("/readiness/github-worker")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "READY", "provider": "github-worker", "installation_id": 158901090, "account": "nario0715masa0619-create", "incident_alerting": "issues-write-token-ready"})
        verify.assert_called_once_with("4823016", "158901090", "test-key")
        repository.assert_called_once_with("https://api.github.com/repos/nario0715masa0619-create/luvira-ai-devflow", token="token")

    def test_github_worker_readiness_blocks_without_configuration(self):
        with patch.dict(os.environ, {"GITHUB_WORKER_APP_ID": "", "GITHUB_WORKER_INSTALLATION_ID": "", "GITHUB_WORKER_PRIVATE_KEY": ""}):
            response = self.client.get("/readiness/github-worker")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "github_worker_not_configured")

    def test_runtime_watchdog_identity_check_requires_expected_account(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value={"id": 158901090, "account": {"login": "other"}, "permissions": {"issues": "write"}}):
            self.assertFalse(main.github_worker_identity_available())
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value={"id": 158901090, "account": {"login": "nario0715masa0619-create"}, "permissions": {"issues": "write"}}):
            self.assertTrue(main.github_worker_identity_available())

    def test_github_worker_readiness_blocks_without_incident_write_permission(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        installation = {"id": 158901090, "account": {"login": "nario0715masa0619-create"}, "permissions": {"issues": "read"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value=installation):
            response = self.client.get("/readiness/github-worker")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "github_worker_issues_write_required")

    def test_github_worker_readiness_blocks_unusable_alert_token(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        installation = {"id": 158901090, "account": {"login": "nario0715masa0619-create"}, "permissions": {"issues": "write"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value=installation), patch("main.github_worker_installation_token", side_effect=ValueError("unavailable")):
            response = self.client.get("/readiness/github-worker")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["reason"], "github_worker_alert_token_unavailable")

    def test_runtime_incident_uses_only_failed_component_names(self):
        with patch("main.github_worker_installation_token", return_value="token"), patch("main.github_api_request") as request:
            main.create_runtime_incident({"control_plane": "READY", "opencode_go": "UNAVAILABLE"})

        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["body"]["title"], "DevFlow runtime health blocked")
        self.assertIn("opencode_go", request.call_args.kwargs["body"]["body"])
        self.assertNotIn("token", request.call_args.kwargs["body"]["body"])

    def test_runtime_alerting_probe_requires_the_expected_repository(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016", "GITHUB_WORKER_INSTALLATION_ID": "158901090", "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        installation = {"id": 158901090, "account": {"login": "nario0715masa0619-create"}, "permissions": {"issues": "write"}}
        with patch.dict(os.environ, configured), patch("main.github_worker_installation", return_value=installation), patch("main.github_worker_installation_token", return_value="token"), patch("main.github_api_request", return_value={"full_name": "other/repository"}):
            self.assertFalse(main.github_worker_alerting_available())

    def test_worker_eligibility_uses_github_workflow_records(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        proposal = {"project_id": "project-canary", "repository": "nario0715masa0619-create/devflow-onboarding-canary", "issue": 42, "source_branch": "worker/issue-42-safe-change"}
        evidence = {"head_sha": "abc123", "workflows": {"Context Lock tests": "success", "Orchestrator tests": "success"}}
        projects = unittest.mock.Mock()
        projects.require_ready_repository.return_value = proposal["repository"]
        with patch.dict(os.environ, configured), patch("main.PROJECT_ONBOARDING", projects), patch("main.github_worker_quality_evidence", return_value=evidence) as lookup:
            response = self.client.post("/worker/eligibility", json=proposal)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ELIGIBLE_FOR_DRAFT_PR", "issue": 42, "head_sha": "abc123"})
        lookup.assert_called_once_with(proposal["repository"], "4823016", "158901090", "test-key", "worker/issue-42-safe-change")

    def test_worker_eligibility_blocks_missing_github_workflow(self):
        configured = {
            "GITHUB_WORKER_APP_ID": "4823016",
            "GITHUB_WORKER_INSTALLATION_ID": "158901090",
            "GITHUB_WORKER_PRIVATE_KEY": "test-key",
        }
        proposal = {"project_id": "project-canary", "repository": "nario0715masa0619-create/devflow-onboarding-canary", "issue": 42, "source_branch": "worker/issue-42-safe-change"}
        evidence = {"head_sha": "abc123", "workflows": {"Context Lock tests": "success", "Orchestrator tests": "missing"}}
        projects = unittest.mock.Mock()
        projects.require_ready_repository.return_value = proposal["repository"]
        with patch.dict(os.environ, configured), patch("main.PROJECT_ONBOARDING", projects), patch("main.github_worker_quality_evidence", return_value=evidence):
            response = self.client.post("/worker/eligibility", json=proposal)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["status"], "PENDING_QUALITY_GATES")
        self.assertEqual(response.json["missing"], ["Orchestrator tests"])

    def test_worker_eligibility_blocks_branch_outside_issue_scope(self):
        projects = unittest.mock.Mock()
        projects.require_ready_repository.return_value = "nario0715masa0619-create/devflow-onboarding-canary"
        with patch("main.PROJECT_ONBOARDING", projects):
            response = self.client.post("/worker/eligibility", json={"project_id": "project-canary", "repository": "nario0715masa0619-create/devflow-onboarding-canary", "issue": 42, "source_branch": "main"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["reason"], "source_branch_not_allowed")

    def test_blocks_other_repository(self):
        response = self.client.post("/events", json=event({"repository": "other/repository", "action": "opened", "issue": 1}))
        self.assertEqual(response.status_code, 403)

    def test_accepts_signed_github_issue(self):
        payload = self.approval_issue_payload(26)
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        task = unittest.mock.Mock(status=main.V3Status.AWAITING_HUMAN_APPROVAL, task_id="task-26", approval_binding="binding")
        task.spec.hash = "abc"
        plane = unittest.mock.Mock()
        plane.register.return_value = task
        with patch("main.V3_CONTROL_PLANE", plane), patch("main.PROJECT_ONBOARDING", object()), patch("main.approval_issue_spec", return_value={"repository": "nario0715masa0619-create/devflow-onboarding-canary"}), patch("main.task_spec_hash", return_value="a" * 64), patch("main.github_default_branch_sha", return_value="base"):
            response = self.client.post("/github/webhook", data=raw, content_type="application/json", headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "AWAITING_HUMAN_APPROVAL")
        self.assertEqual(response.json["task_id"], "task-26")

    def test_signed_project_onboarding_issue_registers_a_project_without_a_worker(self):
        payload = self.project_onboarding_issue_payload(52)
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            response = self.client.post(
                "/github/webhook", data=raw, content_type="application/json",
                headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["status"], "AWAITING_HUMAN_APPROVAL")
            self.assertEqual(response.json["project_id"], "project-orca-e2e-sample")
            self.assertEqual(main.PROJECT_ONBOARDING.get("project-orca-e2e-sample").status.value, "REQUESTED")
        finally:
            main.PROJECT_ONBOARDING = previous

    def test_project_onboarding_issue_is_idempotent_for_github_opened_and_labeled_events(self):
        payload = self.project_onboarding_issue_payload(53)
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            first = self.client.post(
                "/github/webhook", data=raw, content_type="application/json",
                headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature},
            )
            payload["action"] = "labeled"
            raw = json.dumps(payload).encode()
            signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
            repeated = self.client.post(
                "/github/webhook", data=raw, content_type="application/json",
                headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature},
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(repeated.json["project_id"], "project-orca-e2e-sample")
        finally:
            main.PROJECT_ONBOARDING = previous

    def test_project_onboarding_rejects_an_implementation_label_mix(self):
        payload = self.project_onboarding_issue_payload(54)
        payload["issue"]["labels"].append({"name": "ai-approval"})
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            response = self.client.post(
                "/github/webhook", data=raw, content_type="application/json",
                headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature},
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["reason"], "project_onboarding_label_conflict")
        finally:
            main.PROJECT_ONBOARDING = previous

    def approval_issue_payload(self, number):
        body = """### Project ID

project-canary
### Repository

nario0715masa0619-create/devflow-onboarding-canary
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
### 許可するリポジトリ内パス

["README.md"]
### 参照を許可するリポジトリ内パス

["README.md", "docs/"]
### 許可を求める最初のアクション

read
### 有効期限（UTC）

2026-12-31T00:00:00Z
"""
        return {"action": "opened", "repository": {"full_name": "nario0715masa0619-create/luvira-ai-devflow"}, "issue": {"number": number, "node_id": "issue-node", "labels": [{"name": "ai-approval"}], "body": body}}

    def project_onboarding_issue_payload(self, number):
        body = (
            "### Project Owner\n\n"
            "nario0715masa0619-create\n"
            "### Project Slug\n\n"
            "orca-e2e-sample\n"
            "### プロダクト概要\n\n"
            "Orca受付から新規プロダクトを安全に作成する検証用CLI\n"
        )
        return {
            "action": "opened",
            "repository": {"full_name": "nario0715masa0619-create/luvira-ai-devflow"},
            "issue": {
                "number": number,
                "node_id": "project-issue-node",
                "labels": [{"name": "project-onboarding"}],
                "body": body,
            },
        }

    def test_approval_issue_form_becomes_control_plane_spec(self):
        payload = self.approval_issue_payload(31)
        projects = unittest.mock.Mock()
        projects.require_ready_repository.return_value = "nario0715masa0619-create/devflow-onboarding-canary"
        with patch("main.github_default_branch_sha", return_value="f" * 40):
            spec = main.approval_issue_spec(payload, "nario0715masa0619-create/luvira-ai-devflow", 31, projects)

        self.assertEqual(spec["base_commit"], "f" * 40)
        self.assertEqual(spec["approval_context"]["task_type"], "documentation")
        self.assertEqual(spec["acceptance_criteria"], ["README is reviewed"])
        self.assertEqual(spec["budget"], {"max_cost_usd": 1.0})
        self.assertEqual(spec["execution_scope"]["source_paths"], ["README.md", "docs/"])
        self.assertEqual(spec["approval_context"]["source"]["issue_number"], 31)
        self.assertEqual(spec["repository"], "nario0715masa0619-create/devflow-onboarding-canary")

    def test_approval_issue_form_preserves_an_explicit_implementation_request(self):
        payload = self.approval_issue_payload(33)
        payload["issue"]["body"] = payload["issue"]["body"].replace("\nread\n### 有効期限", "\nimplementation\n### 有効期限")
        projects = unittest.mock.Mock()
        projects.require_ready_repository.return_value = "nario0715masa0619-create/devflow-onboarding-canary"
        with patch("main.github_default_branch_sha", return_value="f" * 40):
            spec = main.approval_issue_spec(payload, "nario0715masa0619-create/luvira-ai-devflow", 33, projects)

        self.assertEqual(spec["requested_action"], "implementation")

    def test_approval_issue_form_requires_ai_approval_label(self):
        payload = self.approval_issue_payload(32)
        payload["issue"]["labels"] = []
        with self.assertRaisesRegex(ValueError, "approval_label_required"):
            main.approval_issue_spec(payload, "nario0715masa0619-create/luvira-ai-devflow", 32, unittest.mock.Mock())

    def test_approval_issue_rejects_a_repository_not_bound_to_the_ready_project(self):
        payload = self.approval_issue_payload(34)
        projects = unittest.mock.Mock()
        projects.require_ready_repository.return_value = "nario0715masa0619-create/another-product"
        with self.assertRaisesRegex(ValueError, "project_repository_mismatch"):
            main.approval_issue_spec(payload, "nario0715masa0619-create/luvira-ai-devflow", 34, projects)

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

    def test_project_onboarding_requires_approval_before_provisioning(self):
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            created = self.client.post("/control-plane/v3/projects", json={
                "owner": "nario0715masa0619-create", "slug": "new-product", "description": "新規プロダクト",
            })
            self.assertEqual(created.status_code, 201)
            project_id = created.json["project_id"]
            self.assertEqual(self.client.post(f"/control-plane/v3/projects/{project_id}/claim-provisioning").status_code, 409)

            pending = self.client.get(f"/control-plane/v3/projects/{project_id}/pending")
            self.assertEqual(pending.status_code, 200)
            authorized = self.client.post(f"/control-plane/v3/projects/{project_id}/authorize", json={
                "actor": "nario0715masa0619-create", "approval_binding": pending.json["approval_binding"],
            })
            self.assertEqual(authorized.status_code, 200)
            claimed = self.client.post(f"/control-plane/v3/projects/{project_id}/claim-provisioning")
            self.assertEqual(claimed.status_code, 200)
            self.assertEqual(claimed.json["status"], "PROVISIONING")
            checkpoint = self.client.post(f"/control-plane/v3/projects/{project_id}/checkpoint-provisioning", json={
                "repository": "nario0715masa0619-create/new-product", "bootstrap_commit": "a" * 40,
            })
            self.assertEqual(checkpoint.status_code, 200)
            failed = self.client.post(f"/control-plane/v3/projects/{project_id}/fail-provisioning", json={
                "failure_code": "GITHUB_APP_NOT_INSTALLED",
            })
            self.assertEqual(failed.status_code, 200)
            self.assertEqual(failed.json["status"], "PROVISION_FAILED")

            # A recovery needs the same protected human approval again; it
            # must not silently retry from a terminal failure.
            retry_pending = self.client.get(f"/control-plane/v3/projects/{project_id}/pending")
            self.assertEqual(retry_pending.status_code, 200)
            retry_authorized = self.client.post(f"/control-plane/v3/projects/{project_id}/authorize", json={
                "actor": "nario0715masa0619-create", "approval_binding": retry_pending.json["approval_binding"],
            })
            self.assertEqual(retry_authorized.status_code, 200)
            self.assertEqual(self.client.post(f"/control-plane/v3/projects/{project_id}/claim-provisioning").status_code, 200)
        finally:
            main.PROJECT_ONBOARDING = previous

    def test_project_onboarding_rejects_wrong_approval_binding(self):
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            created = self.client.post("/control-plane/v3/projects", json={
                "owner": "nario0715masa0619-create", "slug": "new-product", "description": "新規プロダクト",
            })
            response = self.client.post(
                f"/control-plane/v3/projects/{created.json['project_id']}/authorize",
                json={"actor": "nario0715masa0619-create", "approval_binding": "0" * 64},
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json["reason"], "project_approval_binding_mismatch")
        finally:
            main.PROJECT_ONBOARDING = previous

    def test_project_onboarding_completion_requires_control_plane_app_evidence(self):
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            created = self.client.post("/control-plane/v3/projects", json={
                "owner": "nario0715masa0619-create", "slug": "new-product", "description": "新規プロダクト",
            })
            project_id = created.json["project_id"]
            binding = self.client.get(f"/control-plane/v3/projects/{project_id}/pending").json["approval_binding"]
            self.client.post(f"/control-plane/v3/projects/{project_id}/authorize", json={"actor": "nario0715masa0619-create", "approval_binding": binding})
            self.client.post(f"/control-plane/v3/projects/{project_id}/claim-provisioning")
            payload = {"repository": "nario0715masa0619-create/new-product", "bootstrap_commit": "a" * 40}
            self.assertEqual(self.client.post(f"/control-plane/v3/projects/{project_id}/checkpoint-provisioning", json=payload).status_code, 200)
            with patch("main.github_project_repository_ready", side_effect=ProjectOnboardingError("PROJECT_GITHUB_APP_NOT_INSTALLED_FOR_REPOSITORY")):
                blocked = self.client.post(f"/control-plane/v3/projects/{project_id}/complete-provisioning", json=payload)
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(blocked.json["reason"], "PROJECT_GITHUB_APP_NOT_INSTALLED_FOR_REPOSITORY")
            with patch("main.github_project_repository_ready", return_value=None):
                self.assertEqual(self.client.post(f"/control-plane/v3/projects/{project_id}/complete-provisioning", json=payload).status_code, 200)
        finally:
            main.PROJECT_ONBOARDING = previous

    def test_project_provisioning_preflight_reports_the_actionable_app_scope(self):
        with patch("main.github_project_provisioning_ready", side_effect=ProjectOnboardingError("PROJECT_GITHUB_APP_ALL_REPOSITORIES_REQUIRED")):
            response = self.client.get("/readiness/project-provisioning")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {
            "status": "BLOCKED", "reason": "PROJECT_GITHUB_APP_ALL_REPOSITORIES_REQUIRED",
        })

    def test_project_onboarding_readiness_requires_a_durable_registry(self):
        previous = main.PROJECT_ONBOARDING
        main.PROJECT_ONBOARDING = ProjectOnboardingService(InMemoryProjectRegistry())
        try:
            response = self.client.get("/readiness/project-onboarding")
        finally:
            main.PROJECT_ONBOARDING = previous
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "READY")
