import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from v3_worker_envelope import V3WorkerEnvelopeError, from_running_task


def task(status=V3Status.EXECUTION_RUNNING):
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["test"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["src/"]}, "model_policy": "low-cost",
        "approval_context": {"task_type": "implementation"},
    })
    execution = ExecutionRecord("execution", "task", spec.hash, 1, status)
    return V3Task("task", spec, status, execution=execution)


class V3WorkerEnvelopeTest(unittest.TestCase):
    def test_only_immutable_approved_fields_cross_to_worker(self):
        envelope = from_running_task(task())
        self.assertEqual(envelope["task_id"], "task")
        self.assertNotIn("approval_context", envelope)
        self.assertNotIn("api_key", envelope)
        self.assertEqual(envelope["worker_permissions"]["github"], "none")

    def test_unclaimed_execution_cannot_cross_boundary(self):
        with self.assertRaisesRegex(V3WorkerEnvelopeError, "not_running"):
            from_running_task(task(V3Status.EXECUTION_QUEUED))
