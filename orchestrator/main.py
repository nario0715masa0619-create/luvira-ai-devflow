import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
from flask import Flask, jsonify, request
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import firestore
from google.oauth2 import id_token

from v3_runtime import create_v3_queue_service
from v3_control_plane import V3ControlPlane, V3ControlPlaneError
from execution_platform import V3Status, task_spec_hash
from autonomous_broker import AutonomousBroker
from approval_expiry_service import ApprovalExpiryService
from implementation_artifact_verifier import verify_implementation_artifact, ImplementationArtifactError
from opencode_implementation_client import OpenCodeImplementationClient, OpenCodeImplementationError
from project_onboarding import (
    FirestoreProjectRegistry,
    NewProjectRequest,
    ProjectOnboardingError,
    ProjectOnboardingService,
    ProjectStatus,
    project_payload,
)

app = Flask(__name__)
EXPECTED_REPOSITORY = os.environ.get("EXPECTED_REPOSITORY", "nario0715masa0619-create/luvira-ai-devflow")
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
RUNNER_ORDER = tuple(
    provider.strip()
    for provider in os.environ.get("RUNNER_ORDER", "opencode-go,codex,claude-code,copilot").split(",")
    if provider.strip()
)
ALLOWED_RUNNERS = frozenset({"opencode-go", "copilot", "codex", "claude-code"})
OPENCODE_GO_MODELS_URL = "https://opencode.ai/zen/go/v1/models"
GITHUB_API_URL = "https://api.github.com"
PUBLIC_WEBHOOK_INGRESS_ONLY = os.environ.get("PUBLIC_WEBHOOK_INGRESS_ONLY", "").lower() in {"1", "true", "yes"}
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "").rstrip("/")
V3_TASK_COLLECTION = os.environ.get("V3_TASK_COLLECTION", "").strip()
BROKER_SERVICE_ACCOUNT = os.environ.get("BROKER_SERVICE_ACCOUNT", "").strip()
WORKER_JOB = os.environ.get("ISOLATED_WORKER_JOB", "").strip()
WORKER_REGION = os.environ.get("ISOLATED_WORKER_REGION", "").strip()
WORKER_ARTIFACT_BUCKET = os.environ.get("ISOLATED_WORKER_ARTIFACT_BUCKET", "").strip()
WORKER_ARTIFACT_VIEW = os.environ.get("ISOLATED_WORKER_ARTIFACT_VIEW", "").strip()
VERIFIED_ARTIFACT_COLLECTION = os.environ.get("V3_VERIFIED_ARTIFACT_COLLECTION", "").strip()
IMPLEMENTATION_ARTIFACT_COLLECTION = os.environ.get("V3_IMPLEMENTATION_ARTIFACT_COLLECTION", "").strip()
RUNTIME_HEALTH_COLLECTION = os.environ.get("V3_RUNTIME_HEALTH_COLLECTION", "").strip()
PROJECT_REGISTRY_COLLECTION = os.environ.get("V3_PROJECT_REGISTRY_COLLECTION", "").strip()
OPENCODE_IMPLEMENTATION_MODEL = os.environ.get("OPENCODE_IMPLEMENTATION_MODEL", "kimi-k2.6").strip()
# The protected human-approval workflow is the sole external trigger for the
# Broker.  It already has Cloud Run Invoker and an immutable approval binding.
BOOTSTRAP_CALLER_EMAIL = os.environ.get("BOOTSTRAP_CALLER_EMAIL", "devflow-human-approval@luvira-ai-control-plane.iam.gserviceaccount.com")
MAX_SAFE_EXECUTION_ATTEMPTS = 3


# v3 queue wiring is supplied only by the private runtime composition.  An
# absent service fails closed; this module never falls back to the retired
# synchronous bootstrap route.
def create_v3_queue_from_environment():
    if not os.environ.get("K_SERVICE") or PUBLIC_WEBHOOK_INGRESS_ONLY:
        return None
    # All durable stores and external resources are explicit deployment
    # inputs.  Defaults here would allow a staging revision to write into a
    # production-named collection or bucket when one setting was omitted.
    if not all((
        V3_TASK_COLLECTION, BROKER_SERVICE_ACCOUNT, WORKER_JOB, WORKER_REGION,
        WORKER_ARTIFACT_BUCKET, WORKER_ARTIFACT_VIEW,
        VERIFIED_ARTIFACT_COLLECTION, IMPLEMENTATION_ARTIFACT_COLLECTION,
        RUNTIME_HEALTH_COLLECTION,
    )):
        return None
    return create_v3_queue_service(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "luvira-ai-control-plane"),
        region=WORKER_REGION,
        worker_job=WORKER_JOB,
        broker_service_account=BROKER_SERVICE_ACCOUNT,
        task_collection=V3_TASK_COLLECTION,
        artifact_boundary_available=lambda: bool(WORKER_ARTIFACT_BUCKET and WORKER_ARTIFACT_VIEW),
        provider_available=lambda: bool(os.environ.get("OPENCODE_GO_API_KEY")) and opencode_go_model_count(os.environ["OPENCODE_GO_API_KEY"]) > 0,
        implementation_api_key=os.environ.get("OPENCODE_GO_API_KEY", ""),
        implementation_model=OPENCODE_IMPLEMENTATION_MODEL,
        source_token_for_repository=lambda _repository: github_worker_installation_token(),
        # Resolve at watchdog execution time; this module composes its private
        # runtime before the helper functions below are defined.
        worker_identity_available=lambda: github_worker_alerting_available(),
        runtime_incident_reporter=lambda checks: create_runtime_incident(checks),
        artifact_bucket=WORKER_ARTIFACT_BUCKET,
        artifact_view=WORKER_ARTIFACT_VIEW,
        artifact_collection=VERIFIED_ARTIFACT_COLLECTION,
        implementation_artifact_collection=IMPLEMENTATION_ARTIFACT_COLLECTION,
        runtime_health_collection=RUNTIME_HEALTH_COLLECTION,
    )


