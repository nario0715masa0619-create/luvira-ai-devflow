import unittest
from unittest.mock import Mock

from google.api_core.exceptions import AlreadyExists

from execution_platform import ExecutionRecord, V3Status
from execution_record_store import (
    ExecutionRecordConflict, FirestoreExecutionRecordStore,
    InMemoryExecutionRecordStore, record_from_payload, record_payload,
)


def record():
    return ExecutionRecord("execution-1", "task-1", "a" * 64, 1, V3Status.EXECUTION_QUEUED, provider="opencode-go")


class ExecutionRecordStoreTest(unittest.TestCase):
    def test_record_payload_is_allow_listed_and_does_not_add_arbitrary_data(self):
        payload = record_payload(record())
        self.assertEqual(set(payload), {"execution_id", "task_id", "spec_hash", "attempt", "status", "provider", "external_operation_id", "failure_code", "stream_events", "last_progress_at", "revision"})
        self.assertEqual(record_from_payload(payload).status, V3Status.EXECUTION_QUEUED)

    def test_memory_store_rejects_duplicate_execution_identity(self):
        store = InMemoryExecutionRecordStore()
        store.create(record())
        with self.assertRaisesRegex(ExecutionRecordConflict, "execution_id_exists"):
            store.create(record())


class FirestoreExecutionRecordStoreTest(unittest.TestCase):
    def setUp(self):
        self.client, self.collection, self.reference = Mock(), Mock(), Mock()
        self.client.collection.return_value = self.collection
        self.collection.document.return_value = self.reference
        self.store = FirestoreExecutionRecordStore(self.client)

    def test_create_uses_execution_id_as_document_id(self):
        self.store.create(record())
        self.collection.document.assert_called_once_with("execution-1")
        self.assertEqual(self.reference.create.call_args.args[0]["task_id"], "task-1")

    def test_duplicate_create_is_never_overwritten(self):
        self.reference.create.side_effect = AlreadyExists("already exists")
        with self.assertRaisesRegex(ExecutionRecordConflict, "execution_id_exists"):
            self.store.create(record())
        self.reference.set.assert_not_called()

    def test_readiness_check_is_read_only(self):
        self.collection.limit.return_value.stream.return_value = []
        self.store.readiness_check()
        self.collection.limit.assert_called_once_with(1)
        self.collection.document.assert_not_called()

    def test_stale_save_is_rejected_without_overwriting_newer_state(self):
        snapshot = Mock(exists=True)
        stored = record_payload(record())
        stored["revision"] = 2
        snapshot.to_dict.return_value = stored
        self.reference.get.return_value = snapshot
        with self.assertRaisesRegex(ExecutionRecordConflict, "stale_execution_record_revision"):
            self.store.save(record())
        self.reference.update.assert_not_called()
