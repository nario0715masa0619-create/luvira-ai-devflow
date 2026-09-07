"""Fail-closed, read-only Cloud Run preflight adapters for v3.

The caller supplies cloud SDK adapters. Keeping those adapters outside this
module makes the policy unit-testable and ensures that a preflight cannot
launch a job, alter IAM, or expose provider responses in task audit data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from execution_preflight import Checker, PreflightCheck


REQUIRED_BROKER_ROLES = frozenset({
    "roles/run.jobsExecutorWithOverrides",
    "roles/run.viewer",
})


def worker_job_check(
    get_job: Callable[[str, str, str], dict[str, Any] | None],
    project: str,
    region: str,
    job_name: str,
) -> PreflightCheck:
    """Require the configured worker job to resolve to an image-backed job."""
    try:
        job = get_job(project, region, job_name)
    except Exception:
        return PreflightCheck("WORKER_JOB", False, "WORKER_JOB_UNAVAILABLE")

    valid = bool(
        job
        and job.get("name") == job_name
        and isinstance(job.get("image"), str)
        and job["image"].strip()
    )
    return PreflightCheck("WORKER_JOB", valid, "OK" if valid else "WORKER_JOB_INVALID")


def broker_identity_check(
    get_roles: Callable[[str, str, str, str], Iterable[str]],
    project: str,
    region: str,
    job_name: str,
    service_account: str,
) -> PreflightCheck:
    """Require the minimal read/start roles for the broker identity."""
    try:
        roles = frozenset(get_roles(project, region, job_name, service_account))
    except Exception:
        return PreflightCheck("BROKER_IDENTITY", False, "BROKER_IAM_UNAVAILABLE")

    valid = REQUIRED_BROKER_ROLES.issubset(roles)
    return PreflightCheck(
        "BROKER_IDENTITY", valid, "OK" if valid else "BROKER_IAM_INSUFFICIENT"
    )


def cloud_run_checkers(
    get_job: Callable[[str, str, str], dict[str, Any] | None],
    get_roles: Callable[[str, str, str, str], Iterable[str]],
    project: str,
    region: str,
    job_name: str,
    service_account: str,
) -> tuple[Checker, Checker]:
    """Build the ordered Cloud Run checkers used by ``ExecutionPreflight``.

    The returned functions deliberately ignore ``TaskSpec``: the worker and
    broker identity are deployment prerequisites, not task-controlled inputs.
    """
    return (
        lambda _spec: broker_identity_check(
            get_roles, project, region, job_name, service_account
        ),
        lambda _spec: worker_job_check(get_job, project, region, job_name),
    )
