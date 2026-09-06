"""Fail-closed Verification Plane contract for isolated Worker artifacts.

This module deliberately has no storage, network, provider, GitHub, or secret
dependency.  A future artifact transport must give the Verifier untrusted bytes
and the broker's original WorkerEnvelope; it must not grant storage access to
the Worker in order to bypass these checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from isolated_worker import WorkerEnvelopeError, validate_envelope


MAX_ARTIFACT_BYTES = 64 * 1024
ARTIFACT_SCHEMA = "luvira.devflow.worker-bootstrap.v1"
REQUIRED_ARTIFACT_KEYS = {
    "schema",
    "task_id",
    "spec_hash",
    "base_commit",
    "status",
    "changed_paths",
    "tests",
    "publication",
}


class ArtifactVerificationError(ValueError):
    """An artifact is malformed, out of scope, or not publishable."""


@dataclass(frozen=True)
class VerifiedArtifact:
    task_id: str
    spec_hash: str
    base_commit: str
    status: str
    changed_paths: tuple[str, ...]
    tests: tuple[Any, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactVerificationError("artifact_duplicate_key")
        value[key] = item
    return value


def parse_artifact(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_ARTIFACT_BYTES:
        raise ArtifactVerificationError("artifact_size_invalid")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError("artifact_json_invalid") from exc
    if not isinstance(value, dict):
        raise ArtifactVerificationError("artifact_must_be_object")
    return value


def verify_artifact(payload: bytes, envelope: dict[str, Any]) -> VerifiedArtifact:
    """Verify a Worker artifact against the exact envelope issued by Broker.

    The accepted bootstrap status is intentionally non-publishable: it proves
    only that the constrained runtime received a valid envelope.  Any future
    implementation artifact needs a new schema and an independent review.
    """
    try:
        validate_envelope(envelope)
    except WorkerEnvelopeError as exc:
        raise ArtifactVerificationError("broker_envelope_invalid") from exc

    artifact = parse_artifact(payload)
    if set(artifact) != REQUIRED_ARTIFACT_KEYS:
        raise ArtifactVerificationError("artifact_schema_mismatch")
    if artifact["schema"] != ARTIFACT_SCHEMA:
        raise ArtifactVerificationError("artifact_schema_unsupported")
    for key in ("task_id", "spec_hash", "base_commit"):
        if artifact[key] != envelope[key]:
            raise ArtifactVerificationError(f"artifact_{key}_mismatch")
    if artifact["status"] != "ENVELOPE_VALIDATED_NO_MODEL_EXECUTION":
        raise ArtifactVerificationError("artifact_status_not_bootstrap")
    if artifact["publication"] != "none":
        raise ArtifactVerificationError("artifact_publication_forbidden")
    if not isinstance(artifact["changed_paths"], list) or artifact["changed_paths"]:
        raise ArtifactVerificationError("artifact_changes_not_allowed")
    if not isinstance(artifact["tests"], list) or artifact["tests"]:
        raise ArtifactVerificationError("artifact_tests_not_allowed")
    return VerifiedArtifact(
        task_id=artifact["task_id"],
        spec_hash=artifact["spec_hash"],
        base_commit=artifact["base_commit"],
        status=artifact["status"],
        changed_paths=(),
        tests=(),
    )
