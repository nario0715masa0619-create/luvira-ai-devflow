"""Broker-only OpenCode Go client for diff-only implementation artifacts.

The client has no GitHub write capability.  It asks the provider for one JSON
artifact, not shell commands or a repository checkout, and returns raw bytes
to the verifier without logging provider content.
"""

from __future__ import annotations

import hashlib
import base64
import json
import socket
import time
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from opencode_provider_policy import classify_opencode_failure


ENDPOINT = "https://opencode.ai/zen/go/v1/chat/completions"
MAX_SOURCE_BYTES = 256 * 1024
# This is an idle read timeout, not a total generation limit.  SSE events reset
# it, so an active generation can continue while a silent connection is safely
# classified as retryable.
STREAM_IDLE_TIMEOUT_SECONDS = 120
PROGRESS_PERSIST_INTERVAL_SECONDS = 30


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
            "schema": "luvira.devflow.implementation-artifact.v2",
            "publication": "verification-only",
            "task_id": envelope["task_id"],
            "spec_hash": envelope["spec_hash"],
            "base_commit": envelope["base_commit"],
            "allowed_paths": envelope["allowed_paths"],
            "required_fields": ["schema", "task_id", "spec_hash", "base_commit", "files", "changed_paths", "tests", "publication"],
        },
        "acceptance_criteria": envelope["acceptance_criteria"],
        "artifact_rules": [
            "Return a JSON object with exactly the contract.required_fields plus schema and publication; do not omit or add fields.",
            "files must be a non-empty JSON array of {path,content}; content is the complete UTF-8 replacement text for that changed file and must end with a newline. Never return a diff or base64.",
            "files and changed_paths must use the same sorted unique paths, and every path must be within allowed_paths.",
            "tests must be an array of {name,status}; status is only passed or skipped. Leave it empty unless a result is actually available; GitHub CI is the merge gate.",
            "Do not change protected paths, include secrets, use /dev/null paths, or include prose outside the JSON object.",
        ],
        "source_snapshot": source,
    }, ensure_ascii=False, separators=(",", ":"))


def _session_id(envelope: dict[str, Any]) -> str:
    """Return one stable, non-sensitive provider session per approved task."""
    value = f"{envelope['task_id']}:{envelope['spec_hash']}".encode("utf-8")
    return f"luvira-{hashlib.sha256(value).hexdigest()[:32]}"


