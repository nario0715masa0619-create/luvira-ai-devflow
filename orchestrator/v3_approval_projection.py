"""Project a legacy human authorization into the v3 durable task document."""

from __future__ import annotations

from execution_platform import TaskSpec, V3Status, V3Task
from v3_task_store import V3TaskStoreError


class V3ApprovalProjectionError(ValueError):
    pass


def project_authorized(legacy_task, model_policy: str) -> V3Task:
    """Create the immutable v3 representation from a stored approval snapshot."""
    try:
        source = legacy_task.spec
        spec = TaskSpec.from_dict({
            "repository": source["repository"],
            "base_commit": source["base_commit"],
            "requested_action": source["requested_action"],
            "acceptance_criteria": source["acceptance_criteria"],
            "budget": source["budget"],
            "expiry": source["expiry"],
            "execution_scope": source["execution_scope"],
            "model_policy": model_policy,
        })
        task = V3Task(
            task_id=legacy_task.task_id,
            spec=spec,
            status=V3Status.AUTHORIZED,
            approval_binding=legacy_task.approval_binding,
            audit=["TASK_AUTHORIZED"],
        )
    except Exception as exc:
        raise V3ApprovalProjectionError("v3_approval_projection_invalid") from exc
    if not task.approval_binding:
        raise V3ApprovalProjectionError("v3_approval_binding_missing")
    return task


def ensure_projected(store, legacy_task, model_policy: str) -> V3Task:
    """Create once; retries may only reuse the exact immutable projection."""
    projected = project_authorized(legacy_task, model_policy)
    try:
        store.create(projected)
        return projected
    except V3TaskStoreError as exc:
        if str(exc) != "v3_task_exists":
            raise V3ApprovalProjectionError("v3_approval_projection_unavailable") from exc
    existing = store.get(projected.task_id)
    if existing.spec.hash != projected.spec.hash or existing.approval_binding != projected.approval_binding:
        raise V3ApprovalProjectionError("v3_approval_projection_conflict")
    return existing
