import base64
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import isolated_worker


def valid_envelope():
    return {
        "task_id": "github-issue-99-aaaaaaaaaaaaaaaa",
        "spec_hash": "a" * 64,
        "repository": "nario0715masa0619-create/luvira-ai-devflow",
        "base_commit": "0123456789abcdef",
        "task_type": "implementation",
        "acceptance_criteria": ["focused tests pass"],
        "max_cost_usd": 1.0,
        "allowed_paths": ["src/"],
        "worker_permissions": dict(isolated_worker.EXPECTED_PERMISSIONS),
        "publication": "verification-artifact-only",
    }


class IsolatedWorkerTest(unittest.TestCase):
    def test_emits_only_a_no_model_bootstrap_artifact(self):
        envelope = valid_envelope()
        encoded = base64.b64encode(json.dumps(envelope).encode()).decode()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.dict("os.environ", {"WORKER_ENVELOPE_B64": encoded, "WORKER_OUTPUT_PATH": str(output)}, clear=True), contextlib.redirect_stdout(io.StringIO()) as stdout:
                isolated_worker.main()
            artifact = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(artifact["status"], "ENVELOPE_VALIDATED_NO_MODEL_EXECUTION")
        self.assertEqual(artifact["changed_paths"], [])
        self.assertEqual(artifact["publication"], "none")
        self.assertTrue(stdout.getvalue().startswith("LUVIRA_BOOTSTRAP_ARTIFACT_B64="))

    def test_rejects_extra_credential_material(self):
        envelope = valid_envelope()
        envelope["api_key"] = "must-not-enter-worker"
        with self.assertRaisesRegex(isolated_worker.WorkerEnvelopeError, "envelope_schema_mismatch"):
            isolated_worker.validate_envelope(envelope)

    def test_rejects_any_permission_or_publication_expansion(self):
        envelope = valid_envelope()
        envelope["worker_permissions"]["github"] = "write"
        with self.assertRaisesRegex(isolated_worker.WorkerEnvelopeError, "worker_permissions_not_isolated"):
            isolated_worker.validate_envelope(envelope)

        envelope = valid_envelope()
        envelope["publication"] = "create-pull-request"
        with self.assertRaisesRegex(isolated_worker.WorkerEnvelopeError, "publication_not_isolated"):
            isolated_worker.validate_envelope(envelope)
