from pathlib import Path
import unittest


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "authorize-approved-task.yml"


class HumanApprovalWorkflowTest(unittest.TestCase):
    def test_validates_issue_form_before_protected_deployment(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("validate_issue:", workflow)
        self.assertIn("Verify the Issue Form is ready for approval", workflow)
        self.assertIn("needs: validate_issue", workflow)
        self.assertLess(
            workflow.index("validate_issue:"),
            workflow.index("environment: human-approval"),
        )


if __name__ == "__main__":
    unittest.main()