V3_QUEUE_RUNTIME = create_v3_queue_from_environment()
V3_QUEUE_SERVICE = V3_QUEUE_RUNTIME.queue if V3_QUEUE_RUNTIME else None
V3_TASK_STORE = V3_QUEUE_RUNTIME.tasks if V3_QUEUE_RUNTIME else None
V3_CONTROL_PLANE = V3ControlPlane(V3_TASK_STORE, V3_QUEUE_SERVICE) if V3_TASK_STORE and V3_QUEUE_SERVICE else None
V3_AUTONOMOUS_BROKER = AutonomousBroker(
    V3_TASK_STORE, V3_QUEUE_SERVICE, V3_QUEUE_RUNTIME.dispatcher, V3_QUEUE_RUNTIME.reconciler,
    V3_QUEUE_RUNTIME.implementation, V3_QUEUE_RUNTIME.publication, V3_QUEUE_RUNTIME.completion,
    V3_QUEUE_RUNTIME.runtime_watchdog, ApprovalExpiryService(V3_TASK_STORE),
) if V3_QUEUE_RUNTIME else None


def create_project_onboarding_from_environment():
    """Provisioning is opt-in and cannot change the existing task queue."""
    if not os.environ.get("K_SERVICE") or not PROJECT_REGISTRY_COLLECTION:
        return None
    return ProjectOnboardingService(
        FirestoreProjectRegistry(firestore.Client(), PROJECT_REGISTRY_COLLECTION)
    )


PROJECT_ONBOARDING = create_project_onboarding_from_environment()


@app.before_request
def restrict_public_ingress():
    """A public ingress instance exposes exactly one signed webhook route."""
    if PUBLIC_WEBHOOK_INGRESS_ONLY and request.path != "/github/webhook":
        return jsonify(status="BLOCKED", reason="public_ingress_route_not_allowed"), 404


@app.post("/events")
def events():
    envelope = request.get_json(silent=True) or {}
    message = envelope.get("message", {})
    raw = message.get("data", "")
    try:
        event = json.loads(base64.b64decode(raw).decode())
    except Exception:
        logging.warning("BLOCKED invalid event envelope")
        return jsonify(status="BLOCKED", reason="invalid_event"), 400

    repository = event.get("repository")
    action = event.get("action")
    issue = event.get("issue")
    if repository != EXPECTED_REPOSITORY:
        logging.warning("BLOCKED repository=%s", repository)
        return jsonify(status="BLOCKED", reason="repository_mismatch"), 403
    if action not in {"opened", "labeled", "edited"} or not isinstance(issue, int):
        return jsonify(status="BLOCKED", reason="unsupported_event"), 400

    return pending_context_lock(repository, issue, action)


@app.post("/github/webhook")
def github_webhook():
    """Public ingress: authenticate GitHub first, then apply the same context lock."""
    if not WEBHOOK_SECRET:
        logging.error("BLOCKED webhook secret is not configured")
        return jsonify(status="BLOCKED", reason="webhook_not_configured"), 503

    raw = request.get_data(cache=True)
    supplied = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), raw, "sha256").hexdigest()
    if not hmac.compare_digest(supplied, expected):
        logging.warning("BLOCKED invalid GitHub webhook signature")
        return jsonify(status="BLOCKED", reason="invalid_signature"), 401

    event_name = request.headers.get("X-GitHub-Event")
    # GitHub sends a signed ping whenever a webhook is first configured.  It is
    # a transport handshake, not an orchestration request, so acknowledge it
    # without accepting any repository or issue data.
    if event_name == "ping":
        return jsonify(status="OK", event="ping"), 200

    if event_name != "issues":
        return jsonify(status="BLOCKED", reason="unsupported_event"), 400
    payload = request.get_json(silent=True) or {}
    repository = (payload.get("repository") or {}).get("full_name")
    action = payload.get("action")
    issue = (payload.get("issue") or {}).get("number")
    if repository != EXPECTED_REPOSITORY:
        logging.warning("BLOCKED repository=%s", repository)
        return jsonify(status="BLOCKED", reason="repository_mismatch"), 403
    if action not in {"opened", "labeled", "edited"} or not isinstance(issue, int):
        return jsonify(status="BLOCKED", reason="unsupported_event"), 400

    if PUBLIC_WEBHOOK_INGRESS_ONLY:
        if not ORCHESTRATOR_URL:
            logging.error("BLOCKED public webhook ingress has no internal destination")
            return jsonify(status="BLOCKED", reason="orchestrator_destination_not_configured"), 503
        try:
            status, response = forward_signed_webhook(raw, supplied, event_name)
        except (HTTPError, URLError, TimeoutError, ValueError):
            logging.warning("BLOCKED public webhook ingress could not reach internal orchestrator")
            return jsonify(status="BLOCKED", reason="orchestrator_unavailable"), 503
        return jsonify(response), status

    return register_approval_issue(payload, repository, issue, action)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/readiness/control-plane")
def control_plane_readiness():
    """Read-only proof of the only task aggregate store."""
    if V3_TASK_STORE is None:
        logging.error("V3_CONTROL_PLANE_BLOCKED durable store is not initialized")
        return jsonify(status="BLOCKED", reason="v3_control_plane_not_configured"), 503
    try:
        V3_TASK_STORE.readiness_check()
    except Exception:
        logging.warning("V3_CONTROL_PLANE_BLOCKED Firestore readiness check failed")
        return jsonify(status="BLOCKED", reason="v3_control_plane_unavailable"), 503
    return jsonify(status="READY", backend="firestore", collection=V3_TASK_COLLECTION, lifecycle="v3-only")


@app.get("/readiness/project-onboarding")
def project_onboarding_readiness():
    """Read-only proof that new product requests have durable isolated storage."""
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    try:
        service.readiness_check()
    except Exception:
        logging.warning("PROJECT_ONBOARDING_BLOCKED durable registry is unavailable")
        return jsonify(status="BLOCKED", reason="project_registry_unavailable"), 503
    return jsonify(status="READY", backend="firestore", collection=PROJECT_REGISTRY_COLLECTION), 200


