"""Atomic creation boundary for a v3 queued execution."""

from __future__ import annotations

from typing import Any, Callable

from google.cloud import firestore

from execution_platform import ExecutionRecord, V3Status, V3Task
from v3_task_store import task_from_payload, task_payload


class V3TransactionError(ValueError):
    pass


class V3Transaction:
    def __init__(
        self,
        client: Any,
        task_collection: str = "devflow_v3_tasks",
    ):
        self.client = client
        self.tasks = client.collection(task_collection)

    def queue_authorized(self, task: V3Task, record: ExecutionRecord) -> None:
        """Atomically transition an authorized task and create its record."""
        if task.status is not V3Status.EXECUTION_QUEUED:
            raise V3TransactionError("task_not_queued")
        if record.status is not V3Status.EXECUTION_QUEUED:
            raise V3TransactionError("record_not_queued")
        if record.task_id != task.task_id or record.spec_hash != task.spec.hash:
            raise V3TransactionError("task_record_binding_invalid")
        if task.execution is not record:
            raise V3TransactionError("task_execution_not_embedded")

        task_ref = self.tasks.document(task.task_id)
        def write(transaction: Any) -> None:
            task_snapshot = transaction.get(task_ref)
            if not task_snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(task_snapshot.to_dict())
            if stored.status is not V3Status.AUTHORIZED:
                raise V3TransactionError("v3_task_not_authorized")
            if stored.spec.hash != task.spec.hash or stored.revision != task.revision:
                raise V3TransactionError("v3_task_stale_or_modified")
            if stored.execution is not None:
                raise V3TransactionError("v3_execution_already_exists")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def _run_transaction(self, write: Callable[[Any], None]) -> None:
        transaction = self.client.transaction()
        # The client hook keeps this adapter unit-testable. Production Firestore
        # clients use the official retrying transaction decorator.
        decorator = getattr(self.client, "transactional", None) or firestore.transactional
        decorator(write)(transaction)
