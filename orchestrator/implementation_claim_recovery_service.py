"""Safe recovery of an interrupted, intentionally non-replayable AI claim."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from execution_platform import ExecutionPlatform, V3Status


class ClaimRecoveryTaskReader(Protocol):
    def implementation_generating(self): ...


class ClaimRecoveryTransaction(Protocol):
    def record_result(self, task, record, expected_status: V3Status) -> None: ...


class ImplementationClaimRecoveryService:
    """Terminalize an abandoned provider claim without ever replaying it.

    The timeout measures absence of a durable heartbeat after a claim, not the
    allowed duration of a task. Healthy work continues for as long as it keeps
    reporting progress. A stalled claim is made terminal so the immutable audit
    trail explains why a fresh human approval is required to spend again.
    """

    def __init__(self, tasks: ClaimRecoveryTaskReader, transaction: ClaimRecoveryTransaction,
                 *, no_progress_timeout: timedelta = timedelta(minutes=30),
                 now: Callable[[], datetime] | None = None):
        if no_progress_timeout <= timedelta(0):
            raise ValueError("implementation_no_progress_timeout_invalid")
        self._tasks = tasks
        self._transaction = transaction
        self._timeout = no_progress_timeout
        self._now = now or (lambda: datetime.now(timezone.utc))

    def sweep(self):
        outcomes = []
        for task in self._tasks.implementation_generating():
            record = task.execution
            if record is None:
                outcomes.append((task.task_id, "IMPLEMENTATION_STATE_INVALID", None))
                continue
            checkpoint = self._checkpoint(record)
            if checkpoint is not None and self._now() - checkpoint < self._timeout:
                outcomes.append((task.task_id, "IMPLEMENTATION_PROGRESS_ACTIVE", record.execution_id))
                continue
            code = ("IMPLEMENTATION_CLAIM_TIME_UNKNOWN_FINAL"
                    if checkpoint is None else "IMPLEMENTATION_CLAIM_STALLED_FINAL")
            try:
                ExecutionPlatform.fail_existing(task, record.execution_id, code)
                self._transaction.record_result(task, record, V3Status.EXECUTION_FAILED_FINAL)
                outcomes.append((task.task_id, code, record.execution_id))
            except Exception:
                outcomes.append((task.task_id, "IMPLEMENTATION_RECOVERY_UNAVAILABLE", record.execution_id))
        return outcomes

    @staticmethod
    def _checkpoint(record):
        value = record.last_progress_at or record.implementation_claimed_at
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
