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
    def get(self, execution_id: str) -> VerifiedImplementationArtifactRecord: ...


class InMemoryImplementationArtifactStore:
    """Test-only create-once store; production publication uses a durable adapter."""

    def __init__(self):
        self.records: dict[str, VerifiedImplementationArtifactRecord] = {}

    def put_once(self, record: VerifiedImplementationArtifactRecord) -> None:
        if record.execution_id in self.records:
            raise ImplementationArtifactHandoffError("implementation_artifact_replayed")
        self.records[record.execution_id] = record

    def get(self, execution_id: str) -> VerifiedImplementationArtifactRecord:
        return self.records[execution_id]


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

    def get(self, execution_id: str) -> VerifiedImplementationArtifactRecord:
        snapshot = self._collection.document(execution_id).get()
        if not snapshot.exists:
            raise ImplementationArtifactHandoffError("implementation_artifact_not_found")
        try:
            value = snapshot.to_dict()
            artifact = value["artifact"]
            return VerifiedImplementationArtifactRecord(
                execution_id=value["execution_id"], artifact_sha256=value["artifact_sha256"],
                artifact=VerifiedImplementationArtifact(
                    task_id=artifact["task_id"], spec_hash=artifact["spec_hash"], base_commit=artifact["base_commit"],
                    diff=base64.b64decode(artifact["diff_b64"], validate=True),
                    changed_paths=tuple(artifact["changed_paths"]), tests=tuple(artifact["tests"]),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ImplementationArtifactHandoffError("implementation_artifact_invalid") from exc


class ImplementationArtifactHandoff:
    """Accept exactly one verified diff from a trusted Broker read path.

    This boundary never creates a branch, PR, commit, or deployment.  A later
    publication adapter can consume only the verified record.
    """

    def __init__(self, store: ImplementationArtifactStore):
        self._store = store

    def receive_from_broker(self, execution_id: str, envelope: dict, payload: bytes,
                            *, baseline_paths=None) -> VerifiedImplementationArtifactRecord:
        if not isinstance(execution_id, str) or not execution_id:
            raise ImplementationArtifactHandoffError("implementation_execution_id_invalid")
        try:
            artifact = verify_implementation_artifact(payload, envelope, baseline_paths=baseline_paths)
        except ImplementationArtifactError as exc:
            # Persist only the verifier code, never the model response or
            # source snapshot.  Operators can distinguish a contract mismatch
            # from an unsafe diff without exposing provider content in logs.
            raise ImplementationArtifactHandoffError(str(exc)) from exc
        record = VerifiedImplementationArtifactRecord(
            execution_id=execution_id,
            artifact_sha256=hashlib.sha256(payload).hexdigest(),
            artifact=artifact,
        )
        try:
            self._store.put_once(record)
        except ImplementationArtifactHandoffError:
            raise
        except Exception as exc:
            # The model artifact may be valid even when durable storage is
            # unavailable.  Keep that operational failure distinct from a
            # verifier rejection and retain no provider content.
            raise ImplementationArtifactHandoffError("implementation_artifact_store_unavailable") from exc
        return record