class OpenCodeImplementationClient:
    """Uses a Broker-held key; callers receive no provider response metadata."""

    def __init__(self, api_key: str, transport: Transport = urlopen,
                 clock: Callable[[], float] = time.monotonic):
        if not isinstance(api_key, str) or not api_key:
            raise OpenCodeImplementationError("OPENCODE_NOT_CONFIGURED")
        self._api_key, self._transport, self._clock = api_key, transport, clock

    def generate_artifact(self, *, model: str, envelope: dict[str, Any], source_snapshot: bytes,
                          on_progress: Callable[[int], None] | None = None) -> bytes:
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
            "stream": True,
            # The Go endpoint is OpenAI-chat-compatible.  Require the
            # transport to constrain the response as JSON instead of relying
            # solely on a natural-language instruction after a long stream.
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        request = Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "luvira-devflow-broker/1",
            "x-opencode-session": _session_id(envelope),
        })
        try:
            with self._transport(request, timeout=STREAM_IDLE_TIMEOUT_SECONDS) as response:
                content = self._read_stream(response, on_progress)
        except HTTPError as exc:
            raise OpenCodeImplementationError(classify_opencode_failure(exc.code, timeout=False)) from exc
        except (URLError, TimeoutError, socket.timeout):
            raise OpenCodeImplementationError(classify_opencode_failure(None, timeout=True))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise OpenCodeImplementationError("OPENCODE_PROTOCOL_FINAL") from exc
        try:
            artifact = self._verifier_artifact(self._decode_artifact(content))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            # A completed stream with a non-JSON artifact is a provider
            # output failure, not a permanently invalid approved task.  The
            # durable queue may make a bounded retry with the structured
            # response contract; it never retains the model output.
            raise OpenCodeImplementationError("OPENCODE_ARTIFACT_INVALID_RETRYABLE") from exc
        return json.dumps(artifact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _decode_artifact(content: str) -> Any:
        """Decode a JSON response without accepting arbitrary surrounding prose."""
        try:
            return json.loads(content)
        except json.JSONDecodeError as direct_error:
            stripped = content.strip()
            if not (stripped.startswith("```json\n") and stripped.endswith("\n```")):
                raise direct_error
            return json.loads(stripped[len("```json\n"):-len("\n```")])

    @staticmethod
    def _verifier_artifact(artifact: Any) -> dict[str, Any]:
        """Preserve a full-file artifact or convert a legacy readable diff.

        Base64 is a transport encoding, not an implementation task.  Keeping
        that transformation in the Broker removes an error-prone generation
        step while preserving the immutable verifier contract.
        """
        if not isinstance(artifact, dict) or "diff_b64" in artifact:
            raise ValueError("artifact_shape_invalid")
        # v2 intentionally contains complete file contents.  The verifier
        # creates the only publishable diff from the immutable source snapshot,
        # so the model never has to reproduce fragile hunk context.
        if artifact.get("schema") == "luvira.devflow.implementation-artifact.v2":
            if "diff" in artifact:
                raise ValueError("artifact_shape_invalid")
            return artifact
        diff = artifact.pop("diff", None)
        if not isinstance(diff, str) or not diff:
            raise ValueError("artifact_diff_invalid")
        artifact["diff_b64"] = base64.b64encode(diff.encode("utf-8")).decode("ascii")
        return artifact

    def _read_stream(self, response, on_progress: Callable[[int], None] | None) -> str:
        """Read OpenAI-compatible SSE and retain only the resulting artifact."""
        fragments: list[str] = []
        event_data: list[str] = []
        stream_events, last_persisted = 0, None

        def consume_event() -> bool:
            nonlocal stream_events, last_persisted
            if not event_data:
                return False
            raw = "\n".join(event_data)
            event_data.clear()
            if raw == "[DONE]":
                return True
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("stream_event_invalid")
            choices = payload.get("choices")
            # OpenAI-compatible providers can emit a final usage or terminal
            # frame after all text deltas.  It is protocol metadata, not a
            # malformed artifact chunk, and must not discard a completed
            # long-running generation.
            if choices is None or choices == []:
                return False
            if not isinstance(choices, list) or not isinstance(choices[0], dict):
                raise ValueError("stream_event_invalid")
            delta = choices[0].get("delta")
            if delta is None:
                return False
            if not isinstance(delta, dict):
                raise ValueError("stream_event_invalid")
            # OpenAI-compatible streams commonly end with a final delta whose
            # ``content`` is null and whose ``finish_reason`` is set on the
            # surrounding choice.  It carries no artifact text; rejecting it
            # after a long, otherwise valid stream turns a completed provider
            # response into a false terminal protocol failure.
            content = delta.get("content", "")
            if content is None:
                return False
            if not isinstance(content, str):
                raise ValueError("stream_content_invalid")
            fragments.append(content)
            stream_events += 1
            now = self._clock()
            if (on_progress is not None and (last_persisted is None
                    or now - last_persisted >= PROGRESS_PERSIST_INTERVAL_SECONDS)):
                on_progress(stream_events)
                last_persisted = now
            return False

        for line in response:
            text = line.decode("utf-8").rstrip("\r\n")
            if not text:
                if consume_event():
                    return "".join(fragments)
            elif text.startswith("data:"):
                event_data.append(text[5:].lstrip())
            # SSE comments and metadata intentionally carry no model content.
        if consume_event():
            return "".join(fragments)
        # Some OpenAI-compatible SSE relays close the connection after the
        # final content frame without sending a separate ``[DONE]`` frame.
        # Once content has arrived, EOF is an alternate terminal framing, not
        # evidence that the completed artifact is invalid.  JSON decoding and
        # verifier validation still reject an incomplete artifact below.
        if fragments:
            return "".join(fragments)
        raise ValueError("stream_ended_before_done")
