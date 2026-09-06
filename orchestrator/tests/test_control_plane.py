import unittest
from unittest.mock import Mock

from google.cloud import firestore

from control_plane import ControlPlane, FirestoreTaskStore, InMemoryTaskStore, TaskConflict, TaskStatus, spec_hash


def valid_spec():
    return {
        "repository": "nario0715masa0619-create/luvira-ai-devflow",
        "base_commit": "0123456789abcdef",
        "task_type": "implementation",
        "acceptance_criteria": ["tests pass"],
        "budget": {"max_cost_usd": 2},
        "execution_scope": {"allowed_paths": ["src/"]},
    }


class ControlPlaneTest(unittest.TestCase):
    def setUp(self):
        self.control_plane = ControlPlane(InMemoryTaskStore())

    def test_spec_hash_is_stable_across_key_order(self):
        self.assertEqual(spec_hash(valid_spec()), spec_hash(dict(reversed(list(valid_spec().items())))))

    def test_invalid_spec_is_rejected_with_audit_record(self):
        task = self.control_plane.create_draft({"repository": "not-a-repository"}, "intake")
        task = self.control_plane.validate(task.task_id, "validator")
        self.assertEqual(task.status, TaskStatus.REJECTED)
        self.assertEqual(task.audit_events[-1].event, "TASK_REJECTED")

    def test_authorization_binds_human_approval_to_immutable_snapshot(self):
        task = self.control_plane.create_draft(valid_spec(), "intake")
        self.control_plane.validate(task.task_id, "validator")
        waiting = self.control_plane.request_human_approval(task.task_id, "governance")
        authorized = self.control_plane.authorize(task.task_id, "human@example.test", waiting.approval_binding)
        self.assertEqual(authorized.status, TaskStatus.AUTHORIZED)
        self.assertEqual(authorized.approved_by, "human@example.test")
        self.assertEqual(authorized.audit_events[-1].event, "TASK_AUTHORIZED")

    def test_changed_snapshot_cannot_be_authorized(self):
        task = self.control_plane.create_draft(valid_spec(), "intake")
        self.control_plane.validate(task.task_id, "validator")
        waiting = self.control_plane.request_human_approval(task.task_id, "governance")
        task.spec["base_commit"] = "changed"
        with self.assertRaisesRegex(TaskConflict, "task_snapshot_changed"):
            self.control_plane.authorize(task.task_id, "human@example.test", waiting.approval_binding)

    def test_execution_failure_is_retryable_without_reusing_human_approval(self):
        task = self.control_plane.create_draft(valid_spec(), "intake")
        self.control_plane.validate(task.task_id, "validator")
        waiting = self.control_plane.request_human_approval(task.task_id, "governance")
        self.control_plane.authorize(task.task_id, "human@example.test", waiting.approval_binding)
        running = self.control_plane.start_execution(task.task_id, "broker")
        self.assertEqual(running.status, TaskStatus.EXECUTION_RUNNING)
        failed = self.control_plane.fail_execution(task.task_id, "broker", "transport_error")

        self.assertEqual(failed.status, TaskStatus.EXECUTION_FAILED_RETRYABLE)
        retry = self.control_plane.start_execution(task.task_id, "broker")
        self.assertEqual(retry.status, TaskStatus.EXECUTION_RUNNING)
        self.assertEqual(retry.execution_attempts, 2)

    def test_duplicate_task_id_and_impossible_transition_are_rejected(self):
        task = self.control_plane.create_draft(valid_spec(), "intake", task_id="task-1")
        with self.assertRaisesRegex(TaskConflict, "task_id_already_exists"):
            self.control_plane.create_draft(valid_spec(), "intake", task_id="task-1")
        with self.assertRaisesRegex(TaskConflict, "invalid_transition_from_DRAFT"):
            self.control_plane.authorize(task.task_id, "human@example.test", "anything")


class FirestoreTaskStoreTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.collection = Mock()
        self.client.collection.return_value = self.collection
        self.store = FirestoreTaskStore(self.client)

    def test_create_uses_task_id_document_and_primitive_payload(self):
        task = ControlPlane(InMemoryTaskStore()).create_draft(valid_spec(), "intake", task_id="task-1")
        reference = Mock()
        self.collection.document.return_value = reference

        self.store.create(task)

        self.collection.document.assert_called_once_with("task-1")
        payload = reference.create.call_args.args[0]
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["status"], "DRAFT")
        self.assertEqual(payload["revision"], 1)

    def test_stale_writer_is_rejected_before_firestore_update(self):
        task = ControlPlane(InMemoryTaskStore()).create_draft(valid_spec(), "intake", task_id="task-1")
        task.revision = 1
        reference = Mock()
        snapshot = Mock(exists=True)
        newer = task.storage_dict()
        newer["revision"] = 2
        snapshot.to_dict.return_value = newer
        reference.get.return_value = snapshot
        self.collection.document.return_value = reference
        with self.assertRaisesRegex(TaskConflict, "stale_task_revision"):
            self.store.save(task)

        reference.update.assert_not_called()

    def test_save_uses_firestore_update_time_as_an_optimistic_lock(self):
        task = ControlPlane(InMemoryTaskStore()).create_draft(valid_spec(), "intake", task_id="task-1")
        reference = Mock()
        snapshot = Mock(exists=True, update_time=Mock())
        snapshot.to_dict.return_value = task.storage_dict()
        reference.get.return_value = snapshot
        self.collection.document.return_value = reference

        self.store.save(task)

        self.assertEqual(task.revision, 2)
        payload = reference.update.call_args.args[0]
        self.assertEqual(payload["revision"], 2)
        self.assertIsInstance(reference.update.call_args.kwargs["option"], firestore.LastUpdateOption)

    def test_readiness_check_is_read_only(self):
        self.collection.limit.return_value.stream.return_value = []
        self.store.readiness_check()

        self.collection.limit.assert_called_once_with(1)
        self.collection.limit.return_value.stream.assert_called_once_with()
