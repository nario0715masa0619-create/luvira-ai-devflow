import base64
import hmac
import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request

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

    return pending_context_lock(repository, issue, action)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


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
