"""Execution Platform v3 domain model.

This module is deliberately transport-free.  It has no Flask, Cloud Run,
Firestore, provider, credential, or GitHub dependency.  The purpose is to make
the security-critical lifecycle testable before attaching any real execution
system to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Iterable
from uuid import uuid4


class ExecutionPlatformError(ValueError):
    pass


class TransitionRejected(ExecutionPlatformError):
    pass


class PreflightRejected(ExecutionPlatformError):
    pass


class V3Status(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    EXECUTION_QUEUED = "EXECUTION_QUEUED"
    EXECUTION_RUNNING = "EXECUTION_RUNNING"
    EXECUTION_FAILED_RETRYABLE = "EXECUTION_FAILED_RETRYABLE"
    EXECUTION_FAILED_FINAL = "EXECUTION_FAILED_FINAL"
    ARTIFACT_VERIFIED = "ARTIFACT_VERIFIED"
    REVIEWING = "REVIEWING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


RETRYABLE_HTTP_STATUSES = frozenset({409, 429, 500, 502, 503, 504})
FINAL_HTTP_STATUSES = frozenset({400, 401, 403, 404})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def task_spec_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(spec).encode("utf-8")).hexdigest()


def classify_external_failure(status_code: int | None, *, timeout: bool = False) -> str:
    """Return a log-safe, stable failure code; never retain response text."""
    if timeout:
        return "EXTERNAL_TIMEOUT_RETRYABLE"
    if status_code in RETRYABLE_HTTP_STATUSES:
        return f"HTTP_{status_code}_RETRYABLE"
    if status_code in FINAL_HTTP_STATUSES:
        return f"HTTP_{status_code}_FINAL"
    return "EXTERNAL_PROTOCOL_FINAL"


@dataclass(frozen=True)
class TaskSpec:
    repository: str
    base_commit: str
    requested_action: str
    acceptance_criteria: tuple[str, ...]
    max_cost_usd: float
    expiry: str
    allowed_paths: tuple[str, ...]
    model_policy: str
    # Immutable non-execution approval facts (issue source, impact, exclusions
    # and the human-visible approval statement).  They are part of the hash,
    # so an approval can never be detached from the statement it approved.
    approval_context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskSpec":
        try:
            scope = value["execution_scope"]
            budget = value["budget"]
            instance = cls(
                repository=value["repository"], base_commit=value["base_commit"],
                requested_action=value["requested_action"],
                acceptance_criteria=tuple(value["acceptance_criteria"]),
                max_cost_usd=float(budget["max_cost_usd"]), expiry=value["expiry"],
                allowed_paths=tuple(scope["allowed_paths"]), model_policy=value["model_policy"],
                approval_context=dict(value.get("approval_context", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionPlatformError("task_spec_invalid") from exc
        if (not instance.repository or "/" not in instance.repository or not instance.base_commit
                or not instance.requested_action or not instance.acceptance_criteria
                or instance.max_cost_usd <= 0 or not instance.expiry or not instance.allowed_paths
                or not instance.model_policy or not isinstance(instance.approval_context, dict)):
            raise ExecutionPlatformError("task_spec_invalid")
        if any(not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/") for path in instance.allowed_paths):
            raise ExecutionPlatformError("task_scope_invalid")
        return instance

    def canonical_dict(self) -> dict[str, Any]:
        result = {
            "repository": self.repository, "base_commit": self.base_commit,
            "requested_action": self.requested_action,
            "acceptance_criteria": list(self.acceptance_criteria),
            "budget": {"max_cost_usd": self.max_cost_usd}, "expiry": self.expiry,
            "execution_scope": {"allowed_paths": list(self.allowed_paths)},
            "model_policy": self.model_policy,
        }
        if self.approval_context:
            result["approval_context"] = self.approval_context
        return result

    @property
    def hash(self) -> str:
        return task_spec_hash(self.canonical_dict())


@dataclass
class ExecutionRecord:
    execution_id: str
    task_id: str
    spec_hash: str
    attempt: int
    status: V3Status
    provider: str | None = None
    external_operation_id: str | None = None
    failure_code: str | None = None
    revision: int = 1


@dataclass
class V3Task:
    task_id: str
    spec: TaskSpec
    status: V3Status = V3Status.DRAFT
    approval_binding: str | None = None
    execution: ExecutionRecord | None = None
    audit: list[str] = field(default_factory=list)
    revision: int = 1


class ExecutionPlatform:
    """In-memory v3 domain service; production persistence is added separately."""

    def __init__(self):
        self._tasks: dict[str, V3Task] = {}

    def create(self, spec: TaskSpec, task_id: str | None = None) -> V3Task:
        task = V3Task(task_id=task_id or str(uuid4()), spec=spec)
        if task.task_id in self._tasks:
            raise TransitionRejected("task_exists")
        task.audit.append("TASK_DRAFTED")
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> V3Task:
        """Read a task without performing a state transition."""
        return self._task(task_id)

    def validate(self, task_id: str) -> V3Task:
        task = self._task(task_id, V3Status.DRAFT)
        task.status = V3Status.VALIDATED
        task.audit.append("TASK_VALIDATED")
        return task

    def request_approval(self, task_id: str) -> V3Task:
        task = self._task(task_id, V3Status.VALIDATED)
        task.status = V3Status.AWAITING_HUMAN_APPROVAL
        task.approval_binding = hashlib.sha256(_canonical({"task_id": task.task_id, "spec_hash": task.spec.hash}).encode()).hexdigest()
        task.audit.append("HUMAN_APPROVAL_REQUESTED")
        return task

    def authorize(self, task_id: str, binding: str) -> V3Task:
        task = self._task(task_id, V3Status.AWAITING_HUMAN_APPROVAL)
        expected = hashlib.sha256(_canonical({"task_id": task.task_id, "spec_hash": task.spec.hash}).encode()).hexdigest()
        if binding != task.approval_binding or binding != expected:
            raise TransitionRejected("approval_binding_mismatch")
        task.status = V3Status.AUTHORIZED
        task.audit.append("TASK_AUTHORIZED")
        return task

    def queue(self, task_id: str, preflight: Iterable[bool]) -> ExecutionRecord:
        task = self._task(task_id, V3Status.AUTHORIZED, V3Status.EXECUTION_FAILED_RETRYABLE)
        return self.queue_existing(task, preflight)

    @staticmethod
    def queue_existing(task: V3Task, preflight: Iterable[bool]) -> ExecutionRecord:
        """Transition a loaded task without deciding how persistence is done."""
        if task.status not in {V3Status.AUTHORIZED, V3Status.EXECUTION_FAILED_RETRYABLE}:
            raise TransitionRejected(f"invalid_transition_from_{task.status.value}")
        if not all(preflight):
            raise PreflightRejected("execution_preflight_failed")
        attempt = 1 if task.execution is None else task.execution.attempt + 1
        task.execution = ExecutionRecord(str(uuid4()), task.task_id, task.spec.hash, attempt, V3Status.EXECUTION_QUEUED)
        task.status = V3Status.EXECUTION_QUEUED
        task.audit.append("EXECUTION_QUEUED")
        return task.execution

    def begin(self, task_id: str, execution_id: str) -> ExecutionRecord:
        task = self._task(task_id, V3Status.EXECUTION_QUEUED)
        if task.execution is None or task.execution.execution_id != execution_id:
            raise TransitionRejected("execution_identity_mismatch")
        task.execution.status = V3Status.EXECUTION_RUNNING
        task.status = V3Status.EXECUTION_RUNNING
        task.audit.append("EXECUTION_RUNNING")
        return task.execution

    def fail(self, task_id: str, execution_id: str, status_code: int | None, *, timeout: bool = False) -> V3Task:
        task = self._task(task_id, V3Status.EXECUTION_RUNNING)
        if task.execution is None or task.execution.execution_id != execution_id:
            raise TransitionRejected("execution_identity_mismatch")
        code = classify_external_failure(status_code, timeout=timeout)
        task.execution.failure_code = code
        task.execution.status = V3Status.EXECUTION_FAILED_RETRYABLE if code.endswith("RETRYABLE") else V3Status.EXECUTION_FAILED_FINAL
        task.status = task.execution.status
        task.audit.append(task.status.value)
        return task

    def _task(self, task_id: str, *statuses: V3Status) -> V3Task:
        try:
            task = self._tasks[task_id]
        except KeyError as exc:
            raise TransitionRejected("task_not_found") from exc
        if statuses and task.status not in statuses:
            raise TransitionRejected(f"invalid_transition_from_{task.status.value}")
        return task
