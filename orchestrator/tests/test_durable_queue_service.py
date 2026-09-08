import unittest

from durable_queue_service import DurableQueueRejected, DurableQueueService
from execution_platform import TaskSpec, V3Status, V3Task
from execution_preflight import ExecutionPreflight, PreflightCheck, REQUIRED_CHECKS


def authorized_task():
    spec = TaskSpec.from_dict({
        "repository": "owner/repository", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["tests pass"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["orchestrator/"]},
        "model_policy": "low-cost-first",
    })
    return V3Task("task-1", spec, status=V3Status.AUTHORIZED, audit=["TASK_AUTHORIZED"])


class Reader:
    def __init__(self, task):
        self.task = task

    def get(self, task_id):
        if task_id != self.task.task_id:
            raise ValueError("not found")
        return self.task


class Transaction:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def queue_authorized(self, task, record):
        if self.fail:
            raise RuntimeError("storage unavailable")
        self.calls.append((task, record))


def preflight(passed=True):
    return ExecutionPreflight([
        lambda _, name=name: PreflightCheck(name, passed, "OK" if passed else "BLOCKED")
        for name in REQUIRED_CHECKS
    ])


class DurableQueueServiceTest(unittest.TestCase):
    def test_queues_only_after_full_preflight_and_uses_transaction(self):
        task = authorized_task()
        transaction = Transaction()

        record, report = DurableQueueService(
            Reader(task), preflight(), transaction
        ).request(task.task_id)

        self.assertTrue(report.passed)
        self.assertEqual(task.status, V3Status.EXECUTION_QUEUED)
        self.assertEqual(transaction.calls[0][1].execution_id, record.execution_id)

    def test_failed_preflight_keeps_authorization_unchanged(self):
        task = authorized_task()
        transaction = Transaction()

        with self.assertRaisesRegex(DurableQueueRejected, "execution_preflight_failed:BLOCKED"):
            DurableQueueService(Reader(task), preflight(False), transaction).request(task.task_id)

        self.assertEqual(task.status, V3Status.AUTHORIZED)
        self.assertFalse(transaction.calls)

    def test_transaction_failure_restores_authorized_task(self):
        task = authorized_task()

        with self.assertRaisesRegex(DurableQueueRejected, "execution_queue_not_durable"):
            DurableQueueService(Reader(task), preflight(), Transaction(fail=True)).request(task.task_id)

        self.assertEqual(task.status, V3Status.AUTHORIZED)
        self.assertIsNone(task.execution)
        self.assertEqual(task.audit, ["TASK_AUTHORIZED"])
