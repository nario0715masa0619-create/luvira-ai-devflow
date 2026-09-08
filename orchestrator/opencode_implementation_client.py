"""Broker-only OpenCode Go client for diff-only implementation artifacts.

The client has no GitHub write capability.  It asks the provider for one JSON
artifact, not shell commands or a repository checkout, and returns raw bytes
to the verifier without logging provider content.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from opencode_provider_policy import classify_opencode_failure


ENDPOINT = "https://opencode.ai/zen/go/v1/chat/completions"
MAX_SOURCE_BYTES = 256 * 1024


class OpenCodeImplementationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Transport(Protocol):
    def __call__(self, request: Request, timeout: int): ...


def _prompt(envelope: dict[str, Any], source: str) -> str:
    return json.dumps({
        "role": "implementation-agent",
        "instruction": "Return exactly one JSON implementation artifact. Do not return Markdown, commands, prose, credentials, or a pull request.",
        "contract": {
            "schema": "luvira.devflow.implementation-artifact.v1",
            "publication": "verification-only",
            "task_id": envelope["task_id"],
            "spec_hash": envelope["spec_hash"],
            "base_commit": envelope["base_commit"],
            "allowed_paths": envelope["allowed_paths"],
            "required_fields": ["schema", "task_id", "spec_hash", "base_commit", "diff_b64", "changed_paths", "tests", "publication"],
        },
        "acceptance_criteria": envelope["acceptance_criteria"],
        "source_snapshot": source,
    }, ensure_ascii=False, separators=(",", ":"))


class OpenCodeImplementationClient:
    """Uses a Broker-held key; callers receive no provider response metadata."""

    def __init__(self, api_key: str, transport: Transport = urlopen):
        if not isinstance(api_key, str) or not api_key:
            raise OpenCodeImplementationError("OPENCODE_NOT_CONFIGURED")
        self._api_key, self._transport = api_key, transport

    def generate_artifact(self, *, model: str, envelope: dict[str, Any], source_snapshot: bytes) -> bytes:
        if not isinstance(model, str) or not model or not isinstance(source_snapshot, bytes) or len(source_snapshot) > MAX_SOURCE_BYTES:
            raise OpenCodeImplementationError("IMPLEMENTATION_INPUT_INVALID")
        try:
            source = source_snapshot.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OpenCodeImplementationError("IMPLEMENTATION_SOURCE_INVALID") from exc
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": _prompt(envelope, source)}],
            "temperature": 0,
        }).encode("utf-8")
        request = Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "luvira-devflow-broker/1",
        })
        try:
            with self._transport(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise OpenCodeImplementationError(classify_opencode_failure(exc.code, timeout=False)) from exc
        except (URLError, TimeoutError):
            raise OpenCodeImplementationError(classify_opencode_failure(None, timeout=True))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise OpenCodeImplementationError("OPENCODE_PROTOCOL_FINAL") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            artifact = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise OpenCodeImplementationError("OPENCODE_PROTOCOL_FINAL") from exc
        return json.dumps(artifact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
