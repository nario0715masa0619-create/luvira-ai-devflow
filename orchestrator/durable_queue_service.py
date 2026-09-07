"""Durable v3 queue boundary.

This service has no worker launcher. It may only turn one persisted,
authorized task into one persisted queued execution after all preflight checks
pass. The Firestore transaction is injected to keep the state boundary explicit.
"""

from __future__ import annotations

from typing import Protocol

from execution_platform import ExecutionPlatform, ExecutionPlatformError, ExecutionRecord, V3Task
from execution_preflight import ExecutionPreflight, PreflightReport


class V3TaskReader(Protocol):
    def get(self, task_id: str) -> V3Task: ...


class QueueTransaction(Protocol):
    def queue_authorized(self, task: V3Task, record: ExecutionRecord) -> None: ...


class DurableQueueRejected(ExecutionPlatformError):
    pass


class DurableQueueService:
    def __init__(
        self,
        tasks: V3TaskReader,
        preflight: ExecutionPreflight,
        transaction: QueueTransaction,
    ):
        self._tasks = tasks
        self._preflight = preflight
        self._transaction = transaction

    def request(self, task_id: str) -> tuple[ExecutionRecord, PreflightReport]:
        task = self._tasks.get(task_id)
        report = self._preflight.run(task.spec)
        if not report.passed:
            raise DurableQueueRejected("execution_preflight_failed")

        original_status, original_execution, original_audit = (
            task.status,
            task.execution,
            list(task.audit),
        )
        try:
            record = ExecutionPlatform.queue_existing(task, [report.passed])
            self._transaction.queue_authorized(task, record)
        except Exception as exc:
            # The transaction did not commit, so restore the in-memory object
            # used by a request handler as well as leaving Firestore unchanged.
            task.status, task.execution, task.audit = (
                original_status,
                original_execution,
                original_audit,
            )
            if isinstance(exc, DurableQueueRejected):
                raise
            raise DurableQueueRejected("execution_queue_not_durable") from exc
        return record, report
