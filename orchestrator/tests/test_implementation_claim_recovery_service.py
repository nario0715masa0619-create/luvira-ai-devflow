import unittest
from datetime import datetime, timedelta, timezone

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from implementation_claim_recovery_service import ImplementationClaimRecoveryService


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def generating_task(progress_at=None):
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40, "requested_action": "implementation",
        "acceptance_criteria": ["test"], "budget": {"max_cost_usd": 1},
        "expiry": "2026-12-01T00:00:00Z", "execution_scope": {"allowed_paths": ["src/"]},
        "model_policy": "low-cost-first:opencode-go",
    })
    record = ExecutionRecord("execution-123", "task", spec.hash, 1,
                             V3Status.IMPLEMENTATION_GENERATING,
                             last_progress_at=progress_at,
                             implementation_claimed_at=progress_at)
    return V3Task("task", spec, V3Status.IMPLEMENTATION_GENERATING, execution=record)


class ImplementationClaimRecoveryServiceTest(unittest.TestCase):
    def service_for(self, task, writes):
        tasks = type("Tasks", (), {"implementation_generating": lambda _: [task]})()
        transaction = type("Tx", (), {
            "record_result": lambda _, current, record, status: writes.append((current.status, record.failure_code, status)),
        })()
        return ImplementationClaimRecoveryService(
            tasks, transaction, no_progress_timeout=timedelta(minutes=30), now=lambda: NOW,
        )

    def test_fresh_progress_remains_active_without_a_provider_replay(self):
        task, writes = generating_task((NOW - timedelta(minutes=29)).isoformat()), []

        result = self.service_for(task, writes).sweep()

        self.assertEqual(result, [("task", "IMPLEMENTATION_PROGRESS_ACTIVE", "execution-123")])
        self.assertEqual(task.status, V3Status.IMPLEMENTATION_GENERATING)
        self.assertEqual(writes, [])

    def test_stalled_claim_is_terminalized_and_requires_a_new_approval(self):
        task, writes = generating_task((NOW - timedelta(minutes=30)).isoformat()), []

        result = self.service_for(task, writes).sweep()

        self.assertEqual(result, [("task", "IMPLEMENTATION_CLAIM_STALLED_FINAL", "execution-123")])
        self.assertEqual(task.status, V3Status.EXECUTION_FAILED_FINAL)
        self.assertEqual(task.execution.failure_code, "IMPLEMENTATION_CLAIM_STALLED_FINAL")
        self.assertEqual(writes, [(V3Status.EXECUTION_FAILED_FINAL, "IMPLEMENTATION_CLAIM_STALLED_FINAL", V3Status.EXECUTION_FAILED_FINAL)])

    def test_legacy_claim_without_a_durable_timestamp_fails_closed(self):
        task, writes = generating_task(), []

        result = self.service_for(task, writes).sweep()

        self.assertEqual(result, [("task", "IMPLEMENTATION_CLAIM_TIME_UNKNOWN_FINAL", "execution-123")])
        self.assertEqual(task.execution.failure_code, "IMPLEMENTATION_CLAIM_TIME_UNKNOWN_FINAL")

    def test_invalid_timeout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timeout_invalid"):
            ImplementationClaimRecoveryService(object(), object(), no_progress_timeout=timedelta(0))
