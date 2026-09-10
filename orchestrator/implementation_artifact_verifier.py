"""Fail-closed verifier for a future AI-produced, diff-only Worker artifact."""

from __future__ import annotations

import base64
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

from github_verified_publisher import VerifiedPublicationError, apply_patch, parse_verified_diff

MAX_ARTIFACT_BYTES = 512 * 1024
MAX_DIFF_BYTES = 384 * 1024
SCHEMA = "luvira.devflow.implementation-artifact.v1"
MODEL_SCHEMA = "luvira.devflow.implementation-artifact.v2"
REQUIRED_KEYS = {"schema", "task_id", "spec_hash", "base_commit", "diff_b64", "changed_paths", "tests", "publication"}
MODEL_REQUIRED_KEYS = {"schema", "task_id", "spec_hash", "base_commit", "files", "changed_paths", "tests", "publication"}
PROTECTED_PREFIXES = (".git/", ".github/", "security/", "infra/", "terraform/")
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".pyc", ".pyo")
SECRET_MARKERS = ("-----BEGIN ", "AKIA", "AIza", "ghp_", "github_pat_", "xoxb-")
PATH_LINE = re.compile(r"^(---|\+\+\+) (?:a/|b/)?(.+?)(?:\t.*)?$")
HUNK_LINE = re.compile(r"^@@ -0(?:,0)? \+\d+(?:,\d+)? @@")


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
    if not isinstance(artifact, dict):
        raise ImplementationArtifactError("artifact_schema_mismatch")
    expected = MODEL_REQUIRED_KEYS if artifact.get("schema") == MODEL_SCHEMA else REQUIRED_KEYS
    if set(artifact) != expected:
        raise ImplementationArtifactError("artifact_schema_mismatch")
    return artifact


def _deterministic_diff(artifact: dict[str, Any], baseline_files: Iterable[tuple[str, bytes]] | None) -> bytes:
    """Create the sole unified diff from immutable bytes and full-file output.

    Models are good at writing a complete target file but unreliable at
    reproducing every hunk offset and context line.  This keeps patch syntax
    and applicability inside the trusted Broker boundary.
    """
    if baseline_files is None:
        raise ImplementationArtifactError("artifact_baseline_required")
    try:
        baseline = {_path(path): content for path, content in baseline_files}
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError("artifact_baseline_invalid") from exc
    if any(not isinstance(content, bytes) for content in baseline.values()):
        raise ImplementationArtifactError("artifact_baseline_invalid")
    entries = artifact.get("files")
    if not isinstance(entries, list) or not entries:
        raise ImplementationArtifactError("artifact_files_required")
    rendered: list[str] = []
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "content"}:
            raise ImplementationArtifactError("artifact_files_invalid")
        path, content = _path(entry["path"]), entry["content"]
        if not isinstance(content, str) or not content.endswith("\n"):
            raise ImplementationArtifactError("artifact_file_content_invalid")
        if path in paths:
            raise ImplementationArtifactError("artifact_file_duplicate")
        paths.append(path)
        try:
            target = content.encode("utf-8")
            original = baseline.get(path)
            original_text = [] if original is None else original.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError as exc:
            raise ImplementationArtifactError("artifact_file_content_invalid") from exc
        # A model can include an inspected file whose replacement text is
        # identical to the immutable base.  It is not a publishable change;
        # discard it instead of letting harmless candidate metadata reject
        # the independently verified changes in the same artifact.
        if original == target:
            continue
        before = "/dev/null" if original is None else f"a/{path}"
        after = f"b/{path}"
        rendered.extend(difflib.unified_diff(
            original_text, content.splitlines(keepends=True), fromfile=before, tofile=after,
            lineterm="\n",
        ))
    if paths != sorted(paths):
        raise ImplementationArtifactError("artifact_files_not_sorted")
    declared = artifact.get("changed_paths")
    if not isinstance(declared, list):
        raise ImplementationArtifactError("artifact_changed_paths_mismatch")
    try:
        declared_paths = tuple(_path(item) for item in declared)
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError("artifact_changed_paths_mismatch") from exc
    if declared_paths != tuple(sorted(set(declared_paths))) or not set(declared_paths).issubset(paths):
        raise ImplementationArtifactError("artifact_changed_paths_mismatch")
    if not rendered:
        raise ImplementationArtifactError("artifact_no_effect")
    return "".join(rendered).encode("utf-8")


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


