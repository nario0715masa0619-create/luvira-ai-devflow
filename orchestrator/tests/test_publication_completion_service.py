import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from publication_completion_service import PublicationCompletionService


def published_task():
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40, "requested_action": "implementation",
        "acceptance_criteria": ["x"], "budget": {"max_cost_usd": 1},
        "expiry": "2027-01-01T00:00:00Z", "execution_scope": {"allowed_paths": ["src/"]},
        "model_policy": "low-cost",
    })
    record = ExecutionRecord("execution", "task", spec.hash, 1, V3Status.PUBLISHED,
                             publication_url="https://github.com/a/b/pull/1")
    return V3Task("task", spec, V3Status.PUBLISHED, execution=record)


class PublicationCompletionServiceTest(unittest.TestCase):
    def test_persists_confirmed_merge_once(self):
        task, writes = published_task(), []
        tasks = type("Tasks", (), {"published": lambda _: [task]})()
        transaction = type("Tx", (), {"record_merge": lambda _, current, record: writes.append(record.execution_id)})()
        publisher = type("Publisher", (), {"pull_state": lambda *_: "MERGED"})()

        outcomes = PublicationCompletionService(tasks, transaction, lambda _: publisher).sweep()

        self.assertEqual(outcomes, [("task", "MERGED", "execution")])
        self.assertEqual(task.status, V3Status.MERGED)
        self.assertEqual(writes, ["execution"])

    def test_open_pull_remains_published_without_a_write(self):
        task = published_task()
        tasks = type("Tasks", (), {"published": lambda _: [task]})()
        transaction = object()
        publisher = type("Publisher", (), {"pull_state": lambda *_: "OPEN"})()

        outcomes = PublicationCompletionService(tasks, transaction, lambda _: publisher).sweep()

        self.assertEqual(outcomes, [("task", "PUBLICATION_AWAITING_MERGE", "execution")])
        self.assertEqual(task.status, V3Status.PUBLISHED)

    def test_recovers_a_legacy_publication_url_before_merging(self):
        task, writes = published_task(), []
        task.execution.publication_url = None
        tasks = type("Tasks", (), {"published": lambda _: [task]})()
        transaction = type("Tx", (), {"record_merge": lambda _, current, record: writes.append(record.publication_url)})()
        publisher = type("Publisher", (), {
            "publication_url_for": lambda *_: "https://github.com/a/b/pull/1",
            "pull_state": lambda *_: "MERGED",
        })()

        PublicationCompletionService(tasks, transaction, lambda _: publisher).sweep()

        self.assertEqual(writes, ["https://github.com/a/b/pull/1"])
