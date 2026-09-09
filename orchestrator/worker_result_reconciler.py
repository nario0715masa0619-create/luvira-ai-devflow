"""Asynchronous, fail-closed reconciliation of isolated Worker results."""

from __future__ import annotations

from typing import Protocol

from artifact_handoff import ArtifactHandoff, ArtifactHandoffError
from execution_platform import ExecutionPlatform, ExecutionRecord, V3Status, V3Task
from execution_result_adapter import ExecutionResultAdapterError, extract_bootstrap_artifact
from v3_worker_envelope import from_running_task


class RunningTaskReader(Protocol):
    def running(self): ...


class ResultTransaction(Protocol):
    def record_external_operation(self, task: V3Task, record: ExecutionRecord) -> None: ...
    def record_result(self, task: V3Task, record: ExecutionRecord, expected_status: V3Status) -> None: ...


class WorkerStateReader(Protocol):
    def find_execution_for_task(self, task_id: str, spec_hash: str) -> str | None: ...
    def completion_state(self, execution_id: str) -> str: ...


class WorkerLogReader(Protocol):
    def read_stdout(self, execution_id: str) -> str: ...


class WorkerResultReconciler:
    """Do not block the scheduler while a Worker is running.

    A malformed result fails closed.  A definite bootstrap Worker failure is
    retryable because this Worker cannot alter source, GitHub, or secrets.
    """

    def __init__(self, tasks: RunningTaskReader, transaction: ResultTransaction,
                 worker: WorkerStateReader, logs: WorkerLogReader, handoff: ArtifactHandoff):
        self._tasks, self._transaction = tasks, transaction
        self._worker, self._logs, self._handoff = worker, logs, handoff

    def sweep(self):
        outcomes = []
        for task in self._tasks.running():
            record = task.execution
            if record is None or not record.external_operation_id:
                if record is not None:
                    try:
                        recovered = self._worker.find_execution_for_task(task.task_id, task.spec.hash)
                        if recovered:
                            record.external_operation_id = recovered
                            self._transaction.record_external_operation(task, record)
                        else:
                            # The Worker API never accepted a matching
                            # execution.  Retrying is safe: no isolated
                            # Worker ran, so no provider call, source write,
                            # or publication could have happened.  Without
                            # this transition the durable RUNNING claim would
                            # strand the task forever after a rejected start.
                            ExecutionPlatform.fail_existing(
                                task, record.execution_id,
                                "WORKER_DISPATCH_NOT_ACCEPTED_RETRYABLE",
                            )
                            self._transaction.record_result(
                                task, record,
                                V3Status.EXECUTION_FAILED_RETRYABLE,
                            )
                            outcomes.append((task.task_id, "WORKER_DISPATCH_RETRYABLE", record.execution_id))
                            continue
                    except Exception:
                        outcomes.append((task.task_id, "DISPATCH_OUTCOME_UNKNOWN", record.execution_id))
                        continue
                else:
                    outcomes.append((task.task_id, "DISPATCH_OUTCOME_UNKNOWN", None))
                    continue
            try:
                state = self._worker.completion_state(record.external_operation_id)
            except Exception:
                outcomes.append((task.task_id, "RESULT_RETRY_PENDING", record.execution_id))
                continue
            if state == "PENDING":
                outcomes.append((task.task_id, "WORKER_RUNNING", record.external_operation_id))
                continue
            if state == "FAILED":
                ExecutionPlatform.fail_existing(task, record.execution_id, "WORKER_EXECUTION_RETRYABLE")
                self._transaction.record_result(task, record, V3Status.EXECUTION_FAILED_RETRYABLE)
                outcomes.append((task.task_id, "WORKER_FAILED_RETRYABLE", record.execution_id))
                continue
            if state != "SUCCEEDED":
                outcomes.append((task.task_id, "RESULT_STATE_INVALID", record.execution_id))
                continue
            try:
                envelope = from_running_task(task)
                artifact_bytes = extract_bootstrap_artifact(self._logs.read_stdout(record.external_operation_id))
                self._handoff.receive_from_broker(record.external_operation_id, envelope, artifact_bytes)
                ExecutionPlatform.verify_worker_health_existing(task, record.execution_id)
                self._transaction.record_result(task, record, V3Status.WORKER_HEALTH_VERIFIED)
                outcomes.append((task.task_id, "WORKER_HEALTH_VERIFIED", record.external_operation_id))
            except ExecutionResultAdapterError as exc:
                ExecutionPlatform.fail_existing(task, record.execution_id, str(exc))
                self._transaction.record_result(task, record, V3Status.EXECUTION_FAILED_FINAL)
                outcomes.append((task.task_id, str(exc), record.execution_id))
            except ArtifactHandoffError as exc:
                ExecutionPlatform.fail_existing(task, record.execution_id, str(exc))
                self._transaction.record_result(task, record, V3Status.EXECUTION_FAILED_FINAL)
                outcomes.append((task.task_id, str(exc), record.execution_id))
            except Exception:
                # Artifact integrity failures are not retried automatically:
                # repeated reads must never turn untrusted bytes into a result.
                ExecutionPlatform.fail_existing(task, record.execution_id, "WORKER_RESULT_PROCESSING_UNAVAILABLE_FINAL")
                self._transaction.record_result(task, record, V3Status.EXECUTION_FAILED_FINAL)
                outcomes.append((task.task_id, "WORKER_RESULT_PROCESSING_UNAVAILABLE_FINAL", record.execution_id))
        return outcomes
