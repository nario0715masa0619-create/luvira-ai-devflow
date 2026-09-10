import base64
import json
import unittest

from artifact_handoff import ArtifactHandoff, InMemoryVerifiedArtifactStore
from cloud_run_bootstrap_client import CloudRunBootstrapClientError
from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from execution_result_adapter import RESULT_PREFIX
from v3_transaction import V3TransactionError
from worker_result_reconciler import WorkerResultReconciler


def running_task():
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["test"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["src/"]}, "model_policy": "low-cost",
    })
    record = ExecutionRecord("execution", "task", spec.hash, 1, V3Status.WORKER_LAUNCH_ACCEPTED, external_operation_id="run-123", launch_operation_id="operations/123")
    return V3Task("task", spec, V3Status.WORKER_LAUNCH_ACCEPTED, execution=record)


def artifact_line(task):
    artifact = {"schema": "luvira.devflow.worker-bootstrap.v1", "task_id": task.task_id,
                "spec_hash": task.spec.hash, "base_commit": task.spec.base_commit,
                "status": "ENVELOPE_VALIDATED_NO_MODEL_EXECUTION", "changed_paths": [],
                "tests": [], "publication": "none"}
    return RESULT_PREFIX + base64.b64encode(json.dumps(artifact).encode()).decode()


