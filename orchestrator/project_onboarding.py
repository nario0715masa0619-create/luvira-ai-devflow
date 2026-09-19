"""Fail-closed lifecycle for DevFlow-managed product repositories.

The control repository is an approval and audit plane.  It is never an
implicit destination for product code.  A product repository becomes eligible
for implementation only after its creation, bootstrap, and App-installation
checks have all completed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re
from typing import Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore


class ProjectOnboardingError(ValueError):
    pass


class ProjectStatus(str, Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    PROVISION_FAILED = "PROVISION_FAILED"


_SLUG = re.compile(r"[a-z][a-z0-9-]{2,62}")
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37})")
_COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class NewProjectRequest:
    owner: str
    slug: str
    description: str

    def __post_init__(self) -> None:
        if not _OWNER.fullmatch(self.owner):
            raise ProjectOnboardingError("project_owner_invalid")
        if not _SLUG.fullmatch(self.slug):
            raise ProjectOnboardingError("project_slug_invalid")
        if not isinstance(self.description, str) or not self.description.strip() or len(self.description) > 160:
            raise ProjectOnboardingError("project_description_invalid")

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.slug}"


@dataclass(frozen=True)
class ProjectRecord:
    request: NewProjectRequest
    status: ProjectStatus = ProjectStatus.REQUESTED
    repository: str | None = None
    bootstrap_commit: str | None = None
    github_app_ready: bool = False
    failure_code: str | None = None

    @property
    def project_id(self) -> str:
        return f"project-{self.request.slug}"

    @property
    def accepts_implementation(self) -> bool:
        return (self.status is ProjectStatus.READY
                and self.repository == self.request.repository
                and self.github_app_ready
                and isinstance(self.bootstrap_commit, str))


class ProjectRegistry(Protocol):
    def get(self, project_id: str) -> ProjectRecord: ...
    def save(self, record: ProjectRecord) -> None: ...


class InMemoryProjectRegistry:
    def __init__(self) -> None:
        self._records: dict[str, ProjectRecord] = {}

    def get(self, project_id: str) -> ProjectRecord:
        try:
            return self._records[project_id]
        except KeyError as exc:
            raise ProjectOnboardingError("project_not_found") from exc

    def save(self, record: ProjectRecord) -> None:
        self._records[record.project_id] = record


def project_payload(record: ProjectRecord) -> dict:
    return {
        "project_id": record.project_id,
        "owner": record.request.owner,
        "slug": record.request.slug,
        "description": record.request.description,
        "status": record.status.value,
        "repository": record.repository,
        "bootstrap_commit": record.bootstrap_commit,
        "github_app_ready": record.github_app_ready,
        "failure_code": record.failure_code,
    }


def project_from_payload(payload: dict) -> ProjectRecord:
    try:
        request = NewProjectRequest(payload["owner"], payload["slug"], payload["description"])
        record = ProjectRecord(
            request=request, status=ProjectStatus(payload["status"]), repository=payload.get("repository"),
            bootstrap_commit=payload.get("bootstrap_commit"),
            github_app_ready=payload.get("github_app_ready", False), failure_code=payload.get("failure_code"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectOnboardingError("project_record_invalid") from exc
    if payload.get("project_id") != record.project_id:
        raise ProjectOnboardingError("project_record_identity_invalid")
    return record


class FirestoreProjectRegistry:
    """Durable registry; each product is addressed only by its approved slug."""

    def __init__(self, client, collection: str = "devflow_projects") -> None:
        if not isinstance(collection, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,62}", collection):
            raise ProjectOnboardingError("project_collection_invalid")
        self._collection = client.collection(collection)

    def get(self, project_id: str) -> ProjectRecord:
        snapshot = self._collection.document(project_id).get()
        if not snapshot.exists:
            raise ProjectOnboardingError("project_not_found")
        return project_from_payload(snapshot.to_dict())

    def save(self, record: ProjectRecord) -> None:
        reference = self._collection.document(record.project_id)
        payload = project_payload(record)
        snapshot = reference.get()
        if snapshot.exists:
            reference.set(payload)
            return
        try:
            reference.create(payload)
        except AlreadyExists:
            reference.set(payload)


class ProjectOnboardingService:
    """State machine; adapters perform GitHub calls outside this policy layer."""

    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry

    def request(self, value: NewProjectRequest) -> ProjectRecord:
        record = ProjectRecord(request=value)
        try:
            self._registry.get(record.project_id)
        except ProjectOnboardingError:
            self._registry.save(record)
            return record
        raise ProjectOnboardingError("project_already_requested")

    def approve(self, project_id: str) -> ProjectRecord:
        record = self._registry.get(project_id)
        if record.status is not ProjectStatus.REQUESTED:
            raise ProjectOnboardingError("project_approval_state_invalid")
        return self._save(replace(record, status=ProjectStatus.APPROVED))

    def begin_provisioning(self, project_id: str) -> ProjectRecord:
        record = self._registry.get(project_id)
        if record.status is not ProjectStatus.APPROVED:
            raise ProjectOnboardingError("project_provisioning_state_invalid")
        return self._save(replace(record, status=ProjectStatus.PROVISIONING))

    def complete_provisioning(self, project_id: str, repository: str, bootstrap_commit: str,
                              github_app_ready: bool) -> ProjectRecord:
        record = self._registry.get(project_id)
        if record.status is not ProjectStatus.PROVISIONING:
            raise ProjectOnboardingError("project_completion_state_invalid")
        if repository != record.request.repository:
            raise ProjectOnboardingError("project_repository_mismatch")
        if not _COMMIT.fullmatch(bootstrap_commit) or not github_app_ready:
            raise ProjectOnboardingError("project_readiness_incomplete")
        return self._save(replace(record, status=ProjectStatus.READY, repository=repository,
                                  bootstrap_commit=bootstrap_commit, github_app_ready=True,
                                  failure_code=None))

    def fail_provisioning(self, project_id: str, code: str) -> ProjectRecord:
        record = self._registry.get(project_id)
        if record.status is not ProjectStatus.PROVISIONING or not re.fullmatch(r"[A-Z0-9_]{3,80}", code):
            raise ProjectOnboardingError("project_failure_invalid")
        return self._save(replace(record, status=ProjectStatus.PROVISION_FAILED, failure_code=code))

    def require_ready_repository(self, project_id: str) -> str:
        record = self._registry.get(project_id)
        if not record.accepts_implementation:
            raise ProjectOnboardingError("project_not_ready_for_implementation")
        return record.repository  # guarded by accepts_implementation

    def _save(self, record: ProjectRecord) -> ProjectRecord:
        self._registry.save(record)
        return record
