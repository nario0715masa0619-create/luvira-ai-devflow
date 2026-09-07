"""Atomic creation boundary for a v3 queued execution."""

from __future__ import annotations

from typing import Any, Callable

from google.cloud import firestore

from execution_platform import ExecutionRecord, V3Status, V3Task
from execution_record_store import record_payload
from v3_task_store import task_payload


class V3TransactionError(ValueError):
    pass


class V3Transaction:
    def __init__(
        self,
        client: Any,
        task_collection: str = "devflow_v3_tasks",
        record_collection: str = "devflow_execution_records",
    ):
        self.client = client
        self.tasks = client.collection(task_collection)
        self.records = client.collection(record_collection)

    def create_queued(self, task: V3Task, record: ExecutionRecord) -> None:
        """Atomically persist exactly one queued record for an authorized task."""
        if task.status is not V3Status.EXECUTION_QUEUED:
            raise V3TransactionError("task_not_queued")
        if record.status is not V3Status.EXECUTION_QUEUED:
            raise V3TransactionError("record_not_queued")
        if record.task_id != task.task_id or record.spec_hash != task.spec.hash:
            raise V3TransactionError("task_record_binding_invalid")

        task_ref = self.tasks.document(task.task_id)
        record_ref = self.records.document(record.execution_id)

        def write(transaction: Any) -> None:
            if transaction.get(task_ref).exists or transaction.get(record_ref).exists:
                raise V3TransactionError("v3_identity_exists")
            transaction.create(task_ref, task_payload(task))
            transaction.create(record_ref, record_payload(record))

        self._run_transaction(write)

    def _run_transaction(self, write: Callable[[Any], None]) -> None:
        transaction = self.client.transaction()
        # The client hook keeps this adapter unit-testable. Production Firestore
        # clients use the official retrying transaction decorator.
        decorator = getattr(self.client, "transactional", None) or firestore.transactional
        decorator(write)(transaction)
