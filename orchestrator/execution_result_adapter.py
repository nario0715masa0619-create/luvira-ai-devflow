"""Trusted Broker adapter for collecting bootstrap Worker results.

Only the Broker plane implements these protocols.  The isolated Worker merely
prints a constrained, non-secret bootstrap artifact after writing its local
file.  It is not given a storage credential or an endpoint to call.
"""

from __future__ import annotations

import base64
from typing import Protocol

from artifact_handoff import ArtifactHandoff, VerifiedArtifactRecord


RESULT_PREFIX = "LUVIRA_BOOTSTRAP_ARTIFACT_B64="


class ExecutionResultAdapterError(ValueError):
    """Cloud execution state or its constrained result cannot be trusted."""


class WorkerExecutionClient(Protocol):
    def start(self, envelope: dict) -> str: ...
    def wait_for_success(self, execution_id: str) -> None: ...


class WorkerExecutionLogReader(Protocol):
    def read_stdout(self, execution_id: str) -> str: ...


def extract_bootstrap_artifact(stdout: str) -> bytes:
    """Extract exactly one explicitly prefixed, base64-encoded artifact line."""
    if not isinstance(stdout, str):
        raise ExecutionResultAdapterError("worker_stdout_invalid")
    lines = [line.removeprefix(RESULT_PREFIX) for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(lines) != 1 or not lines[0]:
        raise ExecutionResultAdapterError("worker_bootstrap_artifact_missing_or_ambiguous")
    try:
        return base64.b64decode(lines[0], validate=True)
    except ValueError as exc:
        raise ExecutionResultAdapterError("worker_bootstrap_artifact_encoding_invalid") from exc


class BootstrapResultAdapter:
    """Start a bootstrap Job and hand off its sole allowed result once."""

    def __init__(self, client: WorkerExecutionClient, log_reader: WorkerExecutionLogReader, handoff: ArtifactHandoff):
        self._client = client
        self._log_reader = log_reader
        self._handoff = handoff

    def execute_and_verify(self, envelope: dict) -> VerifiedArtifactRecord:
        execution_id = self._client.start(envelope)
        if not isinstance(execution_id, str) or not execution_id:
            raise ExecutionResultAdapterError("worker_execution_start_invalid")
        # No log is read and no artifact is persisted until Cloud Run reports
        # a successful, completed execution.
        self._client.wait_for_success(execution_id)
        artifact_bytes = extract_bootstrap_artifact(self._log_reader.read_stdout(execution_id))
        return self._handoff.receive_from_broker(execution_id, envelope, artifact_bytes)
