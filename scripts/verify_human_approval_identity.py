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
    require(workflow, "gcloud run services describe", "canonical Cloud Run endpoint discovery")
    require(workflow, "${{ steps.orchestrator.outputs.url }}", "discovered ID-token audience")
    require(workflow, "issue_number:", "server-owned approval Issue input")
    require(workflow, "/control-plane/v3/approval-issues/$ISSUE_NUMBER/pending", "server-side task resolution")
    if "inputs:\n      approval_binding:" in workflow or "inputs:\n      task_id:" in workflow:
        raise SystemExit("human-approval workflow must not accept reconstructed task facts")
    if "ORCHESTRATOR_URL: https://" in workflow:
        raise SystemExit("human-approval workflow must not hard-code a Cloud Run endpoint")
    if "github-deployer" in workflow or "devflow-deployer@" in workflow:
        raise SystemExit("human-approval workflow must not use the deployment identity")


if __name__ == "__main__":
    main()
