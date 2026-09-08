"""One-way, replay-safe handoff for an AI implementation diff artifact."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

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
