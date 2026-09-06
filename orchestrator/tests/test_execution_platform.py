import unittest

from execution_platform import (
    ExecutionPlatform, PreflightRejected, TaskSpec, TransitionRejected,
    V3Status, classify_external_failure,
)


def spec():
    return TaskSpec.from_dict({
        "repository": "nario0715masa0619-create/luvira-ai-devflow",
        "base_commit": "a" * 40, "requested_action": "implementation",
        "acceptance_criteria": ["tests pass"], "budget": {"max_cost_usd": 1},
        "expiry": "2026-12-01T00:00:00Z", "execution_scope": {"allowed_paths": ["orchestrator/"]},
        "model_policy": "low-cost-first",
    })


class ExecutionPlatformTest(unittest.TestCase):
    def authorized(self):
        platform = ExecutionPlatform()
        task = platform.create(spec(), "task-1")
        platform.validate(task.task_id)
        waiting = platform.request_approval(task.task_id)
        platform.authorize(task.task_id, waiting.approval_binding)
        return platform, task

    def test_retry_reuses_approval_but_never_reuses_execution_identity(self):
        platform, task = self.authorized()
        first = platform.queue(task.task_id, [True] * 5)
        platform.begin(task.task_id, first.execution_id)
        platform.fail(task.task_id, first.execution_id, 429)
        second = platform.queue(task.task_id, [True] * 5)
        self.assertEqual(second.attempt, 2)
        self.assertNotEqual(first.execution_id, second.execution_id)
        self.assertEqual(task.status, V3Status.EXECUTION_QUEUED)

    def test_preflight_failure_does_not_start_or_consume_authorization(self):
        platform, task = self.authorized()
        with self.assertRaisesRegex(PreflightRejected, "execution_preflight_failed"):
            platform.queue(task.task_id, [True, False])
        self.assertEqual(task.status, V3Status.AUTHORIZED)
        self.assertIsNone(task.execution)

    def test_mutated_scope_breaks_approval_binding(self):
        platform = ExecutionPlatform()
        task = platform.create(spec(), "task-1")
        platform.validate(task.task_id)
        waiting = platform.request_approval(task.task_id)
        object.__setattr__(task.spec, "allowed_paths", (".github/workflows/",))
        with self.assertRaisesRegex(TransitionRejected, "approval_binding_mismatch"):
            platform.authorize(task.task_id, waiting.approval_binding)

    def test_external_failures_are_classified_without_response_body(self):
        self.assertEqual(classify_external_failure(403), "HTTP_403_FINAL")
        self.assertEqual(classify_external_failure(409), "HTTP_409_RETRYABLE")
        self.assertEqual(classify_external_failure(None, timeout=True), "EXTERNAL_TIMEOUT_RETRYABLE")

    def test_wrong_execution_cannot_change_task_state(self):
        platform, task = self.authorized()
        execution = platform.queue(task.task_id, [True])
        with self.assertRaisesRegex(TransitionRejected, "execution_identity_mismatch"):
            platform.begin(task.task_id, "other")
        self.assertEqual(task.status, V3Status.EXECUTION_QUEUED)
