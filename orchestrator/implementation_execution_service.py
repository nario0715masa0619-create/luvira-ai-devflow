"""Broker-held, single-spend implementation path after Worker health proof."""

from __future__ import annotations

from typing import Callable, Protocol

from execution_platform import ExecutionPlatform, V3Status
from implementation_artifact_handoff import ImplementationArtifactHandoff
from opencode_implementation_client import OpenCodeImplementationError


class ImplementationTaskReader(Protocol):
    def worker_health_verified(self): ...


class ImplementationTransaction(Protocol):
    def claim_implementation(self, task, record) -> None: ...
    def record_result(self, task, record, expected_status: V3Status) -> None: ...


def envelope_from_task(task) -> dict:
    """The provider sees only immutable, already-approved task facts."""
    return {
        "task_id": task.task_id,
        "spec_hash": task.spec.hash,
        "base_commit": task.spec.base_commit,
        "allowed_paths": list(task.spec.allowed_paths),
        "acceptance_criteria": list(task.spec.acceptance_criteria),
    }


class ImplementationExecutionService:
    """Generate one scoped diff only after a durable no-replay claim.

    A process interruption after the claim deliberately leaves the task in
    ``IMPLEMENTATION_GENERATING`` for operator recovery rather than spending
    provider quota a second time.  This service never creates a GitHub branch,
    commit, pull request, or deployment.
    """

    def __init__(self, tasks: ImplementationTaskReader, transaction: ImplementationTransaction,
                 source_for_task: Callable, client, model: str,
                 handoff: ImplementationArtifactHandoff):
        self._tasks = tasks
        self._transaction = transaction
        self._source_for_task = source_for_task
        self._client = client
        self._model = model
        self._handoff = handoff

    def sweep(self):
        outcomes = []
        for task in self._tasks.worker_health_verified():
            record = task.execution
            if record is None:
                outcomes.append((task.task_id, "IMPLEMENTATION_STATE_INVALID", None))
                continue
            if task.spec.model_policy == "none":
                try:
                    # A validation-only TaskSpec has a hard no-provider
                    # contract.  End it here instead of sending the source
                    # snapshot to an implementation model.
                    ExecutionPlatform.complete_validation_existing(task, record.execution_id)
                    self._transaction.record_result(task, record, V3Status.VALIDATION_SUCCEEDED)
                    outcomes.append((task.task_id, "VALIDATION_SUCCEEDED", record.execution_id))
                except Exception:
                    outcomes.append((task.task_id, "VALIDATION_RESULT_UNAVAILABLE", record.execution_id))
                continue
            try:
                ExecutionPlatform.begin_implementation_existing(task, record.execution_id)
                self._transaction.claim_implementation(task, record)
            except Exception:
                outcomes.append((task.task_id, "IMPLEMENTATION_CLAIM_UNAVAILABLE", record.execution_id))
                continue
            try:
                envelope = envelope_from_task(task)
                source = self._source_for_task(task)
                payload = self._client.generate_artifact(
                    model=self._model, envelope=envelope, source_snapshot=source,
                )
                self._handoff.receive_from_broker(record.execution_id, envelope, payload)
                ExecutionPlatform.verify_implementation_existing(task, record.execution_id)
                self._transaction.record_result(task, record, V3Status.ARTIFACT_VERIFIED)
                outcomes.append((task.task_id, "IMPLEMENTATION_ARTIFACT_VERIFIED", record.execution_id))
            except OpenCodeImplementationError as exc:
                self._fail(task, record, exc.code, outcomes)
            except Exception:
                self._fail(task, record, "IMPLEMENTATION_ARTIFACT_REJECTED_FINAL", outcomes)
        return outcomes

    def _fail(self, task, record, code: str, outcomes: list) -> None:
        try:
            ExecutionPlatform.fail_implementation_existing(task, record.execution_id, code)
            self._transaction.record_result(task, record, task.status)
            outcomes.append((task.task_id, task.status.value, record.execution_id))
        except Exception:
            outcomes.append((task.task_id, "IMPLEMENTATION_RESULT_UNAVAILABLE", record.execution_id))
