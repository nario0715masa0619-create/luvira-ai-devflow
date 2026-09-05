"""Phase 1 task-authorization domain.

This module deliberately has no provider, GitHub, secret, or worker dependency.
It is the control-plane boundary preceding any execution broker.  Production must
provide a durable ``TaskStore``; the in-memory store exists only for tests and
local deterministic checks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Protocol
from uuid import uuid4

from google.api_core.exceptions import AlreadyExists


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"


class ControlPlaneError(ValueError):
    pass


class TaskNotFound(ControlPlaneError):
    pass


class TaskConflict(ControlPlaneError):
    pass


class TaskStore(Protocol):
    """Persistence contract. Implementations must enforce task-id uniqueness."""

    def create(self, task: "TaskRecord") -> None: ...
    def get(self, task_id: str) -> "TaskRecord": ...
    def save(self, task: "TaskRecord") -> None: ...


@dataclass
class AuditEvent:
    at: str
    event: str
    actor: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRecord:
    task_id: str
    spec: dict[str, Any]
    spec_hash: str
    status: TaskStatus
    created_at: str
    approval_binding: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    audit_events: list[AuditEvent] = field(default_factory=list)
    revision: int = 1

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value

    def storage_dict(self) -> dict[str, Any]:
        """Stable primitive representation suitable for a durable store."""
        return {
            "task_id": self.task_id,
            "spec": self.spec,
            "spec_hash": self.spec_hash,
            "status": self.status.value,
            "created_at": self.created_at,
            "approval_binding": self.approval_binding,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "audit_events": [asdict(event) for event in self.audit_events],
            "revision": self.revision,
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any]) -> "TaskRecord":
        try:
            return cls(
                task_id=value["task_id"],
                spec=value["spec"],
                spec_hash=value["spec_hash"],
                status=TaskStatus(value["status"]),
                created_at=value["created_at"],
                approval_binding=value.get("approval_binding"),
                approved_by=value.get("approved_by"),
                approved_at=value.get("approved_at"),
                audit_events=[AuditEvent(**event) for event in value.get("audit_events", [])],
                revision=value["revision"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ControlPlaneError("stored_task_is_invalid") from exc


class InMemoryTaskStore:
    """Test-only store. It must never be selected as a deployed runtime backend."""

    def __init__(self):
        self._tasks: dict[str, TaskRecord] = {}

    def create(self, task: TaskRecord) -> None:
        if task.task_id in self._tasks:
            raise TaskConflict("task_id_already_exists")
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise TaskNotFound("task_not_found") from exc

    def save(self, task: TaskRecord) -> None:
        if task.task_id not in self._tasks:
            raise TaskNotFound("task_not_found")
        self._tasks[task.task_id] = task


class FirestoreTaskStore:
    """Durable, optimistic-concurrency store for the control plane.

    The caller supplies an authenticated Firestore client.  No credential is
    accepted by this class; deployed service identity is the only credential
    source.  A stale writer is rejected instead of overwriting an approval or
    state transition created by another request.
    """

    def __init__(self, client: Any, collection: str = "devflow_control_plane_tasks"):
        if not collection or "/" in collection:
            raise ControlPlaneError("invalid_task_collection")
        self._client = client
        self._collection = client.collection(collection)

    def create(self, task: TaskRecord) -> None:
        try:
            self._collection.document(task.task_id).create(task.storage_dict())
        except AlreadyExists as exc:
            raise TaskConflict("task_id_already_exists") from exc

    def get(self, task_id: str) -> TaskRecord:
        snapshot = self._collection.document(task_id).get()
        if not snapshot.exists:
            raise TaskNotFound("task_not_found")
        return TaskRecord.from_storage(snapshot.to_dict())

    def save(self, task: TaskRecord) -> None:
        reference = self._collection.document(task.task_id)
        transaction = self._client.transaction()
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            raise TaskNotFound("task_not_found")
        stored = TaskRecord.from_storage(snapshot.to_dict())
        if stored.revision != task.revision:
            raise TaskConflict("stale_task_revision")
        next_revision = task.revision + 1
        payload = task.storage_dict()
        payload["revision"] = next_revision
        transaction.set(reference, payload)
        transaction.commit()
        task.revision = next_revision

    def readiness_check(self) -> None:
        """Perform a read-only credential and database reachability check."""
        # A bounded empty/non-empty collection read validates the deployed
        # service identity without creating a probe document or changing data.
        list(self._collection.limit(1).stream())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def spec_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlPlane:
    """Validates and authorizes immutable task specifications; never executes them."""

    def __init__(self, store: TaskStore):
        self.store = store

    def create_draft(self, spec: dict[str, Any], actor: str, task_id: str | None = None) -> TaskRecord:
        if not isinstance(spec, dict):
            raise ControlPlaneError("invalid_spec")
        task = TaskRecord(
            task_id=task_id or str(uuid4()),
            spec=json.loads(canonical_json(spec)),
            spec_hash=spec_hash(spec),
            status=TaskStatus.DRAFT,
            created_at=now(),
        )
        self._audit(task, "TASK_DRAFTED", actor, {"spec_hash": task.spec_hash})
        self.store.create(task)
        return task

    def validate(self, task_id: str, actor: str) -> TaskRecord:
        task = self.store.get(task_id)
        self._require_state(task, TaskStatus.DRAFT)
        errors = self._validation_errors(task.spec)
        if errors:
            task.status = TaskStatus.REJECTED
            self._audit(task, "TASK_REJECTED", actor, {"reason": "invalid_spec", "errors": errors})
        else:
            task.status = TaskStatus.VALIDATED
            self._audit(task, "TASK_VALIDATED", actor, {"spec_hash": task.spec_hash})
        self.store.save(task)
        return task

    def request_human_approval(self, task_id: str, actor: str) -> TaskRecord:
        task = self.store.get(task_id)
        self._require_state(task, TaskStatus.VALIDATED)
        task.status = TaskStatus.AWAITING_HUMAN_APPROVAL
        task.approval_binding = self._approval_binding(task)
        self._audit(task, "HUMAN_APPROVAL_REQUESTED", actor, {"approval_binding": task.approval_binding})
        self.store.save(task)
        return task

    def authorize(self, task_id: str, actor: str, approval_binding: str) -> TaskRecord:
        task = self.store.get(task_id)
        self._require_state(task, TaskStatus.AWAITING_HUMAN_APPROVAL)
        if not approval_binding or approval_binding != task.approval_binding:
            raise TaskConflict("approval_binding_mismatch")
        # Recompute from the immutable stored snapshot before granting authorization.
        if task.spec_hash != spec_hash(task.spec) or approval_binding != self._approval_binding(task):
            raise TaskConflict("task_snapshot_changed")
        task.status = TaskStatus.AUTHORIZED
        task.approved_by = actor
        task.approved_at = now()
        self._audit(task, "TASK_AUTHORIZED", actor, {"approval_binding": approval_binding})
        self.store.save(task)
        return task

    @staticmethod
    def _validation_errors(spec: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not isinstance(spec.get("repository"), str) or "/" not in spec["repository"]:
            errors.append("repository_required")
        if not isinstance(spec.get("base_commit"), str) or not spec["base_commit"]:
            errors.append("base_commit_required")
        if not isinstance(spec.get("task_type"), str) or not spec["task_type"]:
            errors.append("task_type_required")
        criteria = spec.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria or not all(isinstance(item, str) and item.strip() for item in criteria):
            errors.append("acceptance_criteria_required")
        budget = spec.get("budget")
        if not isinstance(budget, dict) or not isinstance(budget.get("max_cost_usd"), (int, float)) or budget["max_cost_usd"] <= 0:
            errors.append("positive_budget_required")
        return errors

    @staticmethod
    def _require_state(task: TaskRecord, expected: TaskStatus) -> None:
        if task.status != expected:
            raise TaskConflict(f"invalid_transition_from_{task.status.value}")

    @staticmethod
    def _approval_binding(task: TaskRecord) -> str:
        # Bind the approval to task id, complete spec snapshot and its declared base.
        return hashlib.sha256(canonical_json({"task_id": task.task_id, "spec_hash": task.spec_hash, "base_commit": task.spec.get("base_commit")}).encode("utf-8")).hexdigest()

    @staticmethod
    def _audit(task: TaskRecord, event: str, actor: str, detail: dict[str, Any]) -> None:
        task.audit_events.append(AuditEvent(at=now(), event=event, actor=actor, detail=detail))