class WorkerResultReconcilerTest(unittest.TestCase):
    def reconciler(self, task, state, stdout=""):
        writes = []
        tasks = type("Tasks", (), {"running": lambda _: [task]})()
        transaction = type("Tx", (), {"record_external_operation": lambda *_: None, "record_result": lambda _, current, record, status: writes.append((current.status, status))})()
        worker = type("Worker", (), {"find_execution_for_task": lambda *_: None, "completion_state": lambda _, __: state})()
        logs = type("Logs", (), {"read_stdout": lambda _, __: stdout})()
        return WorkerResultReconciler(tasks, transaction, worker, logs, ArtifactHandoff(InMemoryVerifiedArtifactStore())), writes

    def test_completed_worker_is_verified_without_restarting_it(self):
        task = running_task()
        reconciler, writes = self.reconciler(task, "SUCCEEDED", artifact_line(task))
        self.assertEqual(reconciler.sweep(), [("task", "WORKER_HEALTH_VERIFIED", "run-123")])
        self.assertEqual(writes, [(V3Status.WORKER_HEALTH_VERIFIED, V3Status.WORKER_HEALTH_VERIFIED)])

    def test_failed_bootstrap_worker_is_safely_queued_for_a_new_attempt(self):
        task = running_task()
        reconciler, writes = self.reconciler(task, "FAILED")
        self.assertEqual(reconciler.sweep(), [("task", "WORKER_FAILED_RETRYABLE", "execution")])
        self.assertEqual(writes, [(V3Status.EXECUTION_FAILED_RETRYABLE, V3Status.EXECUTION_FAILED_RETRYABLE)])

    def test_unaccepted_worker_start_is_safely_requeued(self):
        task = running_task()
        task.execution.external_operation_id = None
        reconciler, writes = self.reconciler(task, "PENDING")
        self.assertEqual(reconciler.sweep(), [("task", "WORKER_DISPATCH_RETRYABLE", "execution")])
        self.assertEqual(task.execution.failure_code, "WORKER_DISPATCH_NOT_ACCEPTED_RETRYABLE")
        self.assertEqual(writes, [(V3Status.EXECUTION_FAILED_RETRYABLE, V3Status.EXECUTION_FAILED_RETRYABLE)])

    def test_launch_operation_is_reconciled_without_searching_for_a_duplicate(self):
        task = running_task()
        task.execution.external_operation_id = None
        task.execution.launch_operation_id = "operations/123"
        writes = []
        tasks = type("Tasks", (), {"running": lambda _: [task]})()
        transaction = type("Tx", (), {"record_external_operation": lambda *_: writes.append("bound"), "record_result": lambda *_: None})()
        worker = type("Worker", (), {"execution_for_operation": lambda _, __: "run-456", "completion_state": lambda *_: "PENDING"})()
        logs = type("Logs", (), {"read_stdout": lambda *_: ""})()
        reconciler = WorkerResultReconciler(tasks, transaction, worker, logs, ArtifactHandoff(InMemoryVerifiedArtifactStore()))

        self.assertEqual(reconciler.sweep(), [("task", "WORKER_RUNNING", "run-456")])
        self.assertEqual(task.execution.external_operation_id, "run-456")
        self.assertEqual(writes, ["bound"])

    def test_known_launch_does_not_search_historical_executions_after_a_read_failure(self):
        task = running_task()
        task.execution.external_operation_id = None
        task.execution.launch_operation_id = "operations/123"
        writes = []
        tasks = type("Tasks", (), {"running": lambda _: [task]})()
        transaction = type("Tx", (), {"record_external_operation": lambda *_: writes.append("bound"), "record_result": lambda *_: None})()
        worker = type("Worker", (), {
            "execution_for_operation": lambda *_: (_ for _ in ()).throw(CloudRunBootstrapClientError("cloud_run_operation_failed")),
            "find_execution_for_task": lambda *_: self.fail("known launch must not use historical search"),
        })()
        logs = type("Logs", (), {"read_stdout": lambda *_: ""})()
        reconciler = WorkerResultReconciler(tasks, transaction, worker, logs, ArtifactHandoff(InMemoryVerifiedArtifactStore()))

        self.assertEqual(reconciler.sweep(), [("task", "WORKER_LAUNCH_RECOVERY_PENDING", "operations/123")])
        self.assertIsNone(task.execution.external_operation_id)
        self.assertEqual(writes, [])

    def test_known_launch_with_ambiguous_history_remains_pending_without_retry(self):
        task = running_task()
        task.execution.external_operation_id = None
        task.execution.launch_operation_id = "operations/123"
        writes = []
        tasks = type("Tasks", (), {"running": lambda _: [task]})()
        transaction = type("Tx", (), {"record_external_operation": lambda *_: writes.append("bound"), "record_result": lambda *_: writes.append("result")})()
        worker = type("Worker", (), {"execution_for_operation": lambda *_: (_ for _ in ()).throw(CloudRunBootstrapClientError("cloud_run_execution_ambiguous"))})()
        logs = type("Logs", (), {"read_stdout": lambda *_: ""})()
        reconciler = WorkerResultReconciler(tasks, transaction, worker, logs, ArtifactHandoff(InMemoryVerifiedArtifactStore()))

        self.assertEqual(reconciler.sweep(), [("task", "WORKER_LAUNCH_RECOVERY_PENDING", "operations/123")])
        self.assertEqual(task.status, V3Status.EXECUTION_RUNNING)
        self.assertEqual(writes, [])

    def test_bind_conflict_is_retried_without_launching_another_worker(self):
        task = running_task()
        task.execution.external_operation_id = None
        task.execution.launch_operation_id = "operations/123"
        tasks = type("Tasks", (), {"running": lambda _: [task]})()
        transaction = type("Tx", (), {
            "record_external_operation": lambda *_: (_ for _ in ()).throw(V3TransactionError("v3_external_operation_not_recordable")),
            "record_result": lambda *_: None,
        })()
        worker = type("Worker", (), {"execution_for_operation": lambda *_: "run-456"})()
        logs = type("Logs", (), {"read_stdout": lambda *_: ""})()
        reconciler = WorkerResultReconciler(tasks, transaction, worker, logs, ArtifactHandoff(InMemoryVerifiedArtifactStore()))

        self.assertEqual(reconciler.sweep(), [("task", "WORKER_BINDING_RETRY_PENDING", "execution")])

    def test_malformed_artifact_fails_closed(self):
        task = running_task()
        reconciler, writes = self.reconciler(task, "SUCCEEDED", "not an artifact")
        self.assertEqual(reconciler.sweep(), [("task", "worker_bootstrap_artifact_missing_or_ambiguous", "execution")])
        self.assertEqual(task.execution.failure_code, "worker_bootstrap_artifact_missing_or_ambiguous")
        self.assertEqual(writes, [(V3Status.EXECUTION_FAILED_FINAL, V3Status.EXECUTION_FAILED_FINAL)])

    def test_legacy_ambiguous_worker_launch_is_safely_retried(self):
        task = running_task()
        task.execution.external_operation_id = None
        writes = []
        tasks = type("Tasks", (), {"running": lambda _: [task]})()
        transaction = type("Tx", (), {"record_external_operation": lambda *_: None, "record_result": lambda *_: writes.append(task.status)})()
        worker = type("Worker", (), {"find_execution_for_task": lambda *_: (_ for _ in ()).throw(CloudRunBootstrapClientError("cloud_run_execution_ambiguous"))})()
        logs = type("Logs", (), {"read_stdout": lambda *_: ""})()
        reconciler = WorkerResultReconciler(tasks, transaction, worker, logs, ArtifactHandoff(InMemoryVerifiedArtifactStore()))

        self.assertEqual(reconciler.sweep(), [("task", "WORKER_DISPATCH_RETRYABLE", "execution")])
        self.assertEqual(task.status, V3Status.EXECUTION_FAILED_RETRYABLE)
        self.assertEqual(writes, [V3Status.EXECUTION_FAILED_RETRYABLE])
