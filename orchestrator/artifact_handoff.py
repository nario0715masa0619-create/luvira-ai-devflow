"""One-way Broker-to-Verifier handoff for isolated Worker results.

The Worker never calls this module and has no artifact-store credential.  A
future execution adapter running in the trusted Broker plane retrieves the
ephemeral Worker result, then supplies it here along with the exact envelope it
issued.  The Verifier sees only bytes and fails closed before persistence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol
from google.api_core.exceptions import AlreadyExists

from artifact_verifier import ArtifactVerificationError, VerifiedArtifact, verify_artifact


EXECUTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")


class ArtifactHandoffError(ValueError):
    """The trusted Broker cannot safely hand off an execution result."""


class VerifiedArtifactStore(Protocol):
    """Durable implementations must atomically reject a repeated execution id."""

    def put_once(self, record: "VerifiedArtifactRecord") -> None: ...


@dataclass(frozen=True)
class VerifiedArtifactRecord:
    execution_id: str
    artifact_sha256: str
    artifact: VerifiedArtifact


class InMemoryVerifiedArtifactStore:
    """Test-only store. Production must provide an atomic durable equivalent."""

    def __init__(self) -> None:
        self.records: dict[str, VerifiedArtifactRecord] = {}

    def put_once(self, record: VerifiedArtifactRecord) -> None:
        if record.execution_id in self.records:
            raise ArtifactHandoffError("artifact_execution_already_received")
        self.records[record.execution_id] = record


class FirestoreVerifiedArtifactStore:
    """Durable, create-only result store; duplicate executions fail closed."""

    def __init__(self, client, collection: str = "devflow_verified_artifacts"):
        self._collection = client.collection(collection)

    def put_once(self, record: VerifiedArtifactRecord) -> None:
        payload = {
            "execution_id": record.execution_id,
            "artifact_sha256": record.artifact_sha256,
            "artifact": {
                "task_id": record.artifact.task_id,
                "spec_hash": record.artifact.spec_hash,
                "base_commit": record.artifact.base_commit,
                "status": record.artifact.status,
                "changed_paths": list(record.artifact.changed_paths),
                "tests": list(record.artifact.tests),
            },
        }
        try:
            self._collection.document(record.execution_id).create(payload)
        except AlreadyExists as exc:
            raise ArtifactHandoffError("artifact_execution_already_received") from exc


class ArtifactHandoff:
    """Accept exactly one verified result for a trusted worker execution."""

    def __init__(self, store: VerifiedArtifactStore):
        self._store = store

    def receive_from_broker(self, execution_id: str, envelope: dict, artifact_bytes: bytes) -> VerifiedArtifactRecord:
        if not isinstance(execution_id, str) or not EXECUTION_ID.fullmatch(execution_id):
            raise ArtifactHandoffError("worker_execution_id_invalid")
        try:
            artifact = verify_artifact(artifact_bytes, envelope)
        except ArtifactVerificationError as exc:
            raise ArtifactHandoffError("worker_artifact_rejected") from exc

        record = VerifiedArtifactRecord(
            execution_id=execution_id,
            artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            artifact=artifact,
        )
        try:
            self._store.put_once(record)
        except ArtifactHandoffError:
            raise
        except Exception as exc:
            raise ArtifactHandoffError("worker_artifact_store_unavailable") from exc
        return record
