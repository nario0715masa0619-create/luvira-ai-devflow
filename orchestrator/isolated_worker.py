"""Credential-free bootstrap runtime for a future isolated Worker Job.

The bootstrap validates a broker envelope and emits a verification artifact. It
does not import AI SDKs, call the network, access Cloud APIs, clone GitHub, or
publish output.  A later broker can replace the no-op execution step only after
the same boundary and artifact policy have been independently verified.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


REQUIRED_KEYS = {
    "task_id",
    "spec_hash",
    "repository",
    "base_commit",
    "task_type",
    "acceptance_criteria",
    "max_cost_usd",
    "allowed_paths",
    "worker_permissions",
    "publication",
}
FORBIDDEN_NAME_PARTS = ("token", "secret", "credential", "private_key", "apikey", "api_key")
EXPECTED_PERMISSIONS = {
    "github": "none",
    "gcp": "none",
    "secrets": "none",
    "network": "provider-only-via-broker",
}


class WorkerEnvelopeError(ValueError):
    pass


def decode_envelope(encoded: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        envelope = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerEnvelopeError("invalid_envelope_encoding") from exc
    if not isinstance(envelope, dict):
        raise WorkerEnvelopeError("envelope_must_be_object")
    return envelope


def validate_envelope(envelope: dict[str, Any]) -> None:
    names = set(envelope)
    if names != REQUIRED_KEYS:
        raise WorkerEnvelopeError("envelope_schema_mismatch")
    if any(part in name.lower() for name in names for part in FORBIDDEN_NAME_PARTS):
        raise WorkerEnvelopeError("credential_field_forbidden")
    if not isinstance(envelope["task_id"], str) or not envelope["task_id"]:
        raise WorkerEnvelopeError("task_id_required")
    if not isinstance(envelope["spec_hash"], str) or len(envelope["spec_hash"]) != 64:
        raise WorkerEnvelopeError("spec_hash_invalid")
    if not isinstance(envelope["base_commit"], str) or not envelope["base_commit"]:
        raise WorkerEnvelopeError("base_commit_required")
    if not isinstance(envelope["allowed_paths"], (list, tuple)) or not envelope["allowed_paths"]:
        raise WorkerEnvelopeError("allowed_paths_required")
    if envelope["worker_permissions"] != EXPECTED_PERMISSIONS:
        raise WorkerEnvelopeError("worker_permissions_not_isolated")
    if envelope["publication"] != "verification-artifact-only":
        raise WorkerEnvelopeError("publication_not_isolated")


def build_bootstrap_artifact(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return the only output the bootstrap runtime is allowed to create."""
    return {
        "schema": "luvira.devflow.worker-bootstrap.v1",
        "task_id": envelope["task_id"],
        "spec_hash": envelope["spec_hash"],
        "base_commit": envelope["base_commit"],
        "status": "ENVELOPE_VALIDATED_NO_MODEL_EXECUTION",
        "changed_paths": [],
        "tests": [],
        "publication": "none",
    }


def main() -> None:
    encoded = os.environ.get("WORKER_ENVELOPE_B64", "")
    output_path = Path(os.environ.get("WORKER_OUTPUT_PATH", "/workspace/output/result.json"))
    envelope = decode_envelope(encoded)
    validate_envelope(envelope)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = build_bootstrap_artifact(envelope)
    payload = json.dumps(artifact, sort_keys=True).encode("utf-8")
    output_path.write_bytes(payload)
    # This is the only bootstrap transport output. It contains no source code,
    # credentials, prompt, diff, or publication authority. The Broker reads it
    # only after the Cloud Run Job reports success.
    print("LUVIRA_BOOTSTRAP_ARTIFACT_B64=" + base64.b64encode(payload).decode("ascii"))


if __name__ == "__main__":
    main()
