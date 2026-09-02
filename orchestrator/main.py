import base64
import json
import logging
import os

from flask import Flask, jsonify, request

app = Flask(__name__)
EXPECTED_REPOSITORY = os.environ.get("EXPECTED_REPOSITORY", "nario0715masa0619-create/luvira-ai-devflow")


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

    logging.info("CONTEXT_LOCK_PENDING repository=%s issue=%s action=%s", repository, issue, action)
    return jsonify(status="PENDING_CONTEXT_LOCK", repository=repository, issue=issue)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")
