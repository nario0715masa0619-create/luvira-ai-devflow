import base64
import json
import unittest

from artifact_handoff import ArtifactHandoff, InMemoryVerifiedArtifactStore
from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from execution_result_adapter import RESULT_PREFIX
from worker_result_reconciler import WorkerResultReconciler


def running_task():
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["test"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["src/"]}, "model_policy": "low-cost",
    })
    record = ExecutionRecord("execution", "task", spec.hash, 1, V3Status.EXECUTION_RUNNING, external_operation_id="run-123")
    return V3Task("task", spec, V3Status.EXECUTION_RUNNING, execution=record)


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

    def test_malformed_artifact_fails_closed(self):
        task = running_task()
        reconciler, writes = self.reconciler(task, "SUCCEEDED", "not an artifact")
        self.assertEqual(reconciler.sweep(), [("task", "ARTIFACT_REJECTED_FINAL", "execution")])
        self.assertEqual(writes, [(V3Status.EXECUTION_FAILED_FINAL, V3Status.EXECUTION_FAILED_FINAL)])