@app.post("/control-plane/v3/tasks/<task_id>/authorize")
def authorize_v3_task(task_id):
    """Commit a human decision only; broker admission is asynchronous."""
    payload = request.get_json(silent=True) or {}
    approval_binding = payload.get("approval_binding")
    actor = payload.get("actor")
    if not re.fullmatch(r"github-issue-[1-9][0-9]*-[0-9a-f]{16}", task_id):
        return jsonify(status="BLOCKED", reason="task_id_invalid"), 400
    if not isinstance(approval_binding, str) or not re.fullmatch(r"[0-9a-f]{64}", approval_binding):
        return jsonify(status="BLOCKED", reason="approval_binding_required"), 400
    if not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor):
        return jsonify(status="BLOCKED", reason="approval_actor_invalid"), 400

    if V3_CONTROL_PLANE is None:
        return jsonify(status="BLOCKED", reason="v3_control_plane_not_configured"), 503
    try:
        task = V3_CONTROL_PLANE.authorize(task_id, approval_binding, actor)
    except V3ControlPlaneError as exc:
        code = str(exc)
        status = 404 if code == "task_not_found" else 409 if code in {"approval_binding_mismatch", "authorization_not_reusable", "execution_preflight_failed", "queue_state_conflict"} else 503
        return jsonify(status="BLOCKED", reason=code), status
    except Exception:
        logging.exception("V3_CONTROL_PLANE_BLOCKED queue request failed")
        return jsonify(status="BLOCKED", reason="v3_queue_unavailable"), 503
    return jsonify(status="AUTHORIZED", task_id=task.task_id), 200


@app.get("/control-plane/v3/approval-issues/<int:issue_number>/pending")
def pending_approval_issue(issue_number):
    """Resolve server-side approval facts; callers never reconstruct hashes."""
    if V3_CONTROL_PLANE is None:
        return jsonify(status="BLOCKED", reason="v3_control_plane_not_configured"), 503
    try:
        task = V3_CONTROL_PLANE.pending_for_issue(issue_number)
    except V3ControlPlaneError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 404
    return jsonify(status="AWAITING_HUMAN_APPROVAL", task_id=task.task_id,
                   approval_binding=task.approval_binding), 200


@app.get("/control-plane/v3/approval-issues/<int:issue_number>/status")
def approval_issue_status(issue_number):
    """Return the requester-safe terminal or in-progress state for one Issue.

    Cloud Run authentication remains the access boundary.  This endpoint is
    read-only and intentionally excludes the approval binding, source
    snapshot, model output, and all credentials.  It exists so Orca can keep
    a requester informed after the human decision rather than treating the
    approval workflow as the end of the task.
    """
    if V3_TASK_STORE is None:
        return jsonify(status="BLOCKED", reason="v3_control_plane_not_configured"), 503
    try:
        task = V3_TASK_STORE.task_for_issue(issue_number)
    except Exception as exc:
        if str(exc) == "v3_task_not_found":
            return jsonify(status="BLOCKED", reason="task_not_found"), 404
        logging.exception("V3_TASK_STATUS_BLOCKED task lookup failed")
        return jsonify(status="BLOCKED", reason="v3_task_status_unavailable"), 503

    execution = task.execution
    checkpoint, recovery_action = requester_recovery_checkpoint(task)
    return jsonify(
        status=task.status.value,
        task_id=task.task_id,
        execution_status=execution.status.value if execution else None,
        failure_code=execution.failure_code if execution else None,
        publication_url=execution.publication_url if execution else None,
        checkpoint=checkpoint,
        recovery_action=recovery_action,
        terminal=task.status in {
            V3Status.NO_CHANGE_DETECTED, V3Status.EXECUTION_FAILED_FINAL,
            V3Status.PUBLISHED, V3Status.MERGED, V3Status.REJECTED,
            V3Status.CANCELLED, V3Status.EXPIRED,
        },
    ), 200


def requester_recovery_checkpoint(task):
    """Describe the durable resume boundary without exposing task inputs.

    The Broker owns all transitions; this projection is for the requester and
    monitor only.  It makes clear whether a restart will reconcile existing
    work, safely resume from a checkpoint, or require a new approval.
    """
    status = task.status
    record = task.execution
    if status in {V3Status.EXECUTION_RUNNING, V3Status.WORKER_LAUNCH_ACCEPTED,
                  V3Status.WORKER_EXECUTION_IDENTIFIED}:
        return "WORKER_EXECUTION", "RECONCILE_EXISTING_WORKER"
    if status is V3Status.EXECUTION_FAILED_RETRYABLE:
        if record is not None and record.attempt >= MAX_SAFE_EXECUTION_ATTEMPTS:
            return "WORKER_EXECUTION", "TERMINALIZE_RECOVERY_BUDGET"
        return "WORKER_DISPATCH", "RESUME_SAFE_WORKER_RETRY"
    if status is V3Status.WORKER_HEALTH_VERIFIED:
        return "WORKER_HEALTH", "RESUME_IMPLEMENTATION"
    if status is V3Status.IMPLEMENTATION_GENERATING:
        return "IMPLEMENTATION_CLAIM", "RECONCILE_PROVIDER_CLAIM_NO_REPLAY"
    if status is V3Status.ARTIFACT_VERIFIED:
        return "VERIFIED_ARTIFACT", "RESUME_DRAFT_PR_PUBLICATION"
    if status is V3Status.PUBLISHED:
        return "DRAFT_PR", "AWAIT_HUMAN_MERGE"
    if status is V3Status.AWAITING_HUMAN_APPROVAL:
        return "APPROVAL", "AWAIT_HUMAN_DECISION"
    if status is V3Status.AUTHORIZED:
        return "AUTHORIZATION", "RESUME_QUEUE_ADMISSION"
    if status in {V3Status.EXECUTION_FAILED_FINAL, V3Status.EXPIRED,
                  V3Status.REJECTED, V3Status.CANCELLED}:
        return "TERMINAL", "NEW_APPROVAL_REQUIRED"
    if status in {V3Status.NO_CHANGE_DETECTED, V3Status.VALIDATION_SUCCEEDED,
                  V3Status.MERGED}:
        return "TERMINAL", "NONE"
    return "UNKNOWN", "RECONCILE_STATE"


