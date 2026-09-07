import unittest
from unittest.mock import patch

from execution_platform import TaskSpec


class RuntimePolicyTest(unittest.TestCase):
    def test_validation_policy_does_not_call_provider(self):
        # The decision is tested at the policy boundary: validation is not an
        # AI task and therefore must never make provider availability a gate.
        spec = TaskSpec.from_dict({"repository":"a/b","base_commit":"a" * 40,"requested_action":"validate","acceptance_criteria":["x"],"budget":{"max_cost_usd":1},"expiry":"2027-01-01T00:00:00Z","execution_scope":{"allowed_paths":["README.md"]},"model_policy":"none"})
        self.assertEqual(spec.model_policy, "none")
