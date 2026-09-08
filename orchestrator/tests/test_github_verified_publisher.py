import unittest

from github_verified_publisher import apply_patch, parse_verified_diff


class VerifiedPublisherTest(unittest.TestCase):
    def test_applies_context_checked_verified_diff_before_any_write(self):
        diff = b"--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n old\n-before\n+after\n"
        patch = parse_verified_diff(diff)[0]
        self.assertEqual(apply_patch(b"old\nbefore\n", patch), b"old\nafter\n")

    def test_rejects_context_mismatch(self):
        diff = b"--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        with self.assertRaisesRegex(ValueError, "context_mismatch"):
            apply_patch(b"other\n", parse_verified_diff(diff)[0])
