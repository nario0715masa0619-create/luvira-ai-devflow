"""Read-only preflight contract for Execution Platform v3.

Adapters may perform cloud checks, but this module owns the policy: a failed or
missing check blocks execution before an execution record is created.  Check
details are deliberately reduced to stable codes so credentials, HTTP bodies,
and prompts cannot cross into task audit data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from execution_platform import TaskSpec


REQUIRED_CHECKS = (
    "TASK_SPEC",
    "BROKER_IDENTITY",
    "WORKER_JOB",
    "ARTIFACT_BOUNDARY",
    "PROVIDER_AVAILABILITY",
)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    passed: bool
    code: str

    def public_dict(self) -> dict[str, str | bool]:
        return {"name": self.name, "passed": self.passed, "code": self.code}


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return len(self.checks) == len(REQUIRED_CHECKS) and all(check.passed for check in self.checks)

    def public_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "checks": [check.public_dict() for check in self.checks]}


Checker = Callable[[TaskSpec], PreflightCheck]


class ExecutionPreflight:
    """Runs an injected, read-only check for every v3 execution prerequisite."""

    def __init__(self, checkers: Iterable[Checker]):
        self._checkers = tuple(checkers)

    def run(self, spec: TaskSpec) -> PreflightReport:
        results: list[PreflightCheck] = []
        expected = iter(REQUIRED_CHECKS)
        for checker in self._checkers:
            expected_name = next(expected, None)
            if expected_name is None:
                break
            try:
                result = checker(spec)
            except Exception:
                result = PreflightCheck(expected_name, False, "CHECK_UNAVAILABLE")
            if result.name != expected_name:
                result = PreflightCheck(expected_name, False, "CHECK_CONTRACT_INVALID")
            results.append(self._safe(result))
        for missing in expected:
            results.append(PreflightCheck(missing, False, "CHECK_MISSING"))
        return PreflightReport(tuple(results))

    @staticmethod
    def _safe(check: PreflightCheck) -> PreflightCheck:
        # Audit records are codes, never raw provider/GCP responses.
        code = check.code if isinstance(check.code, str) and check.code.isupper() and len(check.code) <= 64 else "CHECK_RESULT_INVALID"
        return PreflightCheck(check.name, bool(check.passed), code)
