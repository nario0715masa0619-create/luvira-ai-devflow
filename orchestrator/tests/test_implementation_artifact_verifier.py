import base64
import json
import unittest

from implementation_artifact_verifier import ImplementationArtifactError, SCHEMA, verify_implementation_artifact

ENVELOPE = {"task_id": "task", "spec_hash": "a" * 64, "base_commit": "b" * 40, "allowed_paths": ["src/"]}
DIFF = b"--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n"


def payload(**changes):
    value = {"schema": SCHEMA, "task_id": "task", "spec_hash": "a" * 64, "base_commit": "b" * 40,
             "diff_b64": base64.b64encode(DIFF).decode(), "changed_paths": ["src/example.py"],
             "tests": [{"name": "unit", "status": "passed"}], "publication": "verification-only"}
    value.update(changes)
    return json.dumps(value).encode()


class ImplementationArtifactVerifierTest(unittest.TestCase):
    def test_accepts_only_a_scoped_diff_and_explicit_test_result(self):
        result = verify_implementation_artifact(payload(), ENVELOPE)
        self.assertEqual(result.changed_paths, ("src/example.py",))
        self.assertEqual(result.tests[0]["status"], "passed")

    def test_accepts_an_empty_test_report_for_provider_generated_diffs(self):
        result = verify_implementation_artifact(payload(tests=[]), ENVELOPE)
        self.assertEqual(result.tests, ())

    def test_rejects_path_outside_the_approved_scope(self):
        diff = b"--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n"
        with self.assertRaisesRegex(ImplementationArtifactError, "out_of_scope"):
            verify_implementation_artifact(payload(diff_b64=base64.b64encode(diff).decode(), changed_paths=["docs/x.md"]), ENVELOPE)

    def test_rejects_protected_paths_and_secret_markers(self):
        protected = b"--- a/.github/workflows/x.yml\n+++ b/.github/workflows/x.yml\n@@ -1 +1 @@\n-old\n+new\n"
        with self.assertRaisesRegex(ImplementationArtifactError, "protected"):
            verify_implementation_artifact(payload(diff_b64=base64.b64encode(protected).decode(), changed_paths=[".github/workflows/x.yml"]), ENVELOPE)
        secret = DIFF + b"+ghp_not-a-real-token\n"
        with self.assertRaisesRegex(ImplementationArtifactError, "secret_detected"):
            verify_implementation_artifact(payload(diff_b64=base64.b64encode(secret).decode()), ENVELOPE)

    def test_rejects_declared_paths_that_differ_from_the_diff(self):
        with self.assertRaisesRegex(ImplementationArtifactError, "changed_paths_mismatch"):
            verify_implementation_artifact(payload(changed_paths=["src/other.py"]), ENVELOPE)
