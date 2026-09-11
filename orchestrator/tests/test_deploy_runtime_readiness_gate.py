from pathlib import Path
import unittest


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "deploy-orchestrator.yml"


class DeployRuntimeReadinessGateTest(unittest.TestCase):
    def test_deployment_requires_live_provider_and_worker_readiness(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("Verify production execution integrations after deployment", workflow)
        self.assertIn('"$SERVICE_URL/readiness/opencode-go"', workflow)
        self.assertIn('"$SERVICE_URL/readiness/github-worker"', workflow)
        self.assertIn("incident_alerting", workflow)
        self.assertIn('"$opencode")" -gt 0', workflow)
        self.assertIn('"$github_worker")" = "nario0715masa0619-create"', workflow)


if __name__ == "__main__":
    unittest.main()
