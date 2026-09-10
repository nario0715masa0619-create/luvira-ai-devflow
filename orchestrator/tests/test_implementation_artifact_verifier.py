import base64
import json
import unittest

from implementation_artifact_verifier import ImplementationArtifactError, MODEL_SCHEMA, SCHEMA, verify_implementation_artifact
from github_verified_publisher import parse_verified_diff

ENVELOPE = {"task_id": "task", "spec_hash": "a" * 64, "base_commit": "b" * 40, "allowed_paths": ["src/"]}
DIFF = b"--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n"


def payload(**changes):
    value = {"schema": SCHEMA, "task_id": "task", "spec_hash": "a" * 64, "base_commit": "b" * 40,
             "diff_b64": base64.b64encode(DIFF).decode(), "changed_paths": ["src/example.py"],
             "tests": [{"name": "unit", "status": "passed"}], "publication": "verification-only"}
    value.update(changes)
    return json.dumps(value).encode()


def model_payload(**changes):
    value = {"schema": MODEL_SCHEMA, "task_id": "task", "spec_hash": "a" * 64, "base_commit": "b" * 40,
             "files": [{"path": "src/example.py", "content": "new\n"}], "changed_paths": ["src/example.py"],
             "tests": [], "publication": "verification-only"}
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

    def test_normalizes_an_unambiguous_new_file_masquerading_as_existing(self):
        new_file = b"--- a/src/new.py\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+created\n"
        result = verify_implementation_artifact(
            payload(diff_b64=base64.b64encode(new_file).decode(), changed_paths=["src/new.py"]),
            ENVELOPE,
            baseline_paths=("src/existing.py",), baseline_files=(("src/existing.py", b"old\n"),),
        )
        self.assertTrue(result.diff.startswith(b"--- /dev/null\n+++ b/src/new.py\n"))

    def test_rejects_ambiguous_base_mismatch_without_rewriting_it(self):
        invalid = b"--- a/src/new.py\n+++ b/src/new.py\n@@ -1 +1 @@\n-old\n+new\n"
        with self.assertRaisesRegex(ImplementationArtifactError, "diff_base_mismatch"):
            verify_implementation_artifact(
                payload(diff_b64=base64.b64encode(invalid).decode(), changed_paths=["src/new.py"]),
                ENVELOPE, baseline_paths=("src/existing.py",),
            )

    def test_accepts_a_new_file_only_with_dev_null_header(self):
        new_file = b"--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+created\n"
        result = verify_implementation_artifact(
            payload(diff_b64=base64.b64encode(new_file).decode(), changed_paths=["src/new.py"]),
            ENVELOPE,
            baseline_paths=("src/existing.py",),
        )
        self.assertEqual(result.changed_paths, ("src/new.py",))

    def test_parses_a_verified_new_file_for_publication(self):
        patches = parse_verified_diff(b"--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+created\n")
        self.assertEqual((patches[0].old_path, patches[0].new_path), (None, "src/new.py"))

    def test_rejects_a_hunk_that_does_not_apply_to_the_immutable_base(self):
        with self.assertRaisesRegex(ImplementationArtifactError, "diff_context_mismatch"):
            verify_implementation_artifact(
                payload(), ENVELOPE, baseline_paths=("src/example.py",),
                baseline_files=(("src/example.py", b"different\n"),),
            )

    def test_accepts_a_hunk_that_applies_to_the_immutable_base(self):
        result = verify_implementation_artifact(
            payload(), ENVELOPE, baseline_paths=("src/example.py",),
            baseline_files=(("src/example.py", b"old\n"),),
        )
        self.assertEqual(result.changed_paths, ("src/example.py",))

    def test_deterministically_builds_an_applicable_diff_from_full_file_content(self):
        result = verify_implementation_artifact(
            model_payload(), ENVELOPE,
            baseline_paths=("src/example.py",), baseline_files=(("src/example.py", b"old\n"),),
        )
        self.assertEqual(result.diff, DIFF)
        self.assertEqual(result.changed_paths, ("src/example.py",))

    def test_derives_changed_paths_and_discards_unchanged_candidate_files(self):
        result = verify_implementation_artifact(
            model_payload(
                files=[
                    {"path": "src/example.py", "content": "old\n"},
                    {"path": "src/other.py", "content": "created\n"},
                ],
                changed_paths=["src/example.py", "src/other.py"],
            ), ENVELOPE,
            baseline_paths=("src/example.py",), baseline_files=(("src/example.py", b"old\n"),),
        )
        self.assertEqual(result.changed_paths, ("src/other.py",))
        self.assertTrue(result.diff.startswith(b"--- /dev/null\n+++ b/src/other.py\n"))

    def test_rejects_full_file_artifact_with_no_effective_change(self):
        with self.assertRaisesRegex(ImplementationArtifactError, "no_effect"):
            verify_implementation_artifact(
                model_payload(files=[{"path": "src/example.py", "content": "old\n"}]), ENVELOPE,
                baseline_files=(("src/example.py", b"old\n"),),
            )
