import unittest
from unittest.mock import Mock

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from v3_task_store import task_payload
from v3_transaction import V3Transaction, V3TransactionError


class V3TransactionTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.task_ref = Mock()
        self.client.collection.return_value.document.return_value = self.task_ref
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
        self.queued.execution = self.record

    def test_updates_authorized_task_with_embedded_execution_in_one_transaction(self):
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(self.original)
        self.task_ref.get.return_value = task_snapshot

        V3Transaction(self.client).queue_authorized(self.queued, self.record)

        self.task_ref.get.assert_called_once_with(transaction=self.transaction)
        self.transaction.get.assert_not_called()
        self.transaction.update.assert_called_once()
        self.transaction.create.assert_not_called()
        self.assertEqual(self.queued.revision, 2)

    def test_rejects_non_authorized_persisted_task_without_writing(self):
        stored = V3Task("task", self.spec, V3Status.AWAITING_HUMAN_APPROVAL)
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.task_ref.get.return_value = task_snapshot

        with self.assertRaisesRegex(V3TransactionError, "v3_task_not_queueable"):
            V3Transaction(self.client).queue_authorized(self.queued, self.record)

        self.transaction.update.assert_not_called()
        self.transaction.create.assert_not_called()

    def test_replaces_only_a_retryable_execution_with_the_next_attempt(self):
        stored = V3Task("task", self.spec, V3Status.EXECUTION_FAILED_RETRYABLE)
        stored.execution = ExecutionRecord(
            "previous-execution", "task", self.spec.hash, 1,
            V3Status.EXECUTION_FAILED_RETRYABLE, failure_code="OPENCODE_TIMEOUT_RETRYABLE",
        )
        retry = V3Task("task", self.spec, V3Status.EXECUTION_QUEUED)
        retry.audit = list(stored.audit) + ["EXECUTION_QUEUED"]
        retry.execution = ExecutionRecord(
            "next-execution", "task", self.spec.hash, 2, V3Status.EXECUTION_QUEUED,
        )
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.task_ref.get.return_value = task_snapshot

        V3Transaction(self.client).queue_authorized(retry, retry.execution)

        self.transaction.update.assert_called_once()
        self.assertEqual(retry.revision, 2)

    def test_rejects_retry_that_does_not_advance_attempt_once(self):
        stored = V3Task("task", self.spec, V3Status.EXECUTION_FAILED_RETRYABLE)
        stored.execution = ExecutionRecord(
            "previous-execution", "task", self.spec.hash, 1,
            V3Status.EXECUTION_FAILED_RETRYABLE, failure_code="OPENCODE_TIMEOUT_RETRYABLE",
        )
        retry = V3Task("task", self.spec, V3Status.EXECUTION_QUEUED)
        retry.execution = ExecutionRecord(
            "next-execution", "task", self.spec.hash, 3, V3Status.EXECUTION_QUEUED,
        )
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.task_ref.get.return_value = task_snapshot

        with self.assertRaisesRegex(V3TransactionError, "retry_not_queueable"):
            V3Transaction(self.client).queue_authorized(retry, retry.execution)

        self.transaction.update.assert_not_called()

    def test_claims_queued_execution_once_before_external_launch(self):
        stored = self.queued
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.task_ref.get.return_value = task_snapshot
        self.queued.status = V3Status.EXECUTION_RUNNING
        self.record.status = V3Status.EXECUTION_RUNNING

        V3Transaction(self.client).begin_queued(self.queued, self.record)

        self.transaction.update.assert_called_once()
        self.assertEqual(self.queued.revision, 2)

    def test_records_identified_worker_only_after_accepted_launch(self):
        identified = V3Task("task", self.spec, V3Status.WORKER_EXECUTION_IDENTIFIED)
        record = ExecutionRecord("execution", "task", self.spec.hash, 1,
                                 V3Status.WORKER_EXECUTION_IDENTIFIED,
                                 external_operation_id="run-1",
                                 launch_operation_id="operations/1")
        identified.execution = record
        stored = V3Task("task", self.spec, V3Status.WORKER_LAUNCH_ACCEPTED)
        stored.execution = ExecutionRecord("execution", "task", self.spec.hash, 1,
                                           V3Status.WORKER_LAUNCH_ACCEPTED,
                                           launch_operation_id="operations/1")
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.task_ref.get.return_value = task_snapshot

        V3Transaction(self.client).record_worker_execution_identified(identified, record)

        self.transaction.update.assert_called_once()
        self.assertEqual(identified.revision, 2)

    def test_records_accepted_launch_as_one_durable_state_transition(self):
        accepted = V3Task("task", self.spec, V3Status.WORKER_LAUNCH_ACCEPTED)
        record = ExecutionRecord("execution", "task", self.spec.hash, 1,
                                 V3Status.WORKER_LAUNCH_ACCEPTED,
                                 launch_operation_id="operations/1")
        accepted.execution = record
        stored = V3Task("task", self.spec, V3Status.EXECUTION_RUNNING)
        stored.execution = ExecutionRecord("execution", "task", self.spec.hash, 1,
                                           V3Status.EXECUTION_RUNNING)
        task_snapshot = Mock(exists=True)
        task_snapshot.to_dict.return_value = task_payload(stored)
        self.task_ref.get.return_value = task_snapshot

        V3Transaction(self.client).record_launch_operation(accepted, record)

        self.transaction.update.assert_called_once()
        self.assertEqual(accepted.revision, 2)
