import unittest
from unittest.mock import Mock

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from v3_task_store import task_payload
from v3_transaction import V3Transaction, V3TransactionError


class V3TransactionTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.task_ref, self.record_ref = Mock(), Mock()
        self.client.collection.return_value.document.side_effect = [self.task_ref, self.record_ref]
        self.client.transactional = lambda function: function
        self.transaction = Mock()
        self.client.transaction.return_value = self.transaction
        self.spec = TaskSpec.from_dict({
            "repository": "a/b", "base_commit": "a" * 40,
            "requested_action": "implementation", "acceptance_criteria": ["x"],
            "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
            "execution_scope": {"allowed_paths": ["src/"]}, "model_policy": "low-cost",
        })
        self.original = V3Task("task", self.spec, V3Status.AUTHORIZED)
        self.queued = V3Task("task", self.spec, V3Status.EXECUTION_QUEUED)
        self.queued.audit.append("EXECUTION_QUEUED")
        self.record = ExecutionRecord(
            "execution", "task", self.spec.hash, 1, V3Status.EXECUTION_QUEUED
        )

    def test_updates_authorized_task_and_creates_record_in_one_transaction(self):
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(self.original)
        self.transaction.get.side_effect = [task_snapshot, Mock(exists=False)]

        V3Transaction(self.client).queue_authorized(self.queued, self.record)

        self.transaction.update.assert_called_once()
        self.transaction.create.assert_called_once()
        self.assertEqual(self.queued.revision, 2)

    def test_rejects_non_authorized_persisted_task_without_writing(self):
        stored = V3Task("task", self.spec, V3Status.AWAITING_HUMAN_APPROVAL)
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.transaction.get.return_value = task_snapshot

        with self.assertRaisesRegex(V3TransactionError, "v3_task_not_authorized"):
            V3Transaction(self.client).queue_authorized(self.queued, self.record)

        self.transaction.update.assert_not_called()
        self.transaction.create.assert_not_called()
