"""Queue boundary for v3: approval never starts a worker directly."""

from __future__ import annotations

from execution_platform import ExecutionPlatform, ExecutionPlatformError, ExecutionRecord, PreflightRejected
from execution_preflight import ExecutionPreflight, PreflightReport
from execution_record_store import ExecutionRecordStore


class QueueRequestRejected(ExecutionPlatformError):
    pass


class ExecutionQueue:
    """Creates a durable queued record only after a complete preflight.

    There is intentionally no worker client in this class.  A later, separately
    reviewed queue consumer may fetch a durable `EXECUTION_QUEUED` record; the
    GitHub approval route cannot pass beyond this boundary.
    """

    def __init__(self, platform: ExecutionPlatform, preflight: ExecutionPreflight, records: ExecutionRecordStore):
        self._platform, self._preflight, self._records = platform, preflight, records

    def request(self, task_id: str) -> tuple[ExecutionRecord, PreflightReport]:
        task = self._platform.get(task_id)
        report = self._preflight.run(task.spec)
        if not report.passed:
            raise QueueRequestRejected("execution_preflight_failed")
        try:
            record = self._platform.queue(task_id, [report.passed])
            self._records.create(record)
        except Exception as exc:
            # No worker was contacted.  Restore a retryable authorization state
            # so a transient storage error never consumes the human decision.
            if "record" in locals():
                task.execution = None
                task.status = task.status.EXECUTION_FAILED_RETRYABLE
                task.audit.append("EXECUTION_QUEUE_PERSISTENCE_FAILED")
            raise QueueRequestRejected("execution_queue_not_durable") from exc
        return record, report
