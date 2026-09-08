"""Create the credential-free Worker input from one immutable v3 task."""

from __future__ import annotations

from execution_platform import V3Status, V3Task


class V3WorkerEnvelopeError(ValueError):
    pass


def from_running_task(task: V3Task) -> dict:
    """Build the only Worker input; no Issue body, token, or provider key."""
    if task.status is not V3Status.EXECUTION_RUNNING or task.execution is None:
        raise V3WorkerEnvelopeError("execution_not_running")
    if task.execution.spec_hash != task.spec.hash:
        raise V3WorkerEnvelopeError("execution_spec_binding_invalid")
    task_type = task.spec.approval_context.get("task_type", "implementation")
    if not isinstance(task_type, str) or not task_type:
        raise V3WorkerEnvelopeError("task_type_invalid")
    return {
        "task_id": task.task_id,
        "spec_hash": task.spec.hash,
        "repository": task.spec.repository,
        "base_commit": task.spec.base_commit,
        "task_type": task_type,
        "acceptance_criteria": list(task.spec.acceptance_criteria),
        "max_cost_usd": task.spec.max_cost_usd,
        "allowed_paths": list(task.spec.allowed_paths),
        "worker_permissions": {
            "github": "none", "gcp": "none", "secrets": "none",
            "network": "provider-only-via-broker",
        },
        "publication": "verification-artifact-only",
    }
