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
            # Firestore Transaction.get() is a streaming API.  A single
            # document read must go through the document reference so the
            # transaction receives one DocumentSnapshot, not a generator.
            task_snapshot = task_ref.get(transaction=transaction)
            if not task_snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(task_snapshot.to_dict())
            if stored.status is V3Status.AUTHORIZED:
                if stored.execution is not None or record.attempt != 1:
                    raise V3TransactionError("v3_task_not_queueable")
            elif stored.status is V3Status.EXECUTION_FAILED_RETRYABLE:
                # A retry replaces only a terminal retryable execution.  Its
                # attempt must advance exactly once, which prevents both
                # replaying the old provider claim and skipping attempts.
                if (stored.execution is None
                        or stored.execution.status is not V3Status.EXECUTION_FAILED_RETRYABLE
                        or record.attempt != stored.execution.attempt + 1
                        or record.execution_id == stored.execution.execution_id):
                    raise V3TransactionError("v3_task_retry_not_queueable")
            else:
                raise V3TransactionError("v3_task_not_queueable")
            if stored.spec.hash != task.spec.hash or stored.revision != task.revision:
                raise V3TransactionError("v3_task_stale_or_modified")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def begin_queued(self, task: V3Task, record: ExecutionRecord) -> None:
        """Atomically claim a queued execution before any external request.

        The caller must persist ``EXECUTION_RUNNING`` first.  A second broker
        sweep then observes the claim and is rejected before it can launch a
        duplicate Worker or spend a second provider request.
        """
        if task.status is not V3Status.EXECUTION_RUNNING or record.status is not V3Status.EXECUTION_RUNNING:
            raise V3TransactionError("execution_not_running")
        if task.execution is not record:
            raise V3TransactionError("task_execution_not_embedded")
        task_ref = self.tasks.document(task.task_id)

        def write(transaction: Any) -> None:
            snapshot = task_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(snapshot.to_dict())
            if (stored.status is not V3Status.EXECUTION_QUEUED
                    or stored.execution is None
                    or stored.execution.execution_id != record.execution_id
                    or stored.revision != task.revision):
                raise V3TransactionError("v3_execution_not_claimable")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def record_external_operation(self, task: V3Task, record: ExecutionRecord) -> None:
        """Bind the Cloud Run execution id to the already claimed task."""
        if (task.status is not V3Status.EXECUTION_RUNNING
                or task.execution is not record
                or not isinstance(record.external_operation_id, str)
                or not record.external_operation_id):
            raise V3TransactionError("external_operation_invalid")
        task_ref = self.tasks.document(task.task_id)

        def write(transaction: Any) -> None:
            snapshot = task_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(snapshot.to_dict())
            if (stored.status is not V3Status.EXECUTION_RUNNING
                    or stored.execution is None
                    or stored.execution.execution_id != record.execution_id
                    or stored.execution.external_operation_id is not None
                    or stored.revision != task.revision):
                raise V3TransactionError("v3_external_operation_not_recordable")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def record_result(self, task: V3Task, record: ExecutionRecord, expected_status: V3Status) -> None:
        """Persist a verified or classified terminal result for one execution."""
        if (task.execution is not record or task.status is not expected_status
                or record.status is not expected_status):
            raise V3TransactionError("execution_result_invalid")
        task_ref = self.tasks.document(task.task_id)

        def write(transaction: Any) -> None:
            snapshot = task_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(snapshot.to_dict())
            if (stored.status not in {V3Status.EXECUTION_RUNNING, V3Status.IMPLEMENTATION_GENERATING, V3Status.WORKER_HEALTH_VERIFIED}
                    or stored.execution is None
                    or stored.execution.execution_id != record.execution_id
                    or stored.execution.external_operation_id != record.external_operation_id
                    or stored.revision != task.revision):
                raise V3TransactionError("v3_execution_result_not_recordable")
            if (stored.status is V3Status.WORKER_HEALTH_VERIFIED
                    and expected_status is not V3Status.VALIDATION_SUCCEEDED):
                raise V3TransactionError("v3_validation_result_invalid")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def claim_implementation(self, task: V3Task, record: ExecutionRecord) -> None:
        """Persist the provider-spend claim before the Broker calls OpenCode."""
        if task.status is not V3Status.IMPLEMENTATION_GENERATING or task.execution is not record:
            raise V3TransactionError("implementation_not_claimed")
        task_ref = self.tasks.document(task.task_id)

        def write(transaction: Any) -> None:
            snapshot = task_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(snapshot.to_dict())
            if (stored.status is not V3Status.WORKER_HEALTH_VERIFIED
                    or stored.execution is None
                    or stored.execution.execution_id != record.execution_id
                    or stored.revision != task.revision):
                raise V3TransactionError("implementation_not_claimable")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def record_implementation_progress(self, task: V3Task, record: ExecutionRecord) -> None:
        """Persist stream activity without retaining provider output."""
        if (task.status is not V3Status.IMPLEMENTATION_GENERATING
                or task.execution is not record or record.stream_events < 1):
            raise V3TransactionError("implementation_progress_invalid")
        task_ref = self.tasks.document(task.task_id)

        def write(transaction: Any) -> None:
            snapshot = task_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(snapshot.to_dict())
            if (stored.status is not V3Status.IMPLEMENTATION_GENERATING
                    or stored.execution is None
                    or stored.execution.execution_id != record.execution_id
                    or stored.revision != task.revision):
                raise V3TransactionError("v3_implementation_progress_not_recordable")
            payload = task_payload(task)
            payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)

        self._run_transaction(write)
        task.revision += 1

    def record_publication(self, task: V3Task, record: ExecutionRecord) -> None:
        if task.status is not V3Status.PUBLISHED or task.execution is not record:
            raise V3TransactionError("publication_result_invalid")
        task_ref = self.tasks.document(task.task_id)
        def write(transaction: Any) -> None:
            snapshot = task_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise V3TransactionError("v3_task_not_found")
            stored = task_from_payload(snapshot.to_dict())
            if (stored.status is not V3Status.ARTIFACT_VERIFIED or stored.execution is None
                    or stored.execution.execution_id != record.execution_id or stored.revision != task.revision):
                raise V3TransactionError("publication_not_recordable")
            payload = task_payload(task); payload["revision"] = task.revision + 1
            transaction.update(task_ref, payload)
        self._run_transaction(write)
        task.revision += 1

    def _run_transaction(self, write: Callable[[Any], None]) -> None:
        transaction = self.client.transaction()
        # The client hook keeps this adapter unit-testable. Production Firestore
        # clients use the official retrying transaction decorator.
        decorator = getattr(self.client, "transactional", None) or firestore.transactional
        decorator(write)(transaction)
