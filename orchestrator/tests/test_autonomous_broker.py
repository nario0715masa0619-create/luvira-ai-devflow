import unittest
from autonomous_broker import AutonomousBroker
from execution_platform import V3Status


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
