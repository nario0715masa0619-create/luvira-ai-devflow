"""Publish only a previously verified unified diff through the GitHub API."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API = "https://api.github.com"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class VerifiedPublicationError(ValueError):
    pass


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: tuple[tuple[int, tuple[str, ...]], ...]


def _path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    prefix = "a/" if value.startswith("a/") else "b/" if value.startswith("b/") else ""
    path = value[len(prefix):]
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise VerifiedPublicationError("publication_patch_path_invalid")
    return path


def parse_verified_diff(diff: bytes) -> tuple[FilePatch, ...]:
    """Parse a small, text-only unified diff already accepted by the verifier."""
    try:
        lines = diff.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise VerifiedPublicationError("publication_patch_not_utf8") from exc
    patches, old_path, new_path, hunks = [], None, None, []
    have_header = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            if have_header:
                if not hunks:
                    raise VerifiedPublicationError("publication_patch_hunk_required")
                patches.append(FilePatch(old_path, new_path, tuple(hunks)))
                old_path, new_path, hunks = None, None, []
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise VerifiedPublicationError("publication_patch_header_invalid")
            old_path, new_path = _path(line[4:].split("\t", 1)[0]), _path(lines[index + 1][4:].split("\t", 1)[0])
            if old_path is None and new_path is None:
                raise VerifiedPublicationError("publication_patch_header_invalid")
            have_header, hunks, index = True, [], index + 2
            continue
        match = HUNK.match(line)
        if match:
            if not have_header:
                raise VerifiedPublicationError("publication_patch_header_invalid")
            start = int(match.group(1))
            body, index = [], index + 1
            while index < len(lines) and not lines[index].startswith(("--- ", "@@ ")):
                if lines[index].startswith((" ", "+", "-")):
                    body.append(lines[index])
                elif lines[index] != "\\ No newline at end of file":
                    raise VerifiedPublicationError("publication_patch_hunk_invalid")
                index += 1
            hunks.append((start, tuple(body)))
            continue
        index += 1
    if have_header:
        patches.append(FilePatch(old_path, new_path, tuple(hunks)))
    if not patches or any(not item.hunks for item in patches):
        raise VerifiedPublicationError("publication_patch_hunk_required")
    return tuple(patches)


def apply_patch(original: bytes, patch: FilePatch) -> bytes:
    """Apply context-checked hunks; a mismatch aborts before any GitHub write."""
    source = [] if patch.old_path is None else original.decode("utf-8").splitlines()
    output, cursor = [], 0
    for start, hunk in patch.hunks:
        # A standard creation hunk is ``@@ -0,0 +1,N @@``.  It applies at
        # the beginning of an empty source rather than at a negative index.
        offset = 0 if patch.old_path is None and start == 0 else start - 1
        if offset < cursor or offset > len(source):
            raise VerifiedPublicationError("publication_patch_offset_invalid")
        output.extend(source[cursor:offset])
        cursor = offset
        for line in hunk:
            kind, value = line[:1], line[1:]
            if kind == " ":
                if cursor >= len(source) or source[cursor] != value:
                    raise VerifiedPublicationError("publication_patch_context_mismatch")
                output.append(value); cursor += 1
            elif kind == "-":
                if cursor >= len(source) or source[cursor] != value:
                    raise VerifiedPublicationError("publication_patch_context_mismatch")
                cursor += 1
            elif kind == "+":
                output.append(value)
    output.extend(source[cursor:])
    return ("\n".join(output) + "\n").encode("utf-8")


class GitHubVerifiedPublisher:
    """The sole GitHub write adapter; it accepts a verified record, not AI text."""

    def __init__(self, repository: str, token: str, opener=urlopen):
        if not isinstance(repository, str) or repository.count("/") != 1 or not token:
            raise VerifiedPublicationError("publication_config_invalid")
        self.repository, self._token, self._opener = repository, token, opener

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = Request(API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "luvira-devflow-verified-publisher/1",
        })
        try:
            with self._opener(request, timeout=20) as response:
                payload = response.read()
        except HTTPError as exc:
            raise VerifiedPublicationError(f"publication_github_http_{exc.code}") from exc
        except Exception as exc:
            raise VerifiedPublicationError("publication_github_unavailable") from exc
        if not payload:
            return {}
        try:
            value = json.loads(payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerifiedPublicationError("publication_github_response_invalid") from exc
        if not isinstance(value, dict):
            raise VerifiedPublicationError("publication_github_response_invalid")
        return value

    def publish(self, *, task_id: str, execution_id: str, base_commit: str, diff: bytes, title: str, body: str) -> str:
        patches = parse_verified_diff(diff)
        repo = quote(self.repository, safe="/")
        branch = f"luvira/{task_id[:32]}-{execution_id[:12]}"
        # Validate every hunk against the immutable base before creating a ref.
        writes = []
        for patch in patches:
            path = patch.old_path or patch.new_path
            source = b""
            sha = None
            if patch.old_path is not None:
                try:
                    current = self._call("GET", f"/repos/{repo}/contents/{quote(patch.old_path, safe='/')}?ref={quote(base_commit, safe='')}")
                except VerifiedPublicationError as exc:
                    if str(exc) == "publication_github_http_404":
                        raise VerifiedPublicationError("publication_base_content_missing") from exc
                    raise
                try:
                    source = base64.b64decode(current["content"], validate=True); sha = current["sha"]
                except (KeyError, TypeError, ValueError) as exc:
                    raise VerifiedPublicationError("publication_base_content_invalid") from exc
            writes.append((patch, path, sha, apply_patch(source, patch)))
        self._call("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_commit})
        for patch, path, sha, result in writes:
            if patch.new_path is None:
                self._call("DELETE", f"/repos/{repo}/contents/{quote(path, safe='/')}", {"message": f"luvira: {task_id}", "sha": sha, "branch": branch})
            else:
                target = patch.new_path
                body_value = {"message": f"luvira: {task_id}", "content": base64.b64encode(result).decode(), "branch": branch}
                if sha is not None:
                    body_value["sha"] = sha
                self._call("PUT", f"/repos/{repo}/contents/{quote(target, safe='/')}", body_value)
        pull = self._call("POST", f"/repos/{repo}/pulls", {"title": title, "head": branch, "base": "main", "body": body, "draft": True})
        url = pull.get("html_url")
        if not isinstance(url, str) or not url:
            raise VerifiedPublicationError("publication_pull_invalid")
        return url
