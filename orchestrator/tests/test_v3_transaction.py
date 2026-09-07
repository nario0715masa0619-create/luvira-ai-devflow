import unittest
from unittest.mock import Mock

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from v3_transaction import V3Transaction, V3TransactionError


class V3TransactionTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.collection.return_value.document.return_value = Mock()
        self.client.transactional = lambda function: function
        self.transaction = Mock()
        self.transaction.get.return_value = Mock(exists=False)
        self.client.transaction.return_value = self.transaction
        spec = TaskSpec.from_dict({
            "repository": "a/b", "base_commit": "a" * 40,
            "requested_action": "implementation", "acceptance_criteria": ["x"],
            "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
            "execution_scope": {"allowed_paths": ["src/"]}, "model_policy": "low-cost",
        })
        self.task = V3Task("task", spec, V3Status.EXECUTION_QUEUED)
        self.record = ExecutionRecord(
            "execution", "task", spec.hash, 1, V3Status.EXECUTION_QUEUED
        )

    def test_requires_one_transaction_for_both_documents(self):
        V3Transaction(self.client).create_queued(self.task, self.record)

        self.assertEqual(self.transaction.create.call_count, 2)
        self.assertEqual(self.transaction.get.call_count, 2)

    def test_rejects_invalid_task_record_binding_before_writing(self):
        self.record.task_id = "other-task"

        with self.assertRaisesRegex(V3TransactionError, "task_record_binding_invalid"):
            V3Transaction(self.client).create_queued(self.task, self.record)

        self.transaction.create.assert_not_called()