def _canonicalize_unambiguous_creates(diff: bytes, baseline_paths: Iterable[str] | None) -> bytes:
    """Normalize only a provable new-file header emitted in existing-file form.

    Providers sometimes emit ``a/path`` / ``b/path`` for a creation even
    though the hunk is exclusively ``-0,0`` plus-lines.  That is an encoding
    error, not a content change.  We repair that one syntax defect before the
    immutable-artifact boundary.  Every ambiguous mismatch remains rejected.
    """
    if baseline_paths is None:
        return diff
    try:
        baseline = {_path(path) for path in baseline_paths}
        lines = diff.decode("utf-8").splitlines(keepends=True)
    except (TypeError, UnicodeDecodeError) as exc:
        raise ImplementationArtifactError("artifact_baseline_invalid") from exc
    result = list(lines)
    index = 0
    while index < len(lines):
        old_match = PATH_LINE.match(lines[index].rstrip("\r\n"))
        if not old_match or old_match.group(1) != "---":
            index += 1
            continue
        if index + 1 >= len(lines):
            break
        new_match = PATH_LINE.match(lines[index + 1].rstrip("\r\n"))
        if not new_match or new_match.group(1) != "+++":
            index += 1
            continue
        old_raw, new_raw = old_match.group(2), new_match.group(2)
        old_path = None if old_raw == "/dev/null" else _path(old_raw)
        new_path = None if new_raw == "/dev/null" else _path(new_raw)
        next_header = index + 2
        while next_header < len(lines):
            candidate = PATH_LINE.match(lines[next_header].rstrip("\r\n"))
            if candidate and candidate.group(1) == "---":
                break
            next_header += 1
        section = [line.rstrip("\r\n") for line in lines[index + 2:next_header]]
        hunk_positions = [position for position, line in enumerate(section) if line.startswith("@@ ")]
        is_creation = (
            old_path is not None and new_path == old_path and old_path not in baseline
            and bool(hunk_positions)
        )
        if is_creation:
            for position in hunk_positions:
                if not HUNK_LINE.match(section[position]):
                    is_creation = False
                    break
                end = next((later for later in hunk_positions if later > position), len(section))
                if any(line and not line.startswith("+") and line != "\\ No newline at end of file"
                       for line in section[position + 1:end]):
                    is_creation = False
                    break
        if is_creation:
            ending = "\r\n" if lines[index].endswith("\r\n") else "\n"
            result[index] = f"--- /dev/null{ending}"
            result[index + 1] = f"+++ b/{new_path}{ending}"
        index = next_header
    return "".join(result).encode("utf-8")


def _validate_exact_apply(diff: bytes, baseline_files: Iterable[tuple[str, bytes]] | None) -> None:
    """Apply each verified patch against immutable source bytes in memory."""
    if baseline_files is None:
        return
    try:
        files = {_path(path): content for path, content in baseline_files}
    except (TypeError, ValueError) as exc:
        raise ImplementationArtifactError("artifact_baseline_invalid") from exc
    if any(not isinstance(content, bytes) for content in files.values()):
        raise ImplementationArtifactError("artifact_baseline_invalid")
    try:
        for patch in parse_verified_diff(diff):
            if patch.old_path is not None and patch.old_path not in files:
                raise ImplementationArtifactError("artifact_diff_base_mismatch")
            apply_patch(b"" if patch.old_path is None else files[patch.old_path], patch)
    except ImplementationArtifactError:
        raise
    except VerifiedPublicationError as exc:
        code = str(exc)
        if code in {"publication_patch_context_mismatch", "publication_patch_offset_invalid"}:
            raise ImplementationArtifactError("artifact_diff_context_mismatch") from exc
        raise ImplementationArtifactError("artifact_diff_apply_invalid") from exc


def verify_implementation_artifact(payload: bytes, envelope: dict[str, Any], *,
                                   baseline_paths: Iterable[str] | None = None,
                                   baseline_files: Iterable[tuple[str, bytes]] | None = None) -> VerifiedImplementationArtifact:
    """Validate an AI result against the immutable Worker envelope."""
    artifact = _decode(payload)
    if artifact["schema"] not in {SCHEMA, MODEL_SCHEMA} or artifact["publication"] != "verification-only":
        raise ImplementationArtifactError("artifact_schema_unsupported")
    for field in ("task_id", "spec_hash", "base_commit"):
        if artifact[field] != envelope.get(field):
            raise ImplementationArtifactError(f"artifact_{field}_mismatch")
    if artifact["schema"] == MODEL_SCHEMA:
        diff = _deterministic_diff(artifact, baseline_files)
    else:
        try:
            diff = base64.b64decode(artifact["diff_b64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise ImplementationArtifactError("artifact_diff_encoding_invalid") from exc
        diff = _canonicalize_unambiguous_creates(diff, baseline_paths)
    files = _diff_files(diff)
    _validate_baseline(files, baseline_paths)
    _validate_exact_apply(diff, baseline_files)
    paths = tuple(sorted({new_path or old_path for old_path, new_path in files if new_path or old_path}))
    if artifact["schema"] != MODEL_SCHEMA:
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
