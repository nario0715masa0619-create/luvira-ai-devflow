import unittest

from v3_approval_projection import V3ApprovalProjectionError, ensure_projected, project_authorized
from v3_task_store import V3TaskStoreError


class LegacyTask:
    task_id = "github-issue-1-aaaaaaaaaaaaaaaa"
    approval_binding = "b" * 64
    spec = {
        "repository": "owner/repository", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["tests pass"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["src/"]},
    }


class Store:
    def __init__(self):
        self.value = None

    def create(self, value):
        if self.value:
            raise V3TaskStoreError("v3_task_exists")
        self.value = value

    def get(self, _):
        return self.value


class V3ApprovalProjectionTest(unittest.TestCase):
    def test_projects_exact_authorized_snapshot_once(self):
        task = ensure_projected(Store(), LegacyTask(), "low-cost-first:opencode-go")
        self.assertEqual(task.spec.repository, "owner/repository")
        self.assertEqual(task.approval_binding, "b" * 64)

    def test_rejects_reusing_task_id_for_a_changed_snapshot(self):
        store = Store()
        ensure_projected(store, LegacyTask(), "low-cost-first:opencode-go")
        changed = LegacyTask()
        changed.spec = {**LegacyTask.spec, "base_commit": "c" * 40}
        with self.assertRaisesRegex(V3ApprovalProjectionError, "v3_approval_projection_conflict"):
            ensure_projected(store, changed, "low-cost-first:opencode-go")