def project_approval_binding(record):
    """Bind a human decision to exactly one immutable onboarding request."""
    canonical = json.dumps(project_payload(record), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def project_service_or_blocked():
    if PROJECT_ONBOARDING is None:
        return None, (jsonify(status="BLOCKED", reason="project_onboarding_not_configured"), 503)
    return PROJECT_ONBOARDING, None


@app.post("/control-plane/v3/projects")
def request_project_onboarding():
    """Register an Orca product request; this endpoint never creates a repo."""
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    try:
        record = service.request(NewProjectRequest(
            owner=payload.get("owner", ""), slug=payload.get("slug", ""),
            description=payload.get("description", ""),
        ))
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 400
    return jsonify(status="AWAITING_HUMAN_APPROVAL", project_id=record.project_id,
                   approval_binding=project_approval_binding(record)), 201


@app.get("/control-plane/v3/projects/<project_id>/pending")
def pending_project_onboarding(project_id):
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    try:
        record = service.get(project_id)
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 404
    # A terminal provisioning failure is deliberately re-presented for a
    # fresh human decision.  The immutable approval binding remains derived
    # from the original request, while the transition itself is never
    # automatic.
    if record.status not in {ProjectStatus.REQUESTED, ProjectStatus.PROVISION_FAILED}:
        return jsonify(status="BLOCKED", reason="project_approval_not_pending"), 409
    return jsonify(status="AWAITING_HUMAN_APPROVAL", project_id=record.project_id,
                   approval_binding=project_approval_binding(record)), 200


@app.post("/control-plane/v3/projects/<project_id>/authorize")
def authorize_project_onboarding(project_id):
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    actor, binding = payload.get("actor"), payload.get("approval_binding")
    if not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor):
        return jsonify(status="BLOCKED", reason="approval_actor_invalid"), 400
    try:
        record = service.get(project_id)
        if not isinstance(binding, str) or not hmac.compare_digest(binding, project_approval_binding(record)):
            raise ProjectOnboardingError("project_approval_binding_mismatch")
        approved = service.approve(project_id)
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 409
    return jsonify(status="AUTHORIZED", project_id=approved.project_id), 200


@app.post("/control-plane/v3/projects/<project_id>/claim-provisioning")
def claim_project_provisioning(project_id):
    """A dedicated provisioning identity may claim one authorized project."""
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    try:
        record = service.begin_provisioning(project_id)
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 409
    return jsonify(status="PROVISIONING", project_id=record.project_id,
                   owner=record.request.owner, slug=record.request.slug,
                   description=record.request.description), 200


@app.post("/control-plane/v3/projects/<project_id>/complete-provisioning")
def complete_project_provisioning(project_id):
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    try:
        repository = payload.get("repository", "")
        bootstrap_commit = payload.get("bootstrap_commit", "")
        github_project_repository_ready(repository, bootstrap_commit)
        record = service.complete_provisioning(
            project_id, repository, bootstrap_commit, True,
        )
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 409
    return jsonify(status="READY", project_id=record.project_id, repository=record.repository), 200


@app.get("/readiness/project-provisioning")
def project_provisioning_readiness():
    """Prove that the Worker App can cover future private project repositories.

    A selected-repository installation can serve the control repository but
    cannot automatically receive a repository that the provisioner creates.
    Treat that as a preflight failure, before any GitHub-side project resource
    is created.
    """
    try:
        github_project_provisioning_ready()
    except ProjectOnboardingError as exc:
        logging.warning("PROJECT_PROVISIONING_BLOCKED reason=%s", exc)
        return jsonify(status="BLOCKED", reason=str(exc)), 503
    return jsonify(status="READY", provider="github-project-provisioning"), 200


@app.post("/control-plane/v3/projects/<project_id>/checkpoint-provisioning")
def checkpoint_project_provisioning(project_id):
    """Durably record a verified GitHub bootstrap result before readiness."""
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    try:
        record = service.checkpoint_provisioning(
            project_id, payload.get("repository", ""), payload.get("bootstrap_commit", ""),
        )
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 409
    return jsonify(status="PROVISIONING", project_id=record.project_id,
                   repository=record.repository, bootstrap_commit=record.bootstrap_commit), 200


@app.post("/control-plane/v3/projects/<project_id>/fail-provisioning")
def fail_project_provisioning(project_id):
    """Record a terminal provisioning result without choosing another target."""
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    try:
        record = service.fail_provisioning(project_id, payload.get("failure_code", ""))
    except ProjectOnboardingError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 409
    return jsonify(status="PROVISION_FAILED", project_id=record.project_id,
                   failure_code=record.failure_code), 200


@app.post("/internal/broker/sweep")
def sweep_v3_broker():
    """Admit eligible durable tasks; called by authenticated Cloud Scheduler."""
    if V3_AUTONOMOUS_BROKER is None:
        return jsonify(status="BLOCKED", reason="v3_broker_not_configured"), 503
    try:
        outcomes = V3_AUTONOMOUS_BROKER.sweep()
    except Exception:
        logging.exception("V3_BROKER_SWEEP_FAILED")
        return jsonify(status="RETRY_PENDING"), 503
    for task_id, outcome, execution_id in outcomes:
        # Outcomes are fixed control-plane codes and opaque ids only.  Never
        # log a TaskSpec, prompt, provider response, artifact, or credential.
        logging.warning(
            "V3_BROKER_OUTCOME task=%s outcome=%s execution=%s",
            task_id,
            outcome,
            execution_id,
        )
    return jsonify(
        status="OK",
        outcomes=[{"task_id": task_id, "status": status, "execution_id": execution_id}
                  for task_id, status, execution_id in outcomes],
    ), 200


@app.post("/control-plane/tasks/<task_id>/bootstrap")
def run_bootstrap(task_id):
    """Retired v2 route; v3 must use a durable queue consumer instead.

    This endpoint deliberately returns before checking identity, loading task
    state, reading logs, or constructing a Cloud Run client.  Keeping the
    tombstone prevents an old workflow retry from silently reviving the unsafe
    synchronous execution path.
    """
    return jsonify(status="BLOCKED", reason="legacy_execution_route_retired"), 410

