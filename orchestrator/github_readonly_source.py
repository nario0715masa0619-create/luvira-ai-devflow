"""GitHub App adapter that only reads an immutable commit tree and blobs."""

from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from source_snapshot import SourceSnapshotBuilder


API_ROOT = "https://api.github.com"


class GitHubSourceReadError(ValueError):
    pass


class GitHubReadOnlySource:
    def __init__(self, repository: str, installation_token: str, opener=urlopen):
        if not isinstance(repository, str) or repository.count("/") != 1 or not isinstance(installation_token, str) or not installation_token:
            raise GitHubSourceReadError("github_source_config_invalid")
        self.repository, self._token, self._opener = repository, installation_token, opener

    def _get(self, path: str) -> dict:
        request = Request(API_ROOT + path, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "luvira-devflow-source-reader/1",
        })
        try:
            with self._opener(request, timeout=20) as response:
                value = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise GitHubSourceReadError("github_source_unavailable") from exc
        if not isinstance(value, dict):
            raise GitHubSourceReadError("github_source_response_invalid")
        return value

    def snapshot(self, base_commit: str, allowed_paths: tuple[str, ...]):
        repo = quote(self.repository, safe="/")
        commit = quote(base_commit, safe="")
        tree = self._get(f"/repos/{repo}/git/trees/{commit}?recursive=1").get("tree")
        if not isinstance(tree, list):
            raise GitHubSourceReadError("github_source_tree_invalid")
        builder = SourceSnapshotBuilder(
            lambda _commit: tree,
            lambda sha: self._get(f"/repos/{repo}/git/blobs/{quote(str(sha), safe='')}")
        )
        try:
            # The Broker client accepts only raw bytes.  Keep the richer
            # SourceSnapshot object inside this read adapter so it cannot leak
            # across the provider boundary and turn a valid source read into
            # an unclassified implementation failure.
            return builder.build(base_commit, allowed_paths).content
        except ValueError as exc:
            raise GitHubSourceReadError(str(exc)) from exc
