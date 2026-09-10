"""Asynchronous, fail-closed reconciliation of isolated Worker results."""

from __future__ import annotations

from typing import Protocol

from artifact_handoff import ArtifactHandoff, ArtifactHandoffError
from cloud_run_bootstrap_client import CloudRunBootstrapClientError
from execution_platform import ExecutionPlatform, ExecutionRecord, V3Status, V3Task
from execution_result_adapter import ExecutionResultAdapterError, extract_bootstrap_artifact
from v3_worker_envelope import from_running_task
from v3_transaction import V3TransactionError


class RunningTaskReader(Protocol):
    def running(self): ...


class ResultTransaction(Protocol):
    def record_launch_operation(self, task: V3Task, record: ExecutionRecord) -> None: ...
    def record_worker_execution_identified(self, task: V3Task, record: ExecutionRecord) -> None: ...
    def record_result(self, task: V3Task, record: ExecutionRecord, expected_status: V3Status) -> None: ...


class WorkerStateReader(Protocol):
    def execution_for_operation(self, operation_name: str) -> str | None: ...
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
                        recovered = self._recover_execution(task, record)
                        if recovered:
                            record.external_operation_id = recovered
                            ExecutionPlatform.identify_worker_execution_existing(task, record.execution_id)
                            try:
                                self._transaction.record_worker_execution_identified(task, record)
                            except V3TransactionError:
                                # A concurrent sweep may have advanced the
                                # document after this read.  Do not launch
                                # again; the following sweep rereads the
                                # durable record and either observes the bind
                                # or safely retries this exact bind.
                                outcomes.append((task.task_id, "WORKER_BINDING_RETRY_PENDING", record.execution_id))
                                continue
                        elif record.launch_operation_id:
                            outcomes.append((task.task_id, "WORKER_LAUNCH_PENDING", record.launch_operation_id))
                            continue
                        elif task.status is V3Status.EXECUTION_RUNNING:
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
                        else:
                            outcomes.append((task.task_id, "WORKER_LAUNCH_RECOVERY_PENDING", record.execution_id))
                            continue
                    except CloudRunBootstrapClientError as exc:
                        if (str(exc) == "cloud_run_execution_ambiguous"
                                and not record.launch_operation_id
                                and task.status is V3Status.EXECUTION_RUNNING):
                            # This recovery path is only for records created
                            # before launch-operation persistence existed.  The
                            # isolated Worker has no provider, source-write,
                            # GitHub, or secret capability, so several matching
                            # health-probe jobs cannot have spent or changed
                            # anything.  Return the task to the normal durable
                            # retry path instead of stranding approval forever.
                            ExecutionPlatform.fail_existing(
                                task, record.execution_id,
                                "WORKER_DISPATCH_AMBIGUOUS_RETRYABLE",
                            )
                            self._transaction.record_result(
                                task, record,
                                V3Status.EXECUTION_FAILED_RETRYABLE,
                            )
                            outcomes.append((task.task_id, "WORKER_DISPATCH_RETRYABLE", record.execution_id))
                            continue
                        if record.launch_operation_id:
                            # A recorded launch operation is proof that Cloud
                            # Run accepted this exact Worker request.  Never
                            # turn an ambiguous historical execution search
                            # into a retry: doing so would create a duplicate
                            # Worker (and potentially a duplicate provider
                            # request).  Keep the durable claim and retry
                            # only the read-side reconciliation later.
                            outcomes.append((task.task_id, "WORKER_LAUNCH_RECOVERY_PENDING", record.launch_operation_id))
                            continue
                        outcomes.append((task.task_id, "DISPATCH_OUTCOME_UNKNOWN", record.execution_id))
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

    def _recover_execution(self, task: V3Task, record: ExecutionRecord) -> str | None:
        """Resolve one accepted launch without ever creating another Worker.

        The durable Cloud Run operation is the proof whenever it exists.  A
        legacy record without that operation may use a uniquely
        envelope-bound execution.  Do not search historical executions for a
        known accepted launch: an old retry can share the envelope and make
        the result ambiguous, which must never create another Worker.
        """
        if not record.launch_operation_id:
            return self._worker.find_execution_for_task(task.task_id, task.spec.hash)
        return self._worker.execution_for_operation(record.launch_operation_id)
