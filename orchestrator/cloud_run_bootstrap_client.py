"""Broker-only Cloud Run and Logging clients for bootstrap result retrieval."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

from execution_result_adapter import ExecutionResultAdapterError


class CloudRunBootstrapClientError(ExecutionResultAdapterError):
    pass


class CloudRunBootstrapClient:
    def __init__(self, project: str, region: str, job: str, session: Any | None = None, sleep=time.sleep):
        if not all(isinstance(value, str) and value for value in (project, region, job)):
            raise CloudRunBootstrapClientError("cloud_run_config_invalid")
        self.project, self.region, self.job = project, region, job
        self._session = session or AuthorizedSession(google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])[0])
        self._sleep = sleep

    @property
    def _job_url(self) -> str:
        return f"https://run.googleapis.com/v2/projects/{self.project}/locations/{self.region}/jobs/{self.job}"

    def start(self, envelope: dict) -> str:
        encoded = base64.b64encode(json.dumps(envelope, sort_keys=True).encode("utf-8")).decode("ascii")
        response = self._session.post(
            self._job_url + ":run",
            json={"overrides": {"containerOverrides": [{"env": [{"name": "WORKER_ENVELOPE_B64", "value": encoded}]}]}},
            timeout=30,
        )
        operation = self._json(response, "cloud_run_start_failed")
        operation_name = operation.get("name")
        if not isinstance(operation_name, str) or not operation_name:
            raise CloudRunBootstrapClientError("cloud_run_operation_invalid")
        for _ in range(30):
            result = self._json(self._session.get(f"https://run.googleapis.com/v2/{operation_name}", timeout=30), "cloud_run_operation_failed")
            if result.get("done"):
                execution = (result.get("response") or {}).get("name")
                if isinstance(execution, str) and execution.startswith(self._job_url + "/executions/"):
                    return execution.rsplit("/", 1)[1]
                raise CloudRunBootstrapClientError("cloud_run_execution_invalid")
            self._sleep(2)
        raise CloudRunBootstrapClientError("cloud_run_start_timeout")

    def wait_for_success(self, execution_id: str) -> None:
        if not isinstance(execution_id, str) or not execution_id:
            raise CloudRunBootstrapClientError("cloud_run_execution_invalid")
        for _ in range(60):
            result = self._json(self._session.get(f"{self._job_url}/executions/{execution_id}", timeout=30), "cloud_run_execution_read_failed")
            completed = next((c for c in result.get("conditions", []) if c.get("type") == "Completed"), None)
            if completed and completed.get("state") == "CONDITION_SUCCEEDED":
                return
            if completed and completed.get("state") in {"CONDITION_FAILED", "CONDITION_CANCELLED"}:
                raise CloudRunBootstrapClientError("cloud_run_execution_not_successful")
            self._sleep(2)
        raise CloudRunBootstrapClientError("cloud_run_execution_timeout")

    @staticmethod
    def _json(response: Any, reason: str) -> dict:
        if getattr(response, "status_code", 500) >= 300:
            raise CloudRunBootstrapClientError(reason)
        try:
            value = response.json()
        except (ValueError, AttributeError) as exc:
            raise CloudRunBootstrapClientError(reason) from exc
        if not isinstance(value, dict):
            raise CloudRunBootstrapClientError(reason)
        return value


class CloudLoggingBootstrapReader:
    """Read only the dedicated view; never query project-wide logs."""

    def __init__(self, project: str, region: str, bucket: str, view: str, session: Any | None = None):
        if not all(isinstance(value, str) and value for value in (project, region, bucket, view)):
            raise CloudRunBootstrapClientError("logging_view_config_invalid")
        self._view = f"projects/{project}/locations/{region}/buckets/{bucket}/views/{view}"
        self._session = session or AuthorizedSession(google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])[0])

    def read_stdout(self, execution_id: str) -> str:
        response = self._session.post(
            "https://logging.googleapis.com/v2/entries:list",
            json={"resourceNames": [self._view], "filter": f'labels."run.googleapis.com/execution_name"="{execution_id}"', "orderBy": "timestamp desc", "pageSize": 10},
            timeout=30,
        )
        payload = CloudRunBootstrapClient._json(response, "bootstrap_log_read_failed")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise CloudRunBootstrapClientError("bootstrap_log_response_invalid")
        return "\n".join(entry["textPayload"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("textPayload"), str))