@app.get("/readiness/opencode-go")
def opencode_go_readiness():
    """Prove the runtime can reach OpenCode Go without exposing the key or sending prompts."""
    api_key = os.environ.get("OPENCODE_GO_API_KEY", "")
    if not api_key:
        logging.error("OPENCODE_GO_BLOCKED credential is not configured")
        return jsonify(status="BLOCKED", reason="opencode_go_not_configured"), 503

    try:
        model_count = opencode_go_model_count(api_key)
    except (HTTPError, URLError, TimeoutError, ValueError):
        logging.warning("OPENCODE_GO_BLOCKED readiness check failed")
        return jsonify(status="BLOCKED", reason="opencode_go_unavailable"), 503

    logging.info("OPENCODE_GO_READY model_count=%s", model_count)
    return jsonify(status="READY", provider="opencode-go", model_count=model_count)


@app.post("/internal/staging/opencode-generation-canary")
def staging_opencode_generation_canary():
    """Run one opt-in, write-free provider canary in a staging revision only."""
    if os.environ.get("STAGING_OPENCODE_CANARY_ENABLED", "").lower() != "true":
        return jsonify(status="BLOCKED", reason="staging_generation_canary_disabled"), 404
    api_key = os.environ.get("OPENCODE_GO_API_KEY", "")
    if not api_key:
        return jsonify(status="BLOCKED", reason="opencode_go_not_configured"), 503
    envelope = {
        "task_id": "staging-opencode-generation-canary",
        "spec_hash": hashlib.sha256(b"staging-opencode-generation-canary-v1").hexdigest(),
        "base_commit": "0" * 40,
        "allowed_paths": ["canary/"],
        "acceptance_criteria": [
            "Create exactly one UTF-8 Markdown file under canary/.",
            "The file must state that it is a staging-only OpenCode generation canary.",
        ],
    }
    try:
        payload = OpenCodeImplementationClient(api_key).generate_artifact(
            model=OPENCODE_IMPLEMENTATION_MODEL,
            envelope=envelope,
            source_snapshot=b"This is an empty, staging-only canary source snapshot.\n",
        )
        verified = verify_implementation_artifact(
            payload, envelope, baseline_paths=(), baseline_files=(),
        )
    except OpenCodeImplementationError as exc:
        return jsonify(status="BLOCKED", reason=exc.code), 503
    except ImplementationArtifactError as exc:
        return jsonify(status="BLOCKED", reason=str(exc)), 503
    # Do not retain provider output, source data, or a publishable artifact.
    # Only the verified path count proves the complete provider/verifier path.
    return jsonify(status="READY", artifact="VERIFIED", changed_path_count=len(verified.changed_paths))


@app.get("/readiness/github-worker")
def github_worker_readiness():
    """Verify the Worker App identity and alert-token read access without writes."""
    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        logging.error("GITHUB_WORKER_BLOCKED credential or installation is not configured")
        return jsonify(status="BLOCKED", reason="github_worker_not_configured"), 503

    try:
        installation = github_worker_installation(app_id, installation_id, private_key)
    except (HTTPError, URLError, TimeoutError, ValueError, jwt.PyJWTError):
        logging.warning("GITHUB_WORKER_BLOCKED identity verification failed")
        return jsonify(status="BLOCKED", reason="github_worker_unavailable"), 503

    account = (installation.get("account") or {}).get("login")
    if installation.get("id") != int(installation_id) or not isinstance(account, str):
        logging.warning("GITHUB_WORKER_BLOCKED installation identity mismatch")
        return jsonify(status="BLOCKED", reason="github_worker_identity_mismatch"), 503
    if (installation.get("permissions") or {}).get("issues") != "write":
        logging.warning("GITHUB_WORKER_BLOCKED incident permission is not configured")
        return jsonify(status="BLOCKED", reason="github_worker_issues_write_required"), 503
    try:
        repository = github_api_request(
            f"{GITHUB_API_URL}/repos/{EXPECTED_REPOSITORY}", token=github_worker_installation_token(),
        )
    except (HTTPError, URLError, TimeoutError, ValueError, jwt.PyJWTError):
        logging.warning("GITHUB_WORKER_BLOCKED alert token repository probe failed")
        return jsonify(status="BLOCKED", reason="github_worker_alert_token_unavailable"), 503
    if repository.get("full_name") != EXPECTED_REPOSITORY:
        logging.warning("GITHUB_WORKER_BLOCKED alert token repository mismatch")
        return jsonify(status="BLOCKED", reason="github_worker_alert_repository_mismatch"), 503

    logging.info("GITHUB_WORKER_READY installation_id=%s account=%s", installation_id, account)
    return jsonify(status="READY", provider="github-worker", installation_id=int(installation_id), account=account,
                   incident_alerting="issues-write-token-ready")


@app.post("/worker/eligibility")
def worker_eligibility():
    """Read GitHub's immutable CI records before any future Worker write is considered."""
    proposal = request.get_json(silent=True) or {}
    issue = proposal.get("issue")
    source_branch = proposal.get("source_branch")
    project_id = proposal.get("project_id")
    repository = proposal.get("repository")
    if not isinstance(project_id, str) or not isinstance(issue, int) or issue < 1:
        return jsonify(status="BLOCKED", reason="repository_or_issue_mismatch"), 400
    if PROJECT_ONBOARDING is None:
        return jsonify(status="BLOCKED", reason="project_onboarding_not_configured"), 503
    try:
        if repository != PROJECT_ONBOARDING.require_ready_repository(project_id):
            raise ProjectOnboardingError("project_repository_mismatch")
    except ProjectOnboardingError:
        return jsonify(status="BLOCKED", reason="project_repository_not_ready"), 403
    if not isinstance(source_branch, str) or not source_branch.startswith(f"worker/issue-{issue}-"):
        return jsonify(status="BLOCKED", reason="source_branch_not_allowed"), 400

    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        logging.error("GITHUB_WORKER_BLOCKED eligibility credential is not configured")
        return jsonify(status="BLOCKED", reason="github_worker_not_configured"), 503
    try:
        evidence = github_worker_quality_evidence(repository, app_id, installation_id, private_key, source_branch)
    except (HTTPError, URLError, TimeoutError, ValueError, jwt.PyJWTError, KeyError):
        logging.warning("GITHUB_WORKER_BLOCKED eligibility lookup failed")
        return jsonify(status="BLOCKED", reason="github_worker_unavailable"), 503

    missing = [name for name, conclusion in evidence["workflows"].items() if conclusion != "success"]
    if missing:
        logging.info("GITHUB_WORKER_PENDING_QUALITY issue=%s missing=%s", issue, ",".join(missing))
        return jsonify(status="PENDING_QUALITY_GATES", issue=issue, head_sha=evidence["head_sha"], missing=missing), 409
    logging.info("GITHUB_WORKER_ELIGIBLE issue=%s sha=%s", issue, evidence["head_sha"])
    return jsonify(status="ELIGIBLE_FOR_DRAFT_PR", issue=issue, head_sha=evidence["head_sha"])


