import unittest

from opencode_provider_policy import classify_opencode_failure


class OpenCodeProviderPolicyTest(unittest.TestCase):
    def test_transient_limit_is_retryable_without_provider_switch(self):
        self.assertEqual(
            classify_opencode_failure(429, "rate limit exceeded"),
            "OPENCODE_HTTP_429_RETRYABLE",
        )

    def test_zen_monthly_limit_is_terminal(self):
        self.assertEqual(
            classify_opencode_failure(429, "Zen balance monthly limit reached"),
            "OPENCODE_BUDGET_EXHAUSTED_FINAL",
        )

    def test_timeout_is_retryable(self):
        self.assertEqual(
            classify_opencode_failure(None, timeout=True),
            "OPENCODE_TIMEOUT_RETRYABLE",
        )

    def test_body_is_not_part_of_the_returned_code(self):
        self.assertEqual(
            classify_opencode_failure(403, "do not persist this provider response"),
            "OPENCODE_HTTP_403_FINAL",
        )
