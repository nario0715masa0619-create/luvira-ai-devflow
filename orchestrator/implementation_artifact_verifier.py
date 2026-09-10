"""Fail-closed verifier for a future AI-produced, diff-only Worker artifact."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

MAX_ARTIFACT_BYTES = 512 * 1024
MAX_DIFF_BYTES = 384 * 1024
SCHEMA = "luvira.devflow.implementation-artifact.v1"
REQUIRED_KEYS = {"schema", "task_id", "spec_hash", "base_commit", "diff_b64", "changed_paths", "tests", "publication"}
PROTECTED_PREFIXES = (".git/", ".github/", "security/", "infra/", "terraform/")
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".pyc", ".pyo")
SECRET_MARKERS = ("-----BEGIN ", "AKIA", "AIza", "ghp_", "github_pat_", "xoxb-")
PATH_LINE = re.compile(r"^(---|\+\+\+) (?:a/|b/)?(.+?)(?:\t.*)?$")


class ImplementationArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedImplementationArtifact:
    task_id: str
    spec_hash: str
    base_commit: str
    diff: bytes
    changed_paths: tuple[str, ...]
    tests: tuple[dict[str, str], ...]


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ImplementationArtifactError("artifact_duplicate_key")
        value[key] = item
    return value


def _path(value: str) -> str:
    if not isinstance(value, str) or not value or value == "/dev/null":
        raise ImplementationArtifactError("artifact_path_invalid")
    candidate = PurePosixPath(value)
    rendered = candidate.as_posix()
    if candidate.is_absolute() or ".." in candidate.parts or rendered in {".", ""}:
        raise ImplementationArtifactError("artifact_path_invalid")
    if rendered.startswith(PROTECTED_PREFIXES) or rendered.endswith(FORBIDDEN_SUFFIXES):
        raise ImplementationArtifactError("artifact_path_protected")
    return rendered


def _decode(value: bytes) -> dict[str, Any]:
    if not isinstance(value, bytes) or not value or len(value) > MAX_ARTIFACT_BYTES:
        raise ImplementationArtifactError("artifact_size_invalid")
    try:
        artifact = json.loads(value.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImplementationArtifactError("artifact_json_invalid") from exc
    if not isinstance(artifact, dict) or set(artifact) != REQUIRED_KEYS:
        raise ImplementationArtifactError("artifact_schema_mismatch")
    return artifact


def _diff_files(diff: bytes) -> tuple[tuple[str | None, str | None], ...]:
    if not diff or len(diff) > MAX_DIFF_BYTES or b"\0" in diff:
        raise ImplementationArtifactError("artifact_diff_invalid")
    try:
        text = diff.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImplementationArtifactError("artifact_diff_not_utf8") from exc
    if any(marker in text for marker in SECRET_MARKERS):
        raise ImplementationArtifactError("artifact_secret_detected")
    old, new, files = None, None, []
    have_old, have_new = False, False
    for line in text.splitlines():
        match = PATH_LINE.match(line)
        if not match:
            continue
        side, raw = match.groups()
        candidate = None if raw == "/dev/null" else _path(raw)
        if side == "---":
            if have_old or have_new:
                raise ImplementationArtifactError("artifact_diff_header_invalid")
            old = candidate
            have_old = True
        else:
            if not have_old or have_new:
                raise ImplementationArtifactError("artifact_diff_header_invalid")
            new = candidate
            have_new = True
            files.append((old, new))
            old, new, have_old, have_new = None, None, False, False
    if have_old or have_new or not files:
        raise ImplementationArtifactError("artifact_diff_header_invalid")
    if any(old_path is None and new_path is None for old_path, new_path in files):
        raise ImplementationArtifactError("artifact_diff_header_invalid")
    return tuple(files)


def _validate_baseline(files: tuple[tuple[str | None, str | None], ...],
                       baseline_paths: Iterable[str] | None) -> None:
    """Require diff headers to describe the immutable source snapshot.

    The publication adapter can safely apply a patch only when its header
    declares whether the base contains the file.  Without this check a model
    can label a new file as ``a/path`` and pass structural validation, only to
    fail after reaching the GitHub write boundary.
    """
    if baseline_paths is None:
        return
    try:
        baseline = {_path(path) for path in baseline_paths}
    except TypeError as exc:
        raise ImplementationArtifactError("artifact_baseline_invalid") from exc
    for old_path, new_path in files:
        if old_path is not None and old_path not in baseline:
            raise ImplementationArtifactError("artifact_diff_base_mismatch")
        if old_path is None and new_path in baseline:
            raise ImplementationArtifactError("artifact_diff_base_mismatch")
        # The publisher supports create, modify, and delete.  A rename needs
        # an explicit atomic Git operation, so reject it before publication.
        if old_path is not None and new_path is not None and old_path != new_path:
            raise ImplementationArtifactError("artifact_rename_unsupported")


def verify_implementation_artifact(payload: bytes, envelope: dict[str, Any], *,
                                   baseline_paths: Iterable[str] | None = None) -> VerifiedImplementationArtifact:
    """Validate an AI result against the immutable Worker envelope."""
    artifact = _decode(payload)
    if artifact["schema"] != SCHEMA or artifact["publication"] != "verification-only":
        raise ImplementationArtifactError("artifact_schema_unsupported")
    for field in ("task_id", "spec_hash", "base_commit"):
        if artifact[field] != envelope.get(field):
            raise ImplementationArtifactError(f"artifact_{field}_mismatch")
    try:
        diff = base64.b64decode(artifact["diff_b64"], validate=True)
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError("artifact_diff_encoding_invalid") from exc
    files = _diff_files(diff)
    _validate_baseline(files, baseline_paths)
    paths = tuple(sorted({new_path or old_path for old_path, new_path in files if new_path or old_path}))
    declared = artifact["changed_paths"]
    if not isinstance(declared, list) or tuple(sorted({_path(item) for item in declared})) != paths:
        raise ImplementationArtifactError("artifact_changed_paths_mismatch")
    allowed = envelope.get("allowed_paths")
    if not isinstance(allowed, (list, tuple)) or not allowed:
        raise ImplementationArtifactError("envelope_scope_invalid")
    if any(not any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in allowed) for path in paths):
        raise ImplementationArtifactError("artifact_path_out_of_scope")
    tests = artifact["tests"]
    # The generation provider cannot execute the repository's test suite.
    # Treating its self-reported test result as a publication gate both
    # encourages hallucinated "passed" claims and rejects honest empty
    # reports.  The generated draft PR is instead verified by the repository's
    # required GitHub checks before any human can merge it.
    if not isinstance(tests, list):
        raise ImplementationArtifactError("artifact_tests_required")
    normalized_tests = []
    for item in tests:
        if not isinstance(item, dict) or set(item) != {"name", "status"} or not isinstance(item["name"], str) or item["status"] not in {"passed", "skipped"}:
            raise ImplementationArtifactError("artifact_tests_invalid")
        normalized_tests.append({"name": item["name"], "status": item["status"]})
    return VerifiedImplementationArtifact(artifact["task_id"], artifact["spec_hash"], artifact["base_commit"], diff, paths, tuple(normalized_tests))