def pending_context_lock(repository, issue, action):
    """Return a fail-closed routing plan; this endpoint never runs an AI itself."""
    valid_order = [runner for runner in RUNNER_ORDER if runner in ALLOWED_RUNNERS]
    if not valid_order:
        logging.error("BLOCKED no valid execution runner is configured")
        return jsonify(status="BLOCKED", reason="no_valid_runner"), 503

    primary, *fallbacks = valid_order
    logging.info(
        "CONTEXT_LOCK_PENDING repository=%s issue=%s action=%s primary=%s fallbacks=%s",
        repository, issue, action, primary, ",".join(fallbacks) or "none",
    )
    return jsonify(
        status="PENDING_CONTEXT_LOCK",
        repository=repository,
        issue=issue,
        execution={"primary": primary, "fallbacks": fallbacks},
        quality_gates=[
            "context_lock",
            "deterministic_tests",
            "codex_independent_review",
            "claude_code_independent_review",
            "reviewer_runtime_monitor",
            "merge_protection",
        ],
        monitoring_layers={
            "execution": "runner availability, quotas, timeout, fallback count",
            "quality": "tests, static analysis, dependency and secret scanning",
            "independence": "reviewer identity and runtime separation",
            "governance": "context lock, policy, credentials and audit integrity",
        },
    )


def register_approval_issue(payload, repository, issue, action):
    """Persist a signed Issue Form as an immutable approval candidate.

    This is intentionally the final operation of webhook intake.  It does not
    select a model, enqueue a worker, create a branch, or call an AI provider.
    """
    labels = {
        item.get("name")
        for item in (payload.get("issue") or {}).get("labels", [])
        if isinstance(item, dict)
    }
    # Product provisioning is deliberately not an implementation task.  It
    # must be accepted before the normal Issue Form parser tries to resolve a
    # repository that does not exist yet.
    if "project-onboarding" in labels:
        if "ai-approval" in labels:
            logging.warning("PROJECT_ONBOARDING_BLOCKED issue=%s mixed labels", issue)
            return jsonify(status="BLOCKED", reason="project_onboarding_label_conflict"), 400
        return register_project_onboarding_issue(payload, issue, action)

    if V3_CONTROL_PLANE is None:
        logging.error("V3_CONTROL_PLANE_BLOCKED durable store is not initialized")
        return jsonify(status="BLOCKED", reason="v3_control_plane_not_configured"), 503
    if PROJECT_ONBOARDING is None:
        logging.error("V3_CONTROL_PLANE_BLOCKED project registry is not initialized")
        return jsonify(status="BLOCKED", reason="project_onboarding_not_configured"), 503
    try:
        spec = approval_issue_spec(payload, repository, issue, PROJECT_ONBOARDING)
        task_id = f"github-issue-{issue}-{task_spec_hash(spec)[:16]}"
        task = V3_CONTROL_PLANE.register(task_id, spec)
    except (V3ControlPlaneError, ValueError) as exc:
        logging.warning(
            "V3_CONTROL_PLANE_BLOCKED invalid approval issue=%s action=%s reason=%s",
            issue,
            action,
            type(exc).__name__,
        )
        return jsonify(status="BLOCKED", reason="invalid_approval_issue"), 400

    if task.status is not V3Status.AWAITING_HUMAN_APPROVAL:
        logging.warning("V3_CONTROL_PLANE_BLOCKED task=%s status=%s", task.task_id, task.status.value)
        return jsonify(status="BLOCKED", reason="approval_task_not_pending"), 409
    return jsonify(
        status="AWAITING_HUMAN_APPROVAL",
        task_id=task.task_id,
        spec_hash=task.spec.hash,
        approval_binding=task.approval_binding,
        issue=issue,
    )


def register_project_onboarding_issue(payload, issue_number, action):
    """Register a signed Orca-originated product request without creating a repo.

    The control-repository Issue is audit evidence only.  The dedicated
    project-provisioning workflow remains the sole creator of GitHub
    repositories after its separate human approval.
    """
    service, blocked = project_service_or_blocked()
    if blocked:
        return blocked
    issue = payload.get("issue") or {}
    body = issue.get("body")
    if not isinstance(body, str):
        return jsonify(status="BLOCKED", reason="project_onboarding_form_body_required"), 400
    try:
        request_value = NewProjectRequest(
            owner=issue_form_value(body, "Project Owner"),
            slug=issue_form_value(body, "Project Slug"),
            description=issue_form_value(body, "プロダクト概要"),
        )
        try:
            record = service.request(request_value)
        except ProjectOnboardingError as exc:
            # GitHub can deliver both `opened` and `labeled` for one freshly
            # created Issue.  Treat the same immutable request as idempotent;
            # never create a second product record or silently alter it.
            if str(exc) != "project_already_requested":
                raise
            record = service.get(f"project-{request_value.slug}")
            if record.request != request_value:
                raise ProjectOnboardingError("project_request_conflict") from exc
    except ProjectOnboardingError as exc:
        logging.warning(
            "PROJECT_ONBOARDING_BLOCKED issue=%s action=%s reason=%s",
            issue_number,
            action,
            str(exc),
        )
        return jsonify(status="BLOCKED", reason="invalid_project_onboarding_issue"), 400

    return jsonify(
        status="AWAITING_HUMAN_APPROVAL",
        project_id=record.project_id,
        approval_binding=project_approval_binding(record),
        issue=issue_number,
    )


