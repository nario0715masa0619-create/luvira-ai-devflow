"""Fail closed when the human-approval workflow and its identity contract diverge."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "security" / "workload-identity" / "github-human-approval.json"
WORKFLOW = ROOT / ".github" / "workflows" / "authorize-approved-task.yml"


def require(text: str, expected: str, label: str) -> None:
    if expected not in text:
        raise SystemExit(f"human-approval identity contract mismatch: missing {label}")


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    workflow = WORKFLOW.read_text(encoding="utf-8")
    provider = (
        f"projects/{contract['project_number']}/locations/global/"
        f"workloadIdentityPools/{contract['pool_id']}/providers/{contract['provider_id']}"
    )
    require(workflow, f"name: {contract['workflow']}", "workflow name")
    require(workflow, f"environment: {contract['environment']}", "protected environment")
    require(workflow, f"workload_identity_provider: {provider}", "dedicated OIDC provider")
    require(workflow, f"service_account: {contract['service_account']}", "dedicated service account")
    if "github-deployer" in workflow or "devflow-deployer@" in workflow:
        raise SystemExit("human-approval workflow must not use the deployment identity")


if __name__ == "__main__":
    main()
