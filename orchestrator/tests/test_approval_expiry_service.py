import unittest
from datetime import datetime, timezone

from approval_expiry_service import ApprovalExpiryService
from execution_platform import ExecutionPlatform, TaskSpec, V3Status


def waiting_task(task_id, expiry):
    platform = ExecutionPlatform()
    task = platform.create(TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40,
        "requested_action": "read", "acceptance_criteria": ["x"],
        "budget": {"max_cost_usd": 1}, "expiry": expiry,
        "execution_scope": {"allowed_paths": ["README.md"]}, "model_policy": "none",
    }), task_id)
    platform.validate(task_id)
    return platform.request_approval(task_id)


class ApprovalExpiryServiceTest(unittest.TestCase):
    def test_expires_only_past_waiting_approvals_and_keeps_future_ones(self):
        expired = waiting_task("expired", "2026-01-01T00:00:00Z")
        future = waiting_task("future", "2027-01-01T00:00:00Z")
        writes = []
        tasks = type("Tasks", (), {
            "awaiting_human_approval": lambda _: [expired, future],
            "save": lambda _, task: writes.append(task.task_id),
        })()

        outcomes = ApprovalExpiryService(tasks, clock=lambda: datetime(2026, 2, 1, tzinfo=timezone.utc)).sweep()

        self.assertEqual(outcomes, [("expired", "APPROVAL_EXPIRED", "deadline")])
        self.assertEqual(writes, ["expired"])
        self.assertEqual(expired.status, V3Status.EXPIRED)
        self.assertEqual(future.status, V3Status.AWAITING_HUMAN_APPROVAL)