def approval_issue_spec(payload, source_repository, issue_number, projects):
    """Bind a control-repo Issue to one READY product repository.

    The source Issue remains in the control repository so the human approval
    history has one canonical home. Its ``Repository`` field is instead the
    implementation target, accepted only when the durable product registry
    proves that the matching project is READY.
    """
    issue = payload.get("issue") or {}
    labels = {item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)}
    if "ai-approval" not in labels:
        raise ValueError("approval_label_required")
    body = issue.get("body")
    if not isinstance(body, str):
        raise ValueError("approval_form_body_required")
    form = {label: issue_form_value(body, label) for label in (
        "Project ID", "Repository", "承認すること", "タスク種別", "受入条件",
        "最大コスト（USD）", "影響", "しないこと", "許可を求める最初のアクション", "有効期限（UTC）",
        "許可するリポジトリ内パス", "参照を許可するリポジトリ内パス",
    )}
    project_id = form["Project ID"]
    try:
        target_repository = projects.require_ready_repository(project_id)
    except ProjectOnboardingError as exc:
        raise ValueError("project_repository_not_ready") from exc
    if form["Repository"] != target_repository:
        raise ValueError("project_repository_mismatch")
    criteria = [line.strip("- ") for line in form["受入条件"].splitlines() if line.strip()]
    try:
        max_cost_usd = float(form["最大コスト（USD）"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_max_cost_usd") from exc
    if not criteria or max_cost_usd <= 0:
        raise ValueError("invalid_approval_form")
    try:
        allowed_paths = json.loads(form["許可するリポジトリ内パス"])
        source_paths = json.loads(form["参照を許可するリポジトリ内パス"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_execution_scope") from exc
    return {
        "repository": target_repository,
        "base_commit": github_default_branch_sha(target_repository),
        "acceptance_criteria": criteria,
        "budget": {"max_cost_usd": max_cost_usd},
        "requested_action": form["許可を求める最初のアクション"],
        "execution_scope": {"allowed_paths": allowed_paths, "source_paths": source_paths},
        "expiry": form["有効期限（UTC）"],
        "model_policy": "none" if form["タスク種別"] == "validation" else "low-cost-first:" + ",".join(RUNNER_ORDER),
        "approval_context": {
            "project_id": project_id, "task_type": form["タスク種別"],
            "approval": form["承認すること"], "impact": form["影響"],
            "excluded": form["しないこと"],
            "source": {
                "repository": source_repository,
                "issue_number": issue_number,
                "issue_node_id": issue.get("node_id"),
            },
        },
    }


def issue_form_value(body, label):
    pattern = rf"^### {re.escape(label)}\s*$\n+([\s\S]*?)(?=^### |\Z)"
    match = re.search(pattern, body, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def github_default_branch_sha(repository):
    """Read the exact main SHA with the Worker App; it never writes to GitHub."""
    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        raise ValueError("github_worker_not_configured")
    installation_token = github_worker_installation_token()
    ref = github_api_request(f"{GITHUB_API_URL}/repos/{repository}/git/ref/heads/main", token=installation_token)
    sha = (ref.get("object") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise ValueError("invalid_base_commit")
    return sha


def github_worker_installation_token():
    """Mint a short-lived token for the configured App only when a read is due."""
    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        raise ValueError("github_worker_not_configured")
    now_epoch = int(time.time())
    app_jwt = jwt.encode({"iat": now_epoch - 60, "exp": now_epoch + 540, "iss": app_id}, private_key, algorithm="RS256")
    token = github_api_request(
        f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens", method="POST", token=app_jwt
    ).get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("invalid_installation_token")
    return token


def github_project_provisioning_ready():
    """Require an App installation that can receive newly-created repositories."""
    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_NOT_CONFIGURED")
    try:
        configured_installation_id = int(installation_id)
    except ValueError as exc:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_IDENTITY_MISMATCH") from exc
    try:
        installation = github_worker_installation(app_id, installation_id, private_key)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ProjectOnboardingError("PROJECT_GITHUB_APP_AUTHENTICATION_FAILED") from exc
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_INSTALLATION_UNAVAILABLE") from exc
    except (ValueError, URLError, OSError, jwt.PyJWTError) as exc:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_INSTALLATION_UNAVAILABLE") from exc
    if installation.get("id") != configured_installation_id or str(installation.get("app_id", "")) != app_id:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_IDENTITY_MISMATCH")
    if installation.get("repository_selection") != "all":
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_ALL_REPOSITORIES_REQUIRED")


def github_project_repository_ready(repository, bootstrap_commit):
    """Verify the App can read the exact private repository and bootstrap commit."""
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise ProjectOnboardingError("PROJECT_REPOSITORY_IDENTITY_INVALID")
    if not isinstance(bootstrap_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", bootstrap_commit):
        raise ProjectOnboardingError("PROJECT_BOOTSTRAP_COMMIT_INVALID")
    github_project_provisioning_ready()
    app_id = os.environ["GITHUB_WORKER_APP_ID"]
    private_key = os.environ["GITHUB_WORKER_PRIVATE_KEY"]
    try:
        now = int(time.time())
        app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256")
        installation_request = Request(
            f"{GITHUB_API_URL}/repos/{repository}/installation",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "luvira-devflow-project-readiness/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(installation_request, timeout=10) as response:  # nosec B310: fixed GitHub HTTPS endpoint
            installation = json.loads(response.read().decode())
    except HTTPError as exc:
        if exc.code == 404:
            raise ProjectOnboardingError("PROJECT_GITHUB_APP_NOT_INSTALLED_FOR_REPOSITORY") from exc
        if exc.code in {401, 403}:
            raise ProjectOnboardingError("PROJECT_GITHUB_APP_AUTHENTICATION_FAILED") from exc
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_REPOSITORY_PROBE_UNAVAILABLE") from exc
    except (ValueError, URLError, OSError, jwt.PyJWTError) as exc:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_REPOSITORY_PROBE_UNAVAILABLE") from exc
    if not isinstance(installation, dict) or str(installation.get("app_id", "")) != app_id:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_IDENTITY_MISMATCH")
    try:
        commit = github_api_request(
            f"{GITHUB_API_URL}/repos/{repository}/git/commits/{bootstrap_commit}",
            token=github_worker_installation_token(),
        )
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise ProjectOnboardingError("PROJECT_GITHUB_APP_COMMIT_READ_DENIED") from exc
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_COMMIT_PROBE_UNAVAILABLE") from exc
    except (ValueError, URLError, OSError, jwt.PyJWTError) as exc:
        raise ProjectOnboardingError("PROJECT_GITHUB_APP_COMMIT_PROBE_UNAVAILABLE") from exc
    if commit.get("sha") != bootstrap_commit:
        raise ProjectOnboardingError("PROJECT_BOOTSTRAP_COMMIT_NOT_FOUND")


def opencode_go_model_count(api_key):
    """Return only a count, keeping provider data and credentials out of responses/logs."""
    request = Request(
        OPENCODE_GO_MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "luvira-devflow-readiness/1"},
    )
    with urlopen(request, timeout=10) as response:  # nosec B310: fixed HTTPS endpoint
        payload = json.loads(response.read().decode())
    models = payload.get("data")
    if not isinstance(models, list):
        raise ValueError("invalid OpenCode Go model response")
    return len(models)


def github_worker_installation(app_id, installation_id, private_key):
    """Read the configured installation using an App JWT; never mint an installation token."""
    now = int(time.time())
    app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256")
    request = Request(
        f"{GITHUB_API_URL}/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "luvira-devflow-worker-readiness/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=10) as response:  # nosec B310: fixed GitHub HTTPS endpoint
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("invalid GitHub installation response")
    return payload


def github_worker_identity_available():
    """Return a boolean only after proving the configured installation owner."""
    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        return False
    installation = github_worker_installation(app_id, installation_id, private_key)
    account = (installation.get("account") or {}).get("login")
    expected_account = EXPECTED_REPOSITORY.split("/", 1)[0]
    return (installation.get("id") == int(installation_id)
            and account == expected_account
            and (installation.get("permissions") or {}).get("issues") == "write")


def github_worker_alerting_available():
    """Prove the alerting token can read its intended repository, never write it."""
    if not github_worker_identity_available():
        return False
    repository = github_api_request(
        f"{GITHUB_API_URL}/repos/{EXPECTED_REPOSITORY}", token=github_worker_installation_token(),
    )
    return repository.get("full_name") == EXPECTED_REPOSITORY


def create_runtime_incident(checks):
    """Open one bounded operational alert after a durable health transition."""
    if not isinstance(checks, dict) or not checks or not all(
        isinstance(name, str) and isinstance(status, str) and status in {"READY", "BLOCKED", "UNAVAILABLE"}
        for name, status in checks.items()
    ):
        raise ValueError("runtime_incident_checks_invalid")
    failed = sorted(name for name, status in checks.items() if status != "READY")
    if not failed:
        raise ValueError("runtime_incident_not_required")
    github_api_request(
        f"{GITHUB_API_URL}/repos/{EXPECTED_REPOSITORY}/issues", method="POST",
        token=github_worker_installation_token(), body={
            "title": "DevFlow runtime health blocked",
            "body": "Automated runtime monitoring could not verify: " + ", ".join(failed) + ".\n\n"
                    "The broker continues recovery safely; investigate the current runtime health record before approving new work.",
        },
    )


def github_worker_quality_evidence(repository, app_id, installation_id, private_key, source_branch):
    """Read only GitHub branch and workflow records; never create a branch, PR, or commit."""
    now = int(time.time())
    app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256")
    installation_token = github_api_request(
        f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens",
        method="POST",
        token=app_jwt,
    ).get("token")
    if not isinstance(installation_token, str):
        raise ValueError("invalid GitHub installation token response")
    ref = github_api_request(
        f"{GITHUB_API_URL}/repos/{repository}/git/ref/heads/{quote(source_branch, safe='')}",
        token=installation_token,
    )
    head_sha = ((ref.get("object") or {}).get("sha"))
    if not isinstance(head_sha, str):
        raise ValueError("invalid GitHub ref response")
    runs = github_api_request(
        f"{GITHUB_API_URL}/repos/{repository}/actions/runs?head_sha={quote(head_sha, safe='')}&per_page=100",
        token=installation_token,
    ).get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("invalid GitHub workflow response")
    required = {"Context Lock tests", "Orchestrator tests"}
    outcomes = {name: "missing" for name in required}
    for run in runs:
        if run.get("name") in required and run.get("conclusion") == "success":
            outcomes[run["name"]] = "success"
    return {"head_sha": head_sha, "workflows": outcomes}


def github_api_request(url, method="GET", token=None, body=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "luvira-devflow-worker-eligibility/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, headers=headers, data=data, method=method)
    with urlopen(request, timeout=10) as response:  # nosec B310: fixed GitHub HTTPS endpoint
        result = json.loads(response.read().decode())
    if not isinstance(result, dict):
        raise ValueError("invalid GitHub API response")
    return result


def forward_signed_webhook(raw, signature, event_name):
    """Forward a verified payload to the private service using a Cloud Run ID token."""
    token = id_token.fetch_id_token(GoogleAuthRequest(), ORCHESTRATOR_URL)
    internal_request = Request(
        f"{ORCHESTRATOR_URL}/github/webhook",
        data=raw,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Event": event_name,
            "X-Hub-Signature-256": signature,
            "User-Agent": "luvira-devflow-github-ingress/1",
        },
    )
    with urlopen(internal_request, timeout=10) as response:  # nosec B310: configured Cloud Run destination
        payload = json.loads(response.read().decode())
        status = response.status
    if not isinstance(payload, dict):
        raise ValueError("invalid internal orchestrator response")
    return status, payload
