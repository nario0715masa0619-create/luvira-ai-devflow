"""One-shot, credential-isolated DevFlow product repository provisioner."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from project_onboarding import NewProjectRequest, ProjectOnboardingError
from project_provisioner import GitHubProjectProvisioner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    try:
        provisioner = GitHubProjectProvisioner(os.environ.get("PROJECT_PROVISIONING_TOKEN", ""))
        request = NewProjectRequest(args.owner, args.slug, args.description)
        repository, initial_commit = provisioner.create_repository(request)
        bootstrap_commit = provisioner.write_bootstrap_manifest(repository, args.project_id, initial_commit)
    except ProjectOnboardingError as exc:
        print(f"PROVISION_FAILED:{exc}")
        return 1
    print(f"PROVISION_READY:{repository}:{bootstrap_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
