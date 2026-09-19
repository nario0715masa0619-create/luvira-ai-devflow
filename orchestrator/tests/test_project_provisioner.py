import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.error import HTTPError

from project_onboarding import NewProjectRequest, ProjectOnboardingError
from project_provisioner import GitHubProjectProvisioner


class Response:
    def __init__(self, payload): self.payload = payload
    def read(self): return json.dumps(self.payload).encode()
    def __enter__(self): return self
    def __exit__(self, *_): return False


class ProjectProvisionerTest(unittest.TestCase):
    def setUp(self):
        self.request = NewProjectRequest("nario0715masa0619-create", "new-product", "新規プロダクト")

    def test_creates_only_the_approved_private_repository(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if request.full_url.endswith("/user/repos"):
                return Response({"full_name": self.request.repository, "default_branch": "main", "private": True})
            return Response({"object": {"sha": "a" * 40}})
        repository, commit = GitHubProjectProvisioner("token", opener).create_repository(self.request)
        self.assertEqual((repository, commit), (self.request.repository, "a" * 40))
        self.assertEqual(json.loads(calls[0].data.decode())["private"], True)

    def test_rejects_a_repository_created_under_the_wrong_owner(self):
        def opener(_request, timeout):
            return Response({"full_name": "other/new-product", "default_branch": "main", "private": True})
        with self.assertRaisesRegex(ProjectOnboardingError, "project_creation_identity_mismatch"):
            GitHubProjectProvisioner("token", opener).create_repository(self.request)

    def test_resumes_only_the_exact_repository_after_a_partial_creation(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            if request.full_url.endswith("/user/repos"):
                raise HTTPError(request.full_url, 422, "already exists", {}, None)
            if request.full_url.endswith(f"/repos/{self.request.repository}"):
                return Response({"full_name": self.request.repository, "default_branch": "main", "private": True})
            return Response({"object": {"sha": "c" * 40}})

        repository, commit = GitHubProjectProvisioner("token", opener).create_repository(self.request)

        self.assertEqual((repository, commit), (self.request.repository, "c" * 40))
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[1].full_url.endswith(f"/repos/{self.request.repository}"))

    def test_manifest_is_a_non_secret_project_marker(self):
        request = None
        def opener(value, timeout):
            nonlocal request
            request = value
            return Response({"commit": {"sha": "b" * 40}})
        commit = GitHubProjectProvisioner("token", opener).write_bootstrap_manifest(
            self.request.repository, "project-new-product", "a" * 40,
        )
        self.assertEqual(commit, "b" * 40)
        self.assertIn(".github/luvira-project.json", request.full_url)
        self.assertNotIn(b"token", request.data)

    def test_isolated_cli_loads_without_cloud_firestore(self):
        root = Path(__file__).parents[2]
        environment = os.environ | {"PYTHONPATH": ""}
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "provision_project_repository.py"),
             "--owner", self.request.owner, "--slug", self.request.slug,
             "--description", self.request.description, "--project-id", "project-new-product"],
            text=True, capture_output=True, env=environment, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("project_provisioning_credential_missing", result.stdout)
