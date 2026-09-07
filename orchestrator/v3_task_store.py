"""Durable TaskSpec/approval state for Execution Platform v3."""

from __future__ import annotations

from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from execution_platform import ExecutionPlatformError, ExecutionRecord, TaskSpec, V3Status, V3Task


class V3TaskStoreError(ExecutionPlatformError):
    pass


def task_payload(task: V3Task) -> dict[str, Any]:
    payload = {
        "task_id": task.task_id, "spec": task.spec.canonical_dict(), "spec_hash": task.spec.hash,
        "status": task.status.value, "approval_binding": task.approval_binding,
        "audit": list(task.audit), "revision": task.revision,
    }
    if task.execution is not None:
        payload["execution"] = {
            "execution_id": task.execution.execution_id, "task_id": task.execution.task_id,
            "spec_hash": task.execution.spec_hash, "attempt": task.execution.attempt,
            "status": task.execution.status.value, "provider": task.execution.provider,
            "external_operation_id": task.execution.external_operation_id,
            "failure_code": task.execution.failure_code, "revision": task.execution.revision,
        }
    return payload


def task_from_payload(value: dict[str, Any]) -> V3Task:
    try:
        execution_value = value.get("execution")
        execution = None if execution_value is None else ExecutionRecord(
            execution_id=execution_value["execution_id"], task_id=execution_value["task_id"],
            spec_hash=execution_value["spec_hash"], attempt=execution_value["attempt"],
            status=V3Status(execution_value["status"]), provider=execution_value.get("provider"),
            external_operation_id=execution_value.get("external_operation_id"),
            failure_code=execution_value.get("failure_code"), revision=execution_value.get("revision", 1),
        )
        task = V3Task(task_id=value["task_id"], spec=TaskSpec.from_dict(value["spec"]),
                      status=V3Status(value["status"]), approval_binding=value.get("approval_binding"),
                      execution=execution, audit=list(value.get("audit", [])), revision=value["revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise V3TaskStoreError("v3_task_invalid") from exc
    if (value.get("spec_hash") != task.spec.hash
            or (task.execution is not None and (task.execution.task_id != task.task_id or task.execution.spec_hash != task.spec.hash))
            or not all(isinstance(event, str) and event.isupper() for event in task.audit)):
        raise V3TaskStoreError("v3_task_integrity_invalid")
    return task


class FirestoreV3TaskStore:
    def __init__(self, client: Any, collection: str = "devflow_v3_tasks"):
        if not isinstance(collection, str) or not collection or "/" in collection:
            raise V3TaskStoreError("v3_task_collection_invalid")
        self._collection = client.collection(collection)

    def create(self, task: V3Task) -> None:
        try:
            self._collection.document(task.task_id).create(task_payload(task))
        except AlreadyExists as exc:
            raise V3TaskStoreError("v3_task_exists") from exc

    def get(self, task_id: str) -> V3Task:
        snapshot = self._collection.document(task_id).get()
        if not snapshot.exists:
            raise V3TaskStoreError("v3_task_not_found")
        return task_from_payload(snapshot.to_dict())

    def save(self, task: V3Task) -> None:
        reference = self._collection.document(task.task_id)
        snapshot = reference.get()
        if not snapshot.exists:
            raise V3TaskStoreError("v3_task_not_found")
        if task_from_payload(snapshot.to_dict()).revision != task.revision:
            raise V3TaskStoreError("v3_task_stale_revision")
        payload = task_payload(task)
        payload["revision"] = task.revision + 1
        reference.update(payload, option=firestore.LastUpdateOption(snapshot.update_time))
        task.revision += 1

    def readiness_check(self) -> None:
        list(self._collection.limit(1).stream())
