"""One-way, replay-safe handoff for an AI implementation diff artifact."""

from __future__ import annotations

import hashlib
import base64
from dataclasses import dataclass
from typing import Protocol

from google.api_core.exceptions import AlreadyExists

from implementation_artifact_verifier import (
    ImplementationArtifactError,
    VerifiedImplementationArtifact,
    verify_implementation_artifact,
)


class ImplementationArtifactHandoffError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedImplementationArtifactRecord:
    execution_id: str
    artifact_sha256: str
    artifact: VerifiedImplementationArtifact


class ImplementationArtifactStore(Protocol):
    def put_once(self, record: VerifiedImplementationArtifactRecord) -> None: ...


class InMemoryImplementationArtifactStore:
    """Test-only create-once store; production publication uses a durable adapter."""

    def __init__(self):
        self.records: dict[str, VerifiedImplementationArtifactRecord] = {}

    def put_once(self, record: VerifiedImplementationArtifactRecord) -> None:
        if record.execution_id in self.records:
            raise ImplementationArtifactHandoffError("implementation_artifact_replayed")
        self.records[record.execution_id] = record


class FirestoreImplementationArtifactStore:
    """Durable, create-only store for verifier-approved implementation diffs.

    The document is written only after the artifact has passed the strict
    verifier.  Publication is intentionally not performed here: consumers
    must re-read this verified record and apply their own approval checks.
    """

    def __init__(self, client, collection: str = "devflow_verified_implementation_artifacts"):
        if not isinstance(collection, str) or not collection or "/" in collection:
            raise ImplementationArtifactHandoffError("implementation_artifact_collection_invalid")
        self._collection = client.collection(collection)

    def put_once(self, record: VerifiedImplementationArtifactRecord) -> None:
        artifact = record.artifact
        payload = {
            "execution_id": record.execution_id,
            "artifact_sha256": record.artifact_sha256,
            "artifact": {
                "task_id": artifact.task_id,
                "spec_hash": artifact.spec_hash,
                "base_commit": artifact.base_commit,
                "diff_b64": base64.b64encode(artifact.diff).decode("ascii"),
                "changed_paths": list(artifact.changed_paths),
                "tests": list(artifact.tests),
                "publication": "verification-only",
            },
        }
        try:
            self._collection.document(record.execution_id).create(payload)
        except AlreadyExists as exc:
            raise ImplementationArtifactHandoffError("implementation_artifact_replayed") from exc


class ImplementationArtifactHandoff:
    """Accept exactly one verified diff from a trusted Broker read path.

    This boundary never creates a branch, PR, commit, or deployment.  A later
    publication adapter can consume only the verified record.
    """

    def __init__(self, store: ImplementationArtifactStore):
        self._store = store

    def receive_from_broker(self, execution_id: str, envelope: dict, payload: bytes) -> VerifiedImplementationArtifactRecord:
        if not isinstance(execution_id, str) or not execution_id:
            raise ImplementationArtifactHandoffError("implementation_execution_id_invalid")
        try:
            artifact = verify_implementation_artifact(payload, envelope)
        except ImplementationArtifactError as exc:
            raise ImplementationArtifactHandoffError("implementation_artifact_rejected") from exc
        record = VerifiedImplementationArtifactRecord(
            execution_id=execution_id,
            artifact_sha256=hashlib.sha256(payload).hexdigest(),
            artifact=artifact,
        )
        self._store.put_once(record)
        return record
