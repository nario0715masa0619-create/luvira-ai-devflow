import unittest
from unittest.mock import Mock

from v3_task_store import FirestoreV3TaskStore, V3TaskStoreError, task_from_payload, task_payload
from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task


def task():
    return V3Task("task-1", TaskSpec.from_dict({"repository":"a/b", "base_commit":"a" * 40, "requested_action":"implementation", "acceptance_criteria":["tests"], "budget":{"max_cost_usd":1}, "expiry":"2026-12-01T00:00:00Z", "execution_scope":{"allowed_paths":["src/"]}, "model_policy":"low-cost-first"}), audit=["TASK_DRAFTED"])


class V3TaskStoreTest(unittest.TestCase):
    def setUp(self):
        self.client, self.collection, self.reference = Mock(), Mock(), Mock()
        self.client.collection.return_value = self.collection
        self.collection.document.return_value = self.reference
        self.store = FirestoreV3TaskStore(self.client)

    def test_payload_round_trip_binds_the_immutable_spec_hash(self):
        payload = task_payload(task())
        self.assertEqual(task_from_payload(payload).spec.hash, payload["spec_hash"])
        payload["spec_hash"] = "bad"
        with self.assertRaisesRegex(V3TaskStoreError, "integrity"):
            task_from_payload(payload)

    def test_create_uses_one_task_document_with_its_current_execution(self):
        value = task()
        value.execution = ExecutionRecord("execution-1", value.task_id, value.spec.hash, 1, V3Status.EXECUTION_QUEUED)
        self.store.create(value)
        self.collection.document.assert_called_once_with("task-1")
        self.assertEqual(self.reference.create.call_args.args[0]["execution"]["execution_id"], "execution-1")

    def test_stale_save_is_rejected_without_update(self):
        snapshot = Mock(exists=True)
        payload = task_payload(task()); payload["revision"] = 2
        snapshot.to_dict.return_value = payload
        self.reference.get.return_value = snapshot
        with self.assertRaisesRegex(V3TaskStoreError, "stale_revision"):
            self.store.save(task())
        self.reference.update.assert_not_called()

    def test_invalid_terminal_record_does_not_block_a_valid_record(self):
        invalid, valid = Mock(), Mock()
        invalid.id, valid.id = "invalid", "valid"
        broken = task_payload(task()); broken["spec_hash"] = "broken"
        invalid.to_dict.return_value = broken
        valid.to_dict.return_value = task_payload(task())
        query = Mock(); query.stream.return_value = [invalid, valid]
        self.collection.where.return_value = query

        records = list(self.store.artifact_verified())

        self.assertEqual([record.task_id for record in records], ["task-1"])
