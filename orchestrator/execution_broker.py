"""Fail-closed contract for Phase 2 isolated execution.

This module intentionally has no provider SDK, GitHub client, Cloud Run client,
credential, or subprocess access.  It can prepare a constrained worker envelope,
but cannot start a worker or publish any output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable

from control_plane import ControlPlaneError, TaskRecord, TaskStatus


PROTECTED_PREFIXES = (
    ".git/",
    ".github/workflows/",
    ".github/actions/",
    "security/",
    "infra/",
    "terraform/",
)
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".pyc")


class ExecutionBrokerError(ControlPlaneError):
    """The task cannot safely be handed to an isolated worker."""


@dataclass(frozen=True)
class WorkerEnvelope:
    """The complete data boundary for a future diff-only worker.

    It deliberately contains a repository snapshot reference, not a GitHub token,
    checkout credential, provider key, service account, or publication capability.
    """

    task_id: str
    spec_hash: str
    repository: str
    base_commit: str
    task_type: str
    acceptance_criteria: tuple[str, ...]
    max_cost_usd: float
    allowed_paths: tuple[str, ...]
    worker_permissions: dict[str, str]
    publication: str

    def public_dict(self) -> dict:
        return asdict(self)


class ExecutionBroker:
    """Prepare only a bounded, credential-free execution envelope."""

    def prepare(self, task: TaskRecord, allowed_paths: Iterable[str]) -> WorkerEnvelope:
        if task.status is not TaskStatus.AUTHORIZED:
            raise ExecutionBrokerError("task_not_authorized")
        if task.spec.get("requested_action") != "implementation":
            raise ExecutionBrokerError("requested_action_not_implementation")

        paths = tuple(self._validate_paths(allowed_paths))
        criteria = task.spec.get("acceptance_criteria")
        budget = task.spec.get("budget") or {}
        if not isinstance(criteria, list) or not criteria:
            raise ExecutionBrokerError("acceptance_criteria_required")
        if not isinstance(budget.get("max_cost_usd"), (int, float)) or budget["max_cost_usd"] <= 0:
            raise ExecutionBrokerError("positive_budget_required")

        return WorkerEnvelope(
            task_id=task.task_id,
            spec_hash=task.spec_hash,
            repository=task.spec["repository"],
            base_commit=task.spec["base_commit"],
            task_type=task.spec["task_type"],
            acceptance_criteria=tuple(criteria),
            max_cost_usd=float(budget["max_cost_usd"]),
            allowed_paths=paths,
            worker_permissions={
                "github": "none",
                "gcp": "none",
                "secrets": "none",
                "network": "provider-only-via-broker",
            },
            publication="verification-artifact-only",
        )

    @staticmethod
    def _validate_paths(paths: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                raise ExecutionBrokerError("allowed_path_invalid")
            candidate = PurePosixPath(path)
            rendered = candidate.as_posix()
            if candidate.is_absolute() or ".." in candidate.parts or rendered == ".":
                raise ExecutionBrokerError("allowed_path_invalid")
            if any(rendered.startswith(prefix) for prefix in PROTECTED_PREFIXES) or rendered.endswith(FORBIDDEN_SUFFIXES):
                raise ExecutionBrokerError("allowed_path_protected")
            normalized.append(rendered.rstrip("/") + "/" if path.endswith("/") else rendered)
        if not normalized:
            raise ExecutionBrokerError("allowed_paths_required")
        return tuple(sorted(set(normalized)))
