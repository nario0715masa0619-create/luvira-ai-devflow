from pathlib import Path
import subprocess
import sys
import unittest


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "authorize-project-onboarding.yml"


class ProjectProvisioningWorkflowTest(unittest.TestCase):
    def test_uses_two_distinct_protected_boundaries(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("environment: human-approval", workflow)
        self.assertIn("environment: project-provisioning", workflow)
        self.assertLess(workflow.index("environment: human-approval"), workflow.index("environment: project-provisioning"))

    def test_never_uses_the_ordinary_worker_credential(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("secrets.PROJECT_PROVISIONING_TOKEN", workflow)
        self.assertIn("providers/github-project-provisioning", workflow)
        self.assertIn("devflow-project-provisioner@luvira-ai-control-plane.iam.gserviceaccount.com", workflow)
        self.assertIn("/claim-provisioning", workflow)
        self.assertIn("/complete-provisioning", workflow)
        self.assertIn("/fail-provisioning", workflow)
        self.assertNotIn("GITHUB_APP_PRIVATE_KEY", workflow)
        self.assertNotIn("GITHUB_WORKER", workflow)
        self.assertNotIn("OPENCODE", workflow)

    def test_identity_contract_matches_workflow(self):
        root = Path(__file__).parents[2]
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "verify_project_provisioning_identity.py")],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
