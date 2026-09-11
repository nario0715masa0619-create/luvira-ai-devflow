"""Build a bounded, read-only source snapshot for an approved task."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Callable


MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_FILES = 64


class SourceSnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    base_commit: str
    content: bytes
    paths: tuple[str, ...]
    # The exact immutable blobs behind ``content``.  The presentation given to
    # the model remains compact text, while verification can apply its diff to
    # these bytes before an artifact is ever made durable.
    files: tuple[tuple[str, bytes], ...]
    # A metadata-only manifest of every path eligible for output.  This is
    # deliberately broader than ``paths``: the model receives only the
    # approved source subset, while the verifier must still know whether a
    # claimed creation already exists at the immutable base.
    baseline_paths: tuple[str, ...]


def _allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


class SourceSnapshotBuilder:
    """Fetches blobs only from an immutable commit through an injected reader."""

    def __init__(self, list_tree: Callable[[str], list[dict]], read_blob: Callable[[str], dict]):
        self._list_tree, self._read_blob = list_tree, read_blob

    def build(self, base_commit: str, allowed_paths: tuple[str, ...],
              baseline_paths: tuple[str, ...] | None = None) -> SourceSnapshot:
        if not isinstance(base_commit, str) or len(base_commit) != 40 or not allowed_paths:
            raise SourceSnapshotError("source_snapshot_input_invalid")
        tree = self._list_tree(base_commit)
        selected = []
        manifest = []
        output_scope = baseline_paths or allowed_paths
        for item in tree:
            path = item.get("path") if isinstance(item, dict) else None
            if item.get("type") == "blob" and isinstance(path, str) and _allowed(path, allowed_paths):
                selected.append((path, item.get("sha")))
            if item.get("type") == "blob" and isinstance(path, str) and _allowed(path, output_scope):
                manifest.append(path)
        if not selected or len(selected) > MAX_FILES:
            raise SourceSnapshotError("source_snapshot_scope_invalid")
        chunks = []
        files = []
        total = 0
        for path, sha in sorted(selected):
            blob = self._read_blob(sha)
            try:
                # GitHub wraps blob content as Base64 text.  Remove only
                # transport whitespace, then strictly validate the payload.
                encoded = blob["content"]
                if not isinstance(encoded, str):
                    raise TypeError("blob content must be text")
                content = base64.b64decode(
                    encoded.encode("ascii").translate(None, b" \t\r\n"), validate=True,
                )
                text = content.decode("utf-8")
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, UnicodeEncodeError) as exc:
                raise SourceSnapshotError("source_snapshot_blob_invalid") from exc
            total += len(content)
            if total > MAX_SNAPSHOT_BYTES:
                raise SourceSnapshotError("source_snapshot_too_large")
            chunks.append(f"--- {path}\n{text}\n")
            files.append((path, content))
        return SourceSnapshot(
            base_commit, "".join(chunks).encode("utf-8"),
            tuple(path for path, _ in sorted(selected)), tuple(files), tuple(sorted(manifest)),
        )
