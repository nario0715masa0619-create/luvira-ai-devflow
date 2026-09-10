"""Durable Broker-to-Worker dispatch, with no provider credentials in the Worker.

The broker claims the durable execution before it asks Cloud Run to create a
Worker execution.  This intentionally keeps the scheduler endpoint bounded to
starting work; waiting for, and verifying, a Worker result is a separate
reconciliation concern.
"""

from __future__ import annotations

from typing import Protocol

from execution_platform import ExecutionPlatform, ExecutionRecord, V3Task
from v3_worker_envelope import from_running_task


class WorkerDispatcherError(ValueError):
    pass


class TaskReader(Protocol):
    def get(self, task_id: str) -> V3Task: ...


class ExecutionTransaction(Protocol):
    def begin_queued(self, task: V3Task, record: ExecutionRecord) -> None: ...
    def record_launch_operation(self, task: V3Task, record: ExecutionRecord) -> None: ...


class WorkerStarter(Protocol):
    def start_operation(self, envelope: dict) -> str: ...


class WorkerDispatchService:
    """Start exactly the queued execution that was durably admitted."""

    def __init__(self, tasks: TaskReader, transaction: ExecutionTransaction, worker: WorkerStarter):
        self._tasks = tasks
        self._transaction = transaction
        self._worker = worker

    def dispatch(self, task_id: str, execution_id: str) -> str:
        task = self._tasks.get(task_id)
        record = ExecutionPlatform.begin_existing(task, execution_id)
        self._transaction.begin_queued(task, record)
        envelope = from_running_task(task)
        try:
            launch_operation_id = self._worker.start_operation(envelope)
        except Exception as exc:
            # The RUNNING claim is deliberately retained.  A later reconciler
            # must determine whether Cloud Run accepted the request before any
            # retry can spend work twice.
            raise WorkerDispatcherError("worker_start_outcome_unknown") from exc
        if not isinstance(launch_operation_id, str) or not launch_operation_id:
            raise WorkerDispatcherError("worker_start_invalid")
        record.launch_operation_id = launch_operation_id
        ExecutionPlatform.accept_worker_launch_existing(task, execution_id)
        self._transaction.record_launch_operation(task, record)
        return launch_operation_id
