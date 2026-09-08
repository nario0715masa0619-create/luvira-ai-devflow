import unittest
from autonomous_broker import AutonomousBroker
from execution_platform import ExecutionPlatformError, V3Status


class Task:
    def __init__(self, status): self.task_id = "t"; self.status = status


class AutonomousBrokerTest(unittest.TestCase):
    def test_failure_never_revokes_authorization(self):
        task = Task(V3Status.AUTHORIZED)
        tasks = type("Tasks", (), {"eligible": lambda _: [task]})()
        queue = type("Queue", (), {"request": lambda *_: (_ for _ in ()).throw(TimeoutError())})()
        outcome = AutonomousBroker(tasks, queue).sweep()
        self.assertEqual(task.status, V3Status.AUTHORIZED)
        self.assertEqual(outcome[0][1], "RETRY_PENDING")

    def test_safe_domain_code_is_retained_for_operational_diagnosis(self):
        task = Task(V3Status.AUTHORIZED)
        tasks = type("Tasks", (), {"eligible": lambda _: [task]})()
        queue = type("Queue", (), {"request": lambda *_: (_ for _ in ()).throw(ExecutionPlatformError("execution_preflight_failed:WORKER_JOB_UNAVAILABLE"))})()

        outcome = AutonomousBroker(tasks, queue).sweep()

        self.assertEqual(outcome, [("t", "RETRY_PENDING", "execution_preflight_failed:WORKER_JOB_UNAVAILABLE")])

    def test_starts_worker_after_durable_queue_admission(self):
        task = Task(V3Status.AUTHORIZED)
        tasks = type("Tasks", (), {"eligible": lambda _: [task]})()
        queue = type("Queue", (), {"request": lambda *_: (type("Record", (), {"execution_id": "internal"})(), None)})()
        dispatcher = type("Dispatcher", (), {"dispatch": lambda *_: "cloud-run-1"})()
        outcome = AutonomousBroker(tasks, queue, dispatcher).sweep()
        self.assertEqual(outcome, [("t", "WORKER_STARTED", "cloud-run-1")])
