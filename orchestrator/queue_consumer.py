"""v3 queued-execution consumer; contains no Cloud Run dependency."""
from typing import Protocol
from execution_platform import ExecutionPlatform, ExecutionRecord, V3Status
from execution_record_store import ExecutionRecordStore

class Launcher(Protocol):
    def launch(self, record: ExecutionRecord) -> str: ...

class QueueConsumer:
    def __init__(self, platform: ExecutionPlatform, records: ExecutionRecordStore, launcher: Launcher):
        self.platform, self.records, self.launcher = platform, records, launcher

    def consume(self, task_id: str, execution_id: str) -> ExecutionRecord:
        record = self.records.get(execution_id)
        if record.task_id != task_id or record.status is not V3Status.EXECUTION_QUEUED:
            raise ValueError("queued_execution_identity_invalid")
        self.platform.begin(task_id, execution_id)
        record.status = V3Status.EXECUTION_RUNNING
        self.records.save(record)
        try:
            operation = self.launcher.launch(record)
            if not isinstance(operation, str) or not operation:
                raise ValueError("external_operation_invalid")
        except Exception:
            self.platform.fail(task_id, execution_id, None, timeout=True)
            record.status = V3Status.EXECUTION_FAILED_RETRYABLE
            record.failure_code = "EXTERNAL_TIMEOUT_RETRYABLE"
            self.records.save(record)
            raise
        record.external_operation_id = operation
        self.records.save(record)
        return record
