import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from worker_dispatch_service import WorkerDispatchService, WorkerDispatcherError


def queued_task():
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["test"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["src/"]}, "model_policy": "low-cost",
    })
    record = ExecutionRecord("execution", "task", spec.hash, 1, V3Status.EXECUTION_QUEUED)
    return V3Task("task", spec, V3Status.EXECUTION_QUEUED, execution=record)


class WorkerDispatchServiceTest(unittest.TestCase):
    def test_claims_then_starts_then_durably_binds_cloud_run_execution(self):
        task = queued_task()
        calls = []
        transaction = type("Transaction", (), {
            "begin_queued": lambda _, current, record: calls.append(("claim", current.status, record.status)),
            "record_launch_operation": lambda _, current, record: calls.append(("launch", record.launch_operation_id)),
        })()
        worker = type("Worker", (), {"start_operation": lambda _, envelope: calls.append(("start", envelope)) or "operations/123"})()
        service = WorkerDispatchService(type("Tasks", (), {"get": lambda _, __: task})(), transaction, worker)

        self.assertEqual(service.dispatch("task", "execution"), "operations/123")
        self.assertEqual(calls[0], ("claim", V3Status.EXECUTION_RUNNING, V3Status.EXECUTION_RUNNING))
        self.assertEqual(calls[1][0], "start")
        self.assertEqual(calls[2], ("launch", "operations/123"))
        self.assertEqual(task.execution.launch_operation_id, "operations/123")
        self.assertEqual(task.status, V3Status.WORKER_LAUNCH_ACCEPTED)
        self.assertIsNone(task.execution.external_operation_id)

    def test_start_failure_does_not_requeue_or_duplicate_work(self):
        task = queued_task()
        transaction = type("Transaction", (), {"begin_queued": lambda *args: None, "record_launch_operation": lambda *args: None})()
        worker = type("Worker", (), {"start_operation": lambda *_: (_ for _ in ()).throw(TimeoutError())})()
        service = WorkerDispatchService(type("Tasks", (), {"get": lambda _, __: task})(), transaction, worker)

        with self.assertRaisesRegex(WorkerDispatcherError, "outcome_unknown"):
            service.dispatch("task", "execution")
        self.assertEqual(task.status, V3Status.EXECUTION_RUNNING)
        self.assertIsNone(task.execution.external_operation_id)
