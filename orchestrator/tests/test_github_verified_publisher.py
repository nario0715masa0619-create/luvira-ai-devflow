import unittest

from github_verified_publisher import GitHubVerifiedPublisher, VerifiedPublicationError, apply_patch, parse_verified_diff


class VerifiedPublisherTest(unittest.TestCase):
    def test_applies_context_checked_verified_diff_before_any_write(self):
        diff = b"--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n old\n-before\n+after\n"
        patch = parse_verified_diff(diff)[0]
        self.assertEqual(apply_patch(b"old\nbefore\n", patch), b"old\nafter\n")

    def test_rejects_context_mismatch(self):
        diff = b"--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        with self.assertRaisesRegex(ValueError, "context_mismatch"):
            apply_patch(b"other\n", parse_verified_diff(diff)[0])

    def test_opens_verified_artifacts_ready_for_review(self):
        calls = []
        publisher = GitHubVerifiedPublisher("a/b", "token")

        def call(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET" and "/contents/" in path:
                raise VerifiedPublicationError("publication_github_http_404")
            return {"html_url": "https://example.test/pr/1"} if path.endswith("/pulls") else {}

        publisher._call = call
        publisher.publish(
            task_id="task", execution_id="execution", base_commit="a" * 40,
            diff=b"--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+created\n",
            title="題名", body="本文",
        )

        self.assertFalse(calls[-1][2]["draft"])

    def test_rejects_creation_when_the_immutable_base_already_has_the_target(self):
        publisher = GitHubVerifiedPublisher("a/b", "token")
        publisher._call = lambda *_args, **_kwargs: {"content": "", "sha": "present"}

        with self.assertRaisesRegex(ValueError, "publication_base_content_already_exists"):
            publisher.publish(
                task_id="task", execution_id="execution", base_commit="a" * 40,
                diff=b"--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+created\n",
                title="題名", body="本文",
            )

    def test_returns_none_when_the_deterministic_branch_has_no_pr_yet(self):
        publisher = GitHubVerifiedPublisher("a/b", "token")
        publisher._call = lambda *_args, **_kwargs: []

        self.assertIsNone(publisher.publication_url_for("task", "execution"))

    def test_rejects_multiple_prs_for_one_deterministic_branch(self):
        publisher = GitHubVerifiedPublisher("a/b", "token")
        publisher._call = lambda *_args, **_kwargs: [{}, {}]

        with self.assertRaisesRegex(ValueError, "publication_pull_not_unique"):
            publisher.publication_url_for("task", "execution")
