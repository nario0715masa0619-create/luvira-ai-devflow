"""Contract checks for safe, opt-in staging OpenCode credentials."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class StagingOpenCodeSecretGateTests(unittest.TestCase):
    def test_staging_credential_is_opt_in_and_never_uses_production_secret_name(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text(encoding="utf-8")
        self.assertIn('vars.STAGING_OPENCODE_ENABLED }}" = "true"', workflow)
        self.assertIn('vars.STAGING_EXPECTED_REPOSITORY', workflow)
        self.assertIn('dedicated -staging repository', workflow)
        self.assertNotIn('EXPECTED_REPOSITORY=nario0715masa0619-create/luvira-ai-devflow,', workflow)
        self.assertIn('OPENCODE_GO_API_KEY=opencode-go-api-key-staging:latest', workflow)
        self.assertIn('--remove-secrets OPENCODE_GO_API_KEY', workflow)
        self.assertIn("if: vars.STAGING_OPENCODE_ENABLED == 'true'", workflow)
        self.assertIn('"$SERVICE_URL/readiness/opencode-go"', workflow)
        self.assertIn("'.model_count'", workflow)
        self.assertNotIn('opencode-go-api-key-devflow', workflow)

    def test_staging_setup_creates_only_the_staging_secret_and_accessor_binding(self):
        preparation = (ROOT / "scripts" / "prepare-staging-environment.ps1").read_text(encoding="utf-8")
        deployer = (ROOT / "scripts" / "configure-staging-github-deployer.ps1").read_text(encoding="utf-8")
        self.assertIn("'opencode-go-api-key-staging'", preparation)
        self.assertIn("secretmanager.googleapis.com", preparation)
        self.assertIn("'opencode-go-api-key-staging'", deployer)
        self.assertIn("roles/secretmanager.secretAccessor", deployer)
