import hashlib
import json
import unittest

from execution_platform import ExecutionRecord, V3Status
from v3_control_plane import V3ControlPlane, V3ControlPlaneError


def spec():
    return {
        "repository": "owner/repo", "base_commit": "a" * 40,
        "requested_action": "read", "acceptance_criteria": ["no write"],
        "budget": {"max_cost_usd": 1}, "expiry": "2027-01-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["README.md"]},
        "model_policy": "low-cost-first:opencode-go",
        "approval_context": {"approval": "read only", "source": {"issue_number": 1}},
    }


class MemoryStore:
    def __init__(self): self.tasks = {}
    def create(self, task):
        if task.task_id in self.tasks: raise ValueError("v3_task_exists")
        self.tasks[task.task_id] = task
    def get(self, task_id):
        if task_id not in self.tasks: raise ValueError("v3_task_not_found")
        return self.tasks[task_id]
    def save(self, task): task.revision += 1; self.tasks[task.task_id] = task
    def pending_for_issue(self, issue_number):
        matches = [task for task in self.tasks.values() if task.spec.approval_context.get("source", {}).get("issue_number") == issue_number and task.status is V3Status.AWAITING_HUMAN_APPROVAL]
        if len(matches) != 1: raise ValueError("v3_task_not_found")
        return matches[0]


class Queue:
    def __init__(self, store): self.store = store; self.calls = 0
    def request(self, task_id):
        self.calls += 1
        task = self.store.get(task_id)
        task.status = V3Status.EXECUTION_QUEUED
        task.execution = ExecutionRecord("execution-1", task_id, task.spec.hash, 1, V3Status.EXECUTION_QUEUED)
        self.store.save(task)
        return task.execution, type("Report", (), {"public_dict": lambda _: {"passed": True}})()


class V3ControlPlaneTest(unittest.TestCase):
    def test_one_task_document_owns_intake_approval_and_queued_execution(self):
        store = MemoryStore(); queue = Queue(store); plane = V3ControlPlane(store, queue)
        task = plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        self.assertEqual(task.status, V3Status.AWAITING_HUMAN_APPROVAL)
        expected = hashlib.sha256(json.dumps({"task_id": task.task_id, "spec_hash": task.spec.hash}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        record, report = plane.authorize_and_queue(task.task_id, expected, "reviewer")
        self.assertEqual(record.execution_id, "execution-1")
        self.assertTrue(report.public_dict()["passed"])
        self.assertEqual(store.get(task.task_id).status, V3Status.EXECUTION_QUEUED)
        self.assertEqual(queue.calls, 1)

    def test_retry_is_idempotent_and_never_projects_legacy_state(self):
        store = MemoryStore(); queue = Queue(store); plane = V3ControlPlane(store, queue)
        task = plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        binding = hashlib.sha256(json.dumps({"task_id": task.task_id, "spec_hash": task.spec.hash}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        first, _ = plane.authorize_and_queue(task.task_id, binding, "reviewer")
        second, report = plane.authorize_and_queue(task.task_id, binding, "reviewer")
        self.assertEqual(first.execution_id, second.execution_id)
        self.assertIsNone(report)
        self.assertEqual(queue.calls, 1)

    def test_authorization_survives_broker_unavailability_without_queueing(self):
        store = MemoryStore(); queue = Queue(store); plane = V3ControlPlane(store, queue)
        task = plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        binding = hashlib.sha256(json.dumps({"task_id": task.task_id, "spec_hash": task.spec.hash}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        authorized = plane.authorize(task.task_id, binding, "reviewer")
        self.assertEqual(authorized.status, V3Status.AUTHORIZED)
        self.assertEqual(queue.calls, 0)

    def test_changed_approval_context_cannot_reuse_task_id(self):
        store = MemoryStore(); plane = V3ControlPlane(store, Queue(store))
        plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        changed = spec(); changed["approval_context"]["approval"] = "write source"
        with self.assertRaisesRegex(V3ControlPlaneError, "different_spec"):
            plane.register("github-issue-1-aaaaaaaaaaaaaaaa", changed)

    def test_resolves_pending_task_by_issue_without_reconstructing_hash(self):
        store = MemoryStore(); plane = V3ControlPlane(store, Queue(store))
        task = plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        self.assertIs(plane.pending_for_issue(1), task)

    def test_concurrent_webhook_delivery_reloads_the_winning_task(self):
        store = MemoryStore(); plane = V3ControlPlane(store, Queue(store))
        first = plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        repeated = plane.register("github-issue-1-aaaaaaaaaaaaaaaa", spec())
        self.assertIs(first, repeated)
