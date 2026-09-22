from pathlib import Path
import unittest


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "monitor-approved-devflow-task.yml"


class TaskStatusMonitorWorkflowTest(unittest.TestCase):
    def test_observes_status_every_two_minutes_without_mutation_routes(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("/control-plane/v3/approval-issues/$ISSUE_NUMBER/status", workflow)
        self.assertIn("sleep 120", workflow)
        self.assertNotIn("/authorize", workflow)
        self.assertNotIn("pending_deployments", workflow)


if __name__ == "__main__":
    unittest.main()
