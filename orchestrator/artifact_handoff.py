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
        self._store.put_once(record)
        return record
