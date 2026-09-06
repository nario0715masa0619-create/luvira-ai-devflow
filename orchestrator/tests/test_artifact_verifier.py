import json
import unittest

import artifact_verifier
from test_isolated_worker import valid_envelope


def bootstrap_artifact():
    envelope = valid_envelope()
    return {
        "schema": "luvira.devflow.worker-bootstrap.v1",
        "task_id": envelope["task_id"],
        "spec_hash": envelope["spec_hash"],
        "base_commit": envelope["base_commit"],
        "status": "ENVELOPE_VALIDATED_NO_MODEL_EXECUTION",
        "changed_paths": [],
        "tests": [],
        "publication": "none",
    }


class ArtifactVerifierTest(unittest.TestCase):
    def test_accepts_only_the_expected_bootstrap_artifact(self):
        envelope = valid_envelope()
        verified = artifact_verifier.verify_artifact(json.dumps(bootstrap_artifact()).encode(), envelope)

        self.assertEqual(verified.task_id, envelope["task_id"])
        self.assertEqual(verified.changed_paths, ())

    def test_rejects_artifact_for_another_task(self):
        artifact = bootstrap_artifact()
        artifact["task_id"] = "github-issue-100-bbbbbbbbbbbbbbbb"

        with self.assertRaisesRegex(artifact_verifier.ArtifactVerificationError, "artifact_task_id_mismatch"):
            artifact_verifier.verify_artifact(json.dumps(artifact).encode(), valid_envelope())

    def test_rejects_any_change_or_publication_attempt(self):
        artifact = bootstrap_artifact()
        artifact["changed_paths"] = ["src/unreviewed.py"]
        with self.assertRaisesRegex(artifact_verifier.ArtifactVerificationError, "artifact_changes_not_allowed"):
            artifact_verifier.verify_artifact(json.dumps(artifact).encode(), valid_envelope())

        artifact = bootstrap_artifact()
        artifact["publication"] = "create-pull-request"
        with self.assertRaisesRegex(artifact_verifier.ArtifactVerificationError, "artifact_publication_forbidden"):
            artifact_verifier.verify_artifact(json.dumps(artifact).encode(), valid_envelope())

    def test_rejects_duplicate_json_keys(self):
        payload = b'{"schema":"luvira.devflow.worker-bootstrap.v1","schema":"other"}'
        with self.assertRaisesRegex(artifact_verifier.ArtifactVerificationError, "artifact_duplicate_key"):
            artifact_verifier.verify_artifact(payload, valid_envelope())
