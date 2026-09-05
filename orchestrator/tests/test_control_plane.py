import unittest

from control_plane import ControlPlane, InMemoryTaskStore, TaskConflict, TaskStatus, spec_hash


def valid_spec():
    return {
        "repository": "nario0715masa0619-create/luvira-ai-devflow",
        "base_commit": "0123456789abcdef",
        "task_type": "implementation",
        "acceptance_criteria": ["tests pass"],
        "budget": {"max_cost_usd": 2},
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

    def test_duplicate_task_id_and_impossible_transition_are_rejected(self):
        task = self.control_plane.create_draft(valid_spec(), "intake", task_id="task-1")
        with self.assertRaisesRegex(TaskConflict, "task_id_already_exists"):
            self.control_plane.create_draft(valid_spec(), "intake", task_id="task-1")
        with self.assertRaisesRegex(TaskConflict, "invalid_transition_from_DRAFT"):
            self.control_plane.authorize(task.task_id, "human@example.test", "anything")
