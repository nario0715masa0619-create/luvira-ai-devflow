"""The sole task lifecycle for the execution platform.

This module deliberately owns intake, approval and queue admission together.
There is no projection from a legacy task and no second task collection: one
Firestore document is the aggregate root for a task and its current execution.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from durable_queue_service import DurableQueueRejected, DurableQueueService
from execution_platform import ExecutionPlatform, ExecutionPlatformError, TaskSpec, TransitionRejected, V3Status, V3Task


class V3ControlPlaneError(ExecutionPlatformError):
    pass


class V3TaskStore(Protocol):
    def create(self, task: V3Task) -> None: ...
    def get(self, task_id: str) -> V3Task: ...
    def save(self, task: V3Task) -> None: ...


def _binding(task_id: str, spec: TaskSpec) -> str:
    payload = json.dumps({"task_id": task_id, "spec_hash": spec.hash}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class V3ControlPlane:
    """Single-writer lifecycle facade; GitHub and workers never write state."""

    def __init__(self, tasks: V3TaskStore, queue: DurableQueueService):
        self.tasks = tasks
        self.queue = queue

    def register(self, task_id: str, raw_spec: dict) -> V3Task:
        spec = TaskSpec.from_dict(raw_spec)
        try:
            task = self.tasks.get(task_id)
        except Exception as exc:
            if str(exc) != "v3_task_not_found":
                raise V3ControlPlaneError("task_store_unavailable") from exc
            platform = ExecutionPlatform()
            task = platform.create(spec, task_id=task_id)
            platform.validate(task_id)
            platform.request_approval(task_id)
            try:
                self.tasks.create(task)
                return task
            except Exception as create_error:
                # GitHub can deliver `opened` and `labeled` concurrently. A
                # duplicate document means another delivery won the atomic
                # create; reload it and apply the same immutable comparison.
                if str(create_error) != "v3_task_exists":
                    raise V3ControlPlaneError("task_store_unavailable") from create_error
                task = self.tasks.get(task_id)

        # A webhook delivery is idempotent only for exactly the same immutable
        # document.  A changed Issue Form must produce a new task/approval.
        if task.spec.hash != spec.hash:
            raise V3ControlPlaneError("task_id_reused_with_different_spec")
        if task.status is not V3Status.AWAITING_HUMAN_APPROVAL:
            raise V3ControlPlaneError("task_not_pending_human_approval")
        return task

    def authorize_and_queue(self, task_id: str, binding: str, actor: str):
        try:
            task = self.tasks.get(task_id)
        except Exception as exc:
            raise V3ControlPlaneError("task_not_found") from exc
        expected = _binding(task.task_id, task.spec)
        if binding != expected or binding != task.approval_binding:
            raise V3ControlPlaneError("approval_binding_mismatch")

        if task.status is V3Status.AWAITING_HUMAN_APPROVAL:
            task.status = V3Status.AUTHORIZED
            task.audit.append("TASK_AUTHORIZED")
            self.tasks.save(task)
        elif task.status not in {V3Status.AUTHORIZED, V3Status.EXECUTION_QUEUED}:
            raise V3ControlPlaneError("authorization_not_reusable")

        # An Actions retry returns the existing queued execution. It never
        # creates another Job request and it never consults legacy state.
        if task.status is V3Status.EXECUTION_QUEUED and task.execution is not None:
            return task.execution, None
        try:
            return (*self.queue.request(task_id),)
        except DurableQueueRejected as exc:
            raise V3ControlPlaneError(str(exc)) from exc
        except TransitionRejected as exc:
            raise V3ControlPlaneError("queue_state_conflict") from exc

    def authorize(self, task_id: str, binding: str, actor: str) -> V3Task:
        """Persist human approval only; execution is owned by the broker."""
        try:
            task = self.tasks.get(task_id)
        except Exception as exc:
            raise V3ControlPlaneError("task_not_found") from exc
        if binding != _binding(task.task_id, task.spec) or binding != task.approval_binding:
            raise V3ControlPlaneError("approval_binding_mismatch")
        if task.status is V3Status.AWAITING_HUMAN_APPROVAL:
            task.status = V3Status.AUTHORIZED
            task.audit.append("TASK_AUTHORIZED")
            self.tasks.save(task)
        elif task.status not in {V3Status.AUTHORIZED, V3Status.EXECUTION_QUEUED, V3Status.EXECUTION_RUNNING}:
            raise V3ControlPlaneError("authorization_not_reusable")
        return task
