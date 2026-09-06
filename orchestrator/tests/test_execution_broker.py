import unittest

from control_plane import ControlPlane, InMemoryTaskStore
from execution_broker import ExecutionBroker, ExecutionBrokerError


def implementation_task():
    control_plane = ControlPlane(InMemoryTaskStore())
    task = control_plane.create_draft(
        {
            "repository": "nario0715masa0619-create/luvira-ai-devflow",
            "base_commit": "0123456789abcdef",
            "task_type": "implementation",
            "requested_action": "implementation",
            "acceptance_criteria": ["focused tests pass"],
            "budget": {"max_cost_usd": 1},
            "execution_scope": {"allowed_paths": ["src/", "tests/test_feature.py"]},
        },
        "intake",
        task_id="github-issue-99-aaaaaaaaaaaaaaaa",
    )
    control_plane.validate(task.task_id, "validator")
    waiting = control_plane.request_human_approval(task.task_id, "governance")
    control_plane.authorize(task.task_id, "human@example.test", waiting.approval_binding)
    return control_plane.start_execution(task.task_id, "broker")


class ExecutionBrokerTest(unittest.TestCase):
    def test_prepares_credential_free_diff_only_envelope_for_authorized_task(self):
        envelope = ExecutionBroker().prepare(implementation_task())

        self.assertEqual(envelope.repository, "nario0715masa0619-create/luvira-ai-devflow")
        self.assertEqual(envelope.allowed_paths, ("src/", "tests/test_feature.py"))
        self.assertEqual(envelope.worker_permissions["github"], "none")
        self.assertEqual(envelope.worker_permissions["gcp"], "none")
        self.assertEqual(envelope.worker_permissions["secrets"], "none")
        self.assertEqual(envelope.publication, "verification-artifact-only")

    def test_rejects_protected_or_unsafe_paths(self):
        task = implementation_task()
        with self.assertRaisesRegex(ExecutionBrokerError, "allowed_path_protected"):
            task.spec["execution_scope"] = {"allowed_paths": [".github/workflows/worker.yml"]}
            ExecutionBroker().prepare(task)
        with self.assertRaisesRegex(ExecutionBrokerError, "allowed_path_invalid"):
            task.spec["execution_scope"] = {"allowed_paths": ["../secrets.txt"]}
            ExecutionBroker().prepare(task)
        with self.assertRaisesRegex(ExecutionBrokerError, "allowed_path_protected"):
            task.spec["execution_scope"] = {"allowed_paths": ["notes/private.key"]}
            ExecutionBroker().prepare(task)

    def test_rejects_a_non_implementation_authorization(self):
        task = implementation_task()
        task.spec["requested_action"] = "read"
        with self.assertRaisesRegex(ExecutionBrokerError, "requested_action_not_implementation"):
            ExecutionBroker().prepare(task)
