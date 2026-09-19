"""Dedicated GitHub adapter for creating a DevFlow product repository.

This adapter is intentionally separate from the ordinary Worker publisher.  It
accepts a provisioning credential only at construction time and never returns
that credential or logs a response body containing sensitive information.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from project_onboarding import NewProjectRequest, ProjectOnboardingError


GITHUB_API_URL = "https://api.github.com"


class GitHubProjectProvisioner:
    def __init__(self, token: str, opener: Callable[..., Any] = urlopen):
        if not isinstance(token, str) or not token:
            raise ProjectOnboardingError("project_provisioning_credential_missing")
        self._token = token
        self._opener = opener

    def create_repository(self, request: NewProjectRequest) -> tuple[str, str]:
        """Create one private repository with its initial commit.

        `/user/repos` binds creation to the credential owner.  The returned
        full name must still match the owner approved in the request; a token
        for another account therefore fails closed.
        """
        try:
            payload = self._call("POST", "/user/repos", {
                "name": request.slug,
                "description": request.description.strip(),
                "private": True,
                "auto_init": True,
                "has_issues": True,
            })
        except HTTPError as exc:
            # A request can fail after GitHub has created the repository but
            # before the bootstrap manifest is written.  Resume only the one
            # repository named in the immutable approved request; never pick
            # a different destination or create a suffix repository.
            if exc.code not in {409, 422}:
                raise ProjectOnboardingError("project_repository_create_failed") from exc
            try:
                payload = self._call("GET", f"/repos/{quote(request.repository, safe='/')}")
            except HTTPError as recovery_error:
                raise ProjectOnboardingError("project_repository_recovery_missing") from recovery_error
        repository = payload.get("full_name")
        default_branch = payload.get("default_branch")
        if (repository != request.repository or payload.get("private") is not True
                or not isinstance(default_branch, str) or not default_branch):
            raise ProjectOnboardingError("project_creation_identity_mismatch")
        ref = self._call("GET", f"/repos/{quote(repository, safe='/')}/git/ref/heads/{quote(default_branch, safe='')}")
        commit = ((ref.get("object") or {}).get("sha"))
        if not isinstance(commit, str) or len(commit) != 40:
            raise ProjectOnboardingError("project_initial_commit_missing")
        return repository, commit

    def write_bootstrap_manifest(self, repository: str, project_id: str, base_commit: str) -> str:
        """Write a non-secret ownership marker before enabling implementation."""
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise ProjectOnboardingError("project_repository_invalid")
        if not isinstance(project_id, str) or not project_id.startswith("project-"):
            raise ProjectOnboardingError("project_id_invalid")
        if not isinstance(base_commit, str) or len(base_commit) != 40:
            raise ProjectOnboardingError("project_base_commit_invalid")
        contents = json.dumps({
            "schema_version": "luvira.devflow.project.v1",
            "project_id": project_id,
            "control_repository": "nario0715masa0619-create/luvira-ai-devflow",
            "base_commit": base_commit,
        }, ensure_ascii=False, indent=2) + "\n"
        encoded = base64.b64encode(contents.encode()).decode()
        path = ".github/luvira-project.json"
        try:
            payload = self._call("PUT", f"/repos/{quote(repository, safe='/')}/contents/{path}", {
                "message": "DevFlowプロジェクト初期設定を追加",
                "content": encoded,
            })
        except HTTPError as exc:
            # A previous provision attempt may have written the marker before
            # its completion callback failed.  Do not overwrite it blindly:
            # resume only when the exact immutable project identity matches.
            if exc.code != 409:
                raise ProjectOnboardingError("project_bootstrap_write_failed") from exc
            try:
                existing = self._call("GET", f"/repos/{quote(repository, safe='/')}/contents/{path}")
                raw = base64.b64decode(existing.get("content", ""), validate=True)
                manifest = json.loads(raw.decode())
            except (HTTPError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as recovery_error:
                raise ProjectOnboardingError("project_bootstrap_recovery_missing") from recovery_error
            if not isinstance(manifest, dict) or (
                manifest.get("schema_version") != "luvira.devflow.project.v1"
                or manifest.get("project_id") != project_id
                or manifest.get("control_repository") != "nario0715masa0619-create/luvira-ai-devflow"
                or not isinstance(manifest.get("base_commit"), str)
                or len(manifest["base_commit"]) != 40
            ):
                raise ProjectOnboardingError("project_bootstrap_identity_mismatch")
            history = self._call_list(
                f"/repos/{quote(repository, safe='/')}/commits?path={quote(path, safe='/')}&per_page=1"
            )
            payload = {"commit": history[0] if history else {}}
        commit = ((payload.get("commit") or {}).get("sha"))
        if not isinstance(commit, str) or len(commit) != 40:
            raise ProjectOnboardingError("project_bootstrap_commit_missing")
        return commit

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = Request(
            GITHUB_API_URL + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "luvira-devflow-project-provisioner/1",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        with self._opener(request, timeout=10) as response:  # nosec B310: fixed GitHub API endpoint
            payload = json.loads(response.read().decode())
        if not isinstance(payload, dict):
            raise ProjectOnboardingError("project_github_response_invalid")
        return payload

    def _call_list(self, path: str) -> list[dict[str, Any]]:
        request = Request(
            GITHUB_API_URL + path,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "luvira-devflow-project-provisioner/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with self._opener(request, timeout=10) as response:  # nosec B310: fixed GitHub API endpoint
            payload = json.loads(response.read().decode())
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ProjectOnboardingError("project_github_response_invalid")
        return payload
