import unittest

from execution_platform import TaskSpec
from execution_preflight import ExecutionPreflight, PreflightCheck, REQUIRED_CHECKS


def spec():
    return TaskSpec.from_dict({
        "repository": "nario0715masa0619-create/luvira-ai-devflow", "base_commit": "a" * 40,
        "requested_action": "implementation", "acceptance_criteria": ["tests pass"],
        "budget": {"max_cost_usd": 1}, "expiry": "2026-12-01T00:00:00Z",
        "execution_scope": {"allowed_paths": ["orchestrator/"]}, "model_policy": "low-cost-first",
    })


def checker(name, passed=True, code="OK"):
    return lambda _: PreflightCheck(name, passed, code)


class ExecutionPreflightTest(unittest.TestCase):
    def test_every_required_read_only_check_must_pass(self):
        report = ExecutionPreflight([checker(name) for name in REQUIRED_CHECKS]).run(spec())
        self.assertTrue(report.passed)
        self.assertEqual([item.name for item in report.checks], list(REQUIRED_CHECKS))

    def test_missing_or_failed_check_blocks_execution_before_queueing(self):
        report = ExecutionPreflight([checker("TASK_SPEC"), checker("BROKER_IDENTITY", False, "IAM_DENIED")]).run(spec())
        self.assertFalse(report.passed)
        self.assertEqual(report.checks[1].code, "IAM_DENIED")
        self.assertEqual(report.checks[-1].code, "CHECK_MISSING")

    def test_exception_and_raw_response_are_not_exposed_in_report(self):
        def raises(_):
            raise RuntimeError("Bearer secret-value and raw provider response")
        report = ExecutionPreflight([raises]).run(spec())
        text = str(report.public_dict())
        self.assertIn("CHECK_UNAVAILABLE", text)
        self.assertNotIn("secret-value", text)
        self.assertNotIn("provider response", text)

    def test_misordered_checker_fails_closed(self):
        report = ExecutionPreflight([checker("WORKER_JOB")]).run(spec())
        self.assertFalse(report.passed)
        self.assertEqual(report.checks[0].code, "CHECK_CONTRACT_INVALID")
