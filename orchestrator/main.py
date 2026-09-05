import base64
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

from control_plane import ControlPlane, ControlPlaneError, FirestoreTaskStore, TaskConflict, TaskStatus, spec_hash

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
TASK_STORE_BACKEND = os.environ.get("TASK_STORE_BACKEND", "").strip().lower()
FIRESTORE_TASK_COLLECTION = os.environ.get("FIRESTORE_TASK_COLLECTION", "").strip()


def create_control_plane_from_environment() -> ControlPlane:
    """Create the production store only from explicit, non-secret settings."""
    if TASK_STORE_BACKEND != "firestore":
        raise RuntimeError("control_plane_backend_must_be_firestore")
    if not FIRESTORE_TASK_COLLECTION:
        raise RuntimeError("firestore_task_collection_not_configured")
    return ControlPlane(FirestoreTaskStore(firestore.Client(), collection=FIRESTORE_TASK_COLLECTION))


def initialize_control_plane() -> ControlPlane | None:
    """Fail Cloud Run startup closed; local and unit-test imports remain inert."""
    if not os.environ.get("K_SERVICE"):
        return None
    return create_control_plane_from_environment()


CONTROL_PLANE = initialize_control_plane()


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
    """Read-only proof that the deployed identity can reach Firestore."""
    if CONTROL_PLANE is None:
        logging.error("CONTROL_PLANE_BLOCKED durable store is not initialized")
        return jsonify(status="BLOCKED", reason="control_plane_not_configured"), 503
    try:
        store = CONTROL_PLANE.store
        if not isinstance(store, FirestoreTaskStore):
            raise RuntimeError("control_plane_store_is_not_durable")
        store.readiness_check()
    except Exception:
        logging.warning("CONTROL_PLANE_BLOCKED Firestore readiness check failed")
        return jsonify(status="BLOCKED", reason="control_plane_unavailable"), 503
    return jsonify(status="READY", backend="firestore", collection=FIRESTORE_TASK_COLLECTION)


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


@app.get("/readiness/github-worker")
def github_worker_readiness():
    """Verify the Worker App identity without creating a token, branch, or PR."""
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

    logging.info("GITHUB_WORKER_READY installation_id=%s account=%s", installation_id, account)
    return jsonify(status="READY", provider="github-worker", installation_id=int(installation_id), account=account)


@app.post("/worker/eligibility")
def worker_eligibility():
    """Read GitHub's immutable CI records before any future Worker write is considered."""
    proposal = request.get_json(silent=True) or {}
    issue = proposal.get("issue")
    source_branch = proposal.get("source_branch")
    if proposal.get("repository") != EXPECTED_REPOSITORY or not isinstance(issue, int) or issue < 1:
        return jsonify(status="BLOCKED", reason="repository_or_issue_mismatch"), 400
    if not isinstance(source_branch, str) or not source_branch.startswith(f"worker/issue-{issue}-"):
        return jsonify(status="BLOCKED", reason="source_branch_not_allowed"), 400

    app_id = os.environ.get("GITHUB_WORKER_APP_ID", "")
    installation_id = os.environ.get("GITHUB_WORKER_INSTALLATION_ID", "")
    private_key = os.environ.get("GITHUB_WORKER_PRIVATE_KEY", "")
    if not app_id or not installation_id or not private_key:
        logging.error("GITHUB_WORKER_BLOCKED eligibility credential is not configured")
        return jsonify(status="BLOCKED", reason="github_worker_not_configured"), 503
    try:
        evidence = github_worker_quality_evidence(app_id, installation_id, private_key, source_branch)
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
    if CONTROL_PLANE is None:
        logging.error("CONTROL_PLANE_BLOCKED durable store is not initialized")
        return jsonify(status="BLOCKED", reason="control_plane_not_configured"), 503
    try:
        spec = approval_issue_spec(payload, repository, issue)
        task_id = f"github-issue-{issue}-{spec_hash(spec)[:16]}"
        try:
            task = CONTROL_PLANE.create_draft(spec, actor="github-webhook", task_id=task_id)
        except TaskConflict:
            task = CONTROL_PLANE.store.get(task_id)
        if task.status is TaskStatus.DRAFT:
            task = CONTROL_PLANE.validate(task.task_id, actor="control-plane")
        if task.status is TaskStatus.VALIDATED:
            task = CONTROL_PLANE.request_human_approval(task.task_id, actor="control-plane")
    except (ControlPlaneError, ValueError):
        logging.warning("CONTROL_PLANE_BLOCKED invalid approval issue=%s action=%s", issue, action)
        return jsonify(status="BLOCKED", reason="invalid_approval_issue"), 400

    if task.status is not TaskStatus.AWAITING_HUMAN_APPROVAL:
        logging.warning("CONTROL_PLANE_BLOCKED task=%s status=%s", task.task_id, task.status.value)
        return jsonify(status="BLOCKED", reason="approval_task_not_pending"), 409
    return jsonify(
        status="AWAITING_HUMAN_APPROVAL",
        task_id=task.task_id,
        spec_hash=task.spec_hash,
        approval_binding=task.approval_binding,
        issue=issue,
    )


def approval_issue_spec(payload, repository, issue_number):
    """Parse only the fixed Issue Form fields into the control-plane schema."""
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
    )}
    if form["Repository"] != repository:
        raise ValueError("repository_form_mismatch")
    criteria = [line.strip("- ") for line in form["受入条件"].splitlines() if line.strip()]
    try:
        max_cost_usd = float(form["最大コスト（USD）"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_max_cost_usd") from exc
    if not criteria or max_cost_usd <= 0:
        raise ValueError("invalid_approval_form")
    return {
        "project_id": form["Project ID"],
        "repository": repository,
        "base_commit": github_default_branch_sha(repository),
        "task_type": form["タスク種別"],
        "acceptance_criteria": criteria,
        "budget": {"max_cost_usd": max_cost_usd},
        "requested_action": form["許可を求める最初のアクション"],
        "approval": form["承認すること"],
        "impact": form["影響"],
        "excluded": form["しないこと"],
        "expiry": form["有効期限（UTC）"],
        "source": {"issue_number": issue_number, "issue_node_id": issue.get("node_id")},
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
    now_epoch = int(time.time())
    app_jwt = jwt.encode({"iat": now_epoch - 60, "exp": now_epoch + 540, "iss": app_id}, private_key, algorithm="RS256")
    installation_token = github_api_request(
        f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens", method="POST", token=app_jwt
    ).get("token")
    if not isinstance(installation_token, str):
        raise ValueError("invalid_installation_token")
    ref = github_api_request(f"{GITHUB_API_URL}/repos/{repository}/git/ref/heads/main", token=installation_token)
    sha = (ref.get("object") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise ValueError("invalid_base_commit")
    return sha


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


def github_worker_quality_evidence(app_id, installation_id, private_key, source_branch):
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
        f"{GITHUB_API_URL}/repos/{EXPECTED_REPOSITORY}/git/ref/heads/{quote(source_branch, safe='')}",
        token=installation_token,
    )
    head_sha = ((ref.get("object") or {}).get("sha"))
    if not isinstance(head_sha, str):
        raise ValueError("invalid GitHub ref response")
    runs = github_api_request(
        f"{GITHUB_API_URL}/repos/{EXPECTED_REPOSITORY}/actions/runs?head_sha={quote(head_sha, safe='')}&per_page=100",
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


def github_api_request(url, method="GET", token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "luvira-devflow-worker-eligibility/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method=method)
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
