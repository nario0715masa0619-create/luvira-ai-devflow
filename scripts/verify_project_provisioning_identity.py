"""Fail closed when the project-provisioning workflow and identity contract diverge."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "security" / "workload-identity" / "github-project-provisioning.json"
WORKFLOW = ROOT / ".github" / "workflows" / "authorize-project-onboarding.yml"


def require(text: str, expected: str, label: str) -> None:
    if expected not in text:
        raise SystemExit(f"project provisioning identity contract mismatch: missing {label}")


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    workflow = WORKFLOW.read_text(encoding="utf-8")
    account_id = contract["service_account"].split("@", 1)[0]
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", account_id):
        raise SystemExit("provisioning service account ID must satisfy Google Cloud naming limits")
    if contract.get("roles") != ["roles/run.invoker", "roles/run.viewer"]:
        raise SystemExit("provisioning identity roles must be invoker and service viewer only")
    provider = f"projects/{contract['project_number']}/locations/global/workloadIdentityPools/{contract['pool_id']}/providers/{contract['provider_id']}"
    require(workflow, f"name: {contract['workflow']}", "workflow name")
    require(workflow, f"environment: {contract['environment']}", "protected environment")
    require(workflow, f"workload_identity_provider: {provider}", "dedicated OIDC provider")
    require(workflow, f"service_account: {contract['service_account']}", "dedicated service account")
    require(workflow, f"secrets.{contract['required_secret']}", "dedicated provisioning secret")
    require(workflow, "gcloud run services describe", "canonical Cloud Run endpoint discovery")
    require(workflow, "/claim-provisioning", "provisioning claim")
    require(workflow, "/checkpoint-provisioning", "durable provisioning checkpoint")
    require(workflow, "/complete-provisioning", "server-owned completion")
    if "github-deployer" in workflow or "devflow-deployer@" in workflow:
        raise SystemExit("project provisioning workflow must not use the deployment identity")
    if "GITHUB_WORKER" in workflow or "OPENCODE" in workflow:
        raise SystemExit("project provisioning workflow must not use ordinary execution credentials")


if __name__ == "__main__":
    main()
