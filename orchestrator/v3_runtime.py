"""Private runtime composition for the fail-closed v3 queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from google.cloud import firestore
from google.cloud import run_v2

from cloud_run_preflight import cloud_run_checkers
from cloud_run_bootstrap_client import CloudRunBootstrapClient
from cloud_run_bootstrap_client import CloudLoggingBootstrapReader
from artifact_handoff import ArtifactHandoff, FirestoreVerifiedArtifactStore
from implementation_artifact_handoff import FirestoreImplementationArtifactStore, ImplementationArtifactHandoff
from durable_queue_service import DurableQueueService
from execution_preflight import ExecutionPreflight, PreflightCheck
from v3_task_store import FirestoreV3TaskStore
from v3_transaction import V3Transaction
from worker_dispatch_service import WorkerDispatchService
from worker_result_reconciler import WorkerResultReconciler
from github_readonly_source import GitHubReadOnlySource
from implementation_execution_service import ImplementationExecutionService
from opencode_implementation_client import OpenCodeImplementationClient
from github_verified_publisher import GitHubVerifiedPublisher
from verified_publication_service import VerifiedPublicationService
from publication_completion_service import PublicationCompletionService
from runtime_health_watchdog import FirestoreRuntimeHealthStore, RuntimeHealthWatchdog


@dataclass(frozen=True)
class V3QueueRuntime:
    tasks: FirestoreV3TaskStore
    queue: DurableQueueService
    dispatcher: WorkerDispatchService
    reconciler: WorkerResultReconciler
    implementation: ImplementationExecutionService
    publication: VerifiedPublicationService
    completion: PublicationCompletionService
    runtime_watchdog: RuntimeHealthWatchdog


def create_v3_queue_service(
    *,
    project: str,
    region: str,
    worker_job: str,
    broker_service_account: str,
    task_collection: str,
    artifact_boundary_available: Callable[[], bool],
    provider_available: Callable[[], bool],
    implementation_api_key: str,
    implementation_model: str,
    source_token_for_repository: Callable[[str], str],
    worker_identity_available: Callable[[], bool],
    runtime_incident_reporter: Callable[[dict[str, str]], None],
    artifact_bucket: str,
    artifact_view: str,
    artifact_collection: str,
    implementation_artifact_collection: str,
    runtime_health_collection: str,
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
        # google-cloud-run 0.16 exposes IAM calls through the request object
        # rather than flattened keyword fields.  Passing ``resource=`` caused
        # the read-only identity preflight to fail before it could inspect the
        # worker's existing least-privilege bindings.
        policy = jobs.get_iam_policy(request={"resource": resource})
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
    transaction = V3Transaction(firestore_client, task_collection)
    queue = DurableQueueService(
        tasks,
        preflight,
        transaction,
    )
    worker = CloudRunBootstrapClient(project, region, worker_job)
    dispatcher = WorkerDispatchService(tasks, transaction, worker)
    reconciler = WorkerResultReconciler(
        tasks, transaction, worker,
        CloudLoggingBootstrapReader(project, region, artifact_bucket, artifact_view),
        ArtifactHandoff(FirestoreVerifiedArtifactStore(firestore_client, artifact_collection)),
    )
    implementation_store = FirestoreImplementationArtifactStore(
        firestore_client, implementation_artifact_collection,
    )
    implementation = ImplementationExecutionService(
        tasks, transaction,
        lambda task: GitHubReadOnlySource(
            task.spec.repository, source_token_for_repository(task.spec.repository),
        ).snapshot(task.spec.base_commit, task.spec.source_paths, task.spec.allowed_paths),
        OpenCodeImplementationClient(implementation_api_key),
        implementation_model,
        ImplementationArtifactHandoff(implementation_store),
    )
    publication = VerifiedPublicationService(
        tasks, transaction, implementation_store,
        lambda task: GitHubVerifiedPublisher(task.spec.repository, source_token_for_repository(task.spec.repository)),
    )
    completion = PublicationCompletionService(
        tasks, transaction,
        lambda task: GitHubVerifiedPublisher(task.spec.repository, source_token_for_repository(task.spec.repository)),
    )
    watchdog = RuntimeHealthWatchdog(
        FirestoreRuntimeHealthStore(firestore_client, runtime_health_collection),
        _runtime_checks(tasks, provider_available, worker_identity_available), runtime_incident_reporter,
    )
    return V3QueueRuntime(tasks, queue, dispatcher, reconciler, implementation, publication, completion, watchdog)


def _control_plane_available(tasks: FirestoreV3TaskStore) -> bool:
    tasks.readiness_check()
    return True


def _runtime_checks(tasks: FirestoreV3TaskStore, provider_available: Callable[[], bool],
                    worker_identity_available: Callable[[], bool]) -> dict[str, Callable[[], bool]]:
    """Bind probes lazily: construction must not convert a boolean into a probe."""
    return {
        "control_plane": lambda: _control_plane_available(tasks),
        "opencode_go": provider_available,
        "github_worker": worker_identity_available,
    }
