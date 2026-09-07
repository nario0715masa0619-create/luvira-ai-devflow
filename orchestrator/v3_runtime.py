"""Private runtime composition for the fail-closed v3 queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from google.cloud import firestore
from google.cloud import run_v2

from cloud_run_preflight import cloud_run_checkers
from durable_queue_service import DurableQueueService
from execution_preflight import ExecutionPreflight, PreflightCheck
from v3_task_store import FirestoreV3TaskStore
from v3_transaction import V3Transaction


@dataclass(frozen=True)
class V3QueueRuntime:
    tasks: FirestoreV3TaskStore
    queue: DurableQueueService


def create_v3_queue_service(
    *,
    project: str,
    region: str,
    worker_job: str,
    broker_service_account: str,
    task_collection: str,
    artifact_boundary_available: Callable[[], bool],
    provider_available: Callable[[], bool],
) -> V3QueueRuntime:
    """Compose only read checks plus the durable queue; never a launcher."""
    firestore_client = firestore.Client()
    jobs = run_v2.JobsClient()

    def get_job(check_project: str, check_region: str, job_name: str):
        resource = f"projects/{check_project}/locations/{check_region}/jobs/{job_name}"
        job = jobs.get_job(name=resource)
        containers = tuple(job.template.template.containers)
        return {
            "name": job.name.rsplit("/", 1)[-1],
            "image": containers[0].image if containers else "",
        }

    def get_roles(check_project: str, check_region: str, job_name: str, service_account: str):
        resource = f"projects/{check_project}/locations/{check_region}/jobs/{job_name}"
        policy = jobs.get_iam_policy(resource=resource)
        member = f"serviceAccount:{service_account}"
        return [binding.role for binding in policy.bindings if member in binding.members]

    def task_spec_check(_spec):
        return PreflightCheck("TASK_SPEC", True, "OK")

    def artifact_check(_spec):
        try:
            available = artifact_boundary_available()
        except Exception:
            available = False
        return PreflightCheck("ARTIFACT_BOUNDARY", bool(available), "OK" if available else "ARTIFACT_BOUNDARY_UNAVAILABLE")

    def provider_check(_spec):
        # A validation-only task has an explicit `none` model policy.  It is
        # still subject to every infrastructure preflight check, but it must
        # not require an AI provider that it is forbidden to call.
        if _spec.model_policy == "none":
            return PreflightCheck("PROVIDER_AVAILABILITY", True, "NOT_REQUIRED")
        try:
            available = provider_available()
        except Exception:
            available = False
        return PreflightCheck("PROVIDER_AVAILABILITY", bool(available), "OK" if available else "PROVIDER_UNAVAILABLE")

    broker_check, worker_check = cloud_run_checkers(
        get_job, get_roles, project, region, worker_job, broker_service_account
    )
    preflight = ExecutionPreflight((
        task_spec_check,
        broker_check,
        worker_check,
        artifact_check,
        provider_check,
    ))
    tasks = FirestoreV3TaskStore(firestore_client, task_collection)
    return V3QueueRuntime(tasks, DurableQueueService(
        tasks,
        preflight,
        V3Transaction(firestore_client, task_collection),
    ))
