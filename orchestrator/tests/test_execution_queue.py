import unittest

from execution_platform import ExecutionPlatform, TaskSpec, V3Status
from execution_preflight import ExecutionPreflight, PreflightCheck, REQUIRED_CHECKS
from execution_queue import ExecutionQueue, QueueRequestRejected
from execution_record_store import ExecutionRecordConflict, InMemoryExecutionRecordStore


def spec():
    return TaskSpec.from_dict({
        "repository": "nario0715masa0619-create/luvira-ai-devflow", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["tests pass"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["orchestrator/"]}, "model_policy": "low-cost-first",
    })


def preflight(passed=True):
    return ExecutionPreflight([lambda _, name=name: PreflightCheck(name, passed, "OK" if passed else "IAM_DENIED") for name in REQUIRED_CHECKS])


class ExecutionQueueTest(unittest.TestCase):
    def authorized_platform(self):
        platform = ExecutionPlatform()
        task = platform.create(spec(), "task-1")
        platform.validate(task.task_id)
        waiting = platform.request_approval(task.task_id)
        platform.authorize(task.task_id, waiting.approval_binding)
        return platform, task

    def test_persists_queued_execution_only_after_complete_preflight(self):
        platform, task = self.authorized_platform()
        records = InMemoryExecutionRecordStore()
        record, report = ExecutionQueue(platform, preflight(), records).request(task.task_id)
        self.assertTrue(report.passed)
        self.assertEqual(records.get(record.execution_id).task_id, task.task_id)
        self.assertEqual(task.status, V3Status.EXECUTION_QUEUED)

    def test_failed_preflight_cannot_create_record_or_change_authorization(self):
        platform, task = self.authorized_platform()
        records = InMemoryExecutionRecordStore()
        with self.assertRaisesRegex(QueueRequestRejected, "execution_preflight_failed"):
            ExecutionQueue(platform, preflight(False), records).request(task.task_id)
        self.assertEqual(task.status, V3Status.AUTHORIZED)
        self.assertFalse(records.records)

    def test_persistence_failure_returns_retryable_state_without_worker_call(self):
        platform, task = self.authorized_platform()
        records = InMemoryExecutionRecordStore()
        records.create = lambda _: (_ for _ in ()).throw(ExecutionRecordConflict("execution_id_exists"))
        with self.assertRaisesRegex(QueueRequestRejected, "execution_queue_not_durable"):
            ExecutionQueue(platform, preflight(), records).request(task.task_id)
        self.assertEqual(task.status, V3Status.EXECUTION_FAILED_RETRYABLE)
        self.assertIsNone(task.execution)
