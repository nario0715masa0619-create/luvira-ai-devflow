"""Durable execution-record storage for Execution Platform v3.

The task approval and each execution attempt have different identities.  This
store preserves that boundary: an existing execution id is never overwritten,
and only a read-only readiness probe may run before the v3 queue consumer is
introduced.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from execution_platform import ExecutionRecord, ExecutionPlatformError, V3Status


class ExecutionRecordConflict(ExecutionPlatformError):
    pass


class ExecutionRecordNotFound(ExecutionPlatformError):
    pass


class ExecutionRecordStore(Protocol):
    def create(self, record: ExecutionRecord) -> None: ...
    def get(self, execution_id: str) -> ExecutionRecord: ...
    def save(self, record: ExecutionRecord) -> None: ...


def record_payload(record: ExecutionRecord) -> dict[str, Any]:
    """Return an allow-listed primitive document; never accept arbitrary detail."""
    value = asdict(record)
    value["status"] = record.status.value
    return value


def record_from_payload(value: dict[str, Any]) -> ExecutionRecord:
    try:
        return ExecutionRecord(
            execution_id=value["execution_id"], task_id=value["task_id"],
            spec_hash=value["spec_hash"], attempt=value["attempt"],
            status=V3Status(value["status"]), provider=value.get("provider"),
            external_operation_id=value.get("external_operation_id"),
            launch_operation_id=value.get("launch_operation_id"),
            failure_code=value.get("failure_code"),
            stream_events=value.get("stream_events", 0),
            last_progress_at=value.get("last_progress_at"),
            revision=value.get("revision", 1),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionPlatformError("execution_record_invalid") from exc


class InMemoryExecutionRecordStore:
    """Test-only implementation."""

    def __init__(self):
        self.records: dict[str, ExecutionRecord] = {}

    def create(self, record: ExecutionRecord) -> None:
        if record.execution_id in self.records:
            raise ExecutionRecordConflict("execution_id_exists")
        self.records[record.execution_id] = record

    def get(self, execution_id: str) -> ExecutionRecord:
        try:
            return self.records[execution_id]
        except KeyError as exc:
            raise ExecutionRecordNotFound("execution_record_not_found") from exc

    def save(self, record: ExecutionRecord) -> None:
        if record.execution_id not in self.records:
            raise ExecutionRecordNotFound("execution_record_not_found")
        self.records[record.execution_id] = record


class FirestoreExecutionRecordStore:
    """Firestore adapter with per-execution identity and a read-only probe."""

    def __init__(self, client: Any, collection: str = "devflow_execution_records"):
        if not isinstance(collection, str) or not collection or "/" in collection:
            raise ExecutionPlatformError("execution_record_collection_invalid")
        self._collection = client.collection(collection)

    def create(self, record: ExecutionRecord) -> None:
        try:
            self._collection.document(record.execution_id).create(record_payload(record))
        except AlreadyExists as exc:
            raise ExecutionRecordConflict("execution_id_exists") from exc

    def get(self, execution_id: str) -> ExecutionRecord:
        snapshot = self._collection.document(execution_id).get()
        if not snapshot.exists:
            raise ExecutionRecordNotFound("execution_record_not_found")
        return record_from_payload(snapshot.to_dict())

    def save(self, record: ExecutionRecord) -> None:
        reference = self._collection.document(record.execution_id)
        snapshot = reference.get()
        if not snapshot.exists:
            raise ExecutionRecordNotFound("execution_record_not_found")
        stored = record_from_payload(snapshot.to_dict())
        if stored.revision != record.revision:
            raise ExecutionRecordConflict("stale_execution_record_revision")
        payload = record_payload(record)
        payload["revision"] = record.revision + 1
        reference.update(payload, option=firestore.LastUpdateOption(snapshot.update_time))
        record.revision += 1

    def readiness_check(self) -> None:
        list(self._collection.limit(1).stream())
