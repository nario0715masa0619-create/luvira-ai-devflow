import json
import unittest

from opencode_implementation_client import OpenCodeImplementationClient, OpenCodeImplementationError


class Response:
    def __init__(self, events): self.events = events
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def __iter__(self):
        for event in self.events:
            if event == "[DONE]":
                yield b"data: [DONE]\n"
            else:
                yield f"data: {json.dumps(event)}\n".encode()
            yield b"\n"


def artifact_events():
    return [
        {"choices": [{"delta": {"content": '{\"schema\":'}}]},
        {"choices": [{"delta": {"content": ' \"luvira.devflow.implementation-artifact.v1\"}'}}]},
        "[DONE]",
    ]


class OpenCodeImplementationClientTest(unittest.TestCase):
    def test_returns_only_provider_artifact_json_without_exposing_key(self):
        calls = []
        def transport(request, timeout):
            calls.append((request, timeout))
            return Response(artifact_events())
        api_key = "test-api-key-not-for-prompt"
        client = OpenCodeImplementationClient(api_key, transport)
        result = client.generate_artifact(model="kimi-k2.6", envelope={"task_id":"t","spec_hash":"a" * 64,"base_commit":"b" * 40,"allowed_paths":["src/"],"acceptance_criteria":["test"]}, source_snapshot=b"x")
        self.assertEqual(json.loads(result)["schema"], "luvira.devflow.implementation-artifact.v1")
        self.assertEqual(calls[0][1], 120)
        self.assertNotIn(api_key.encode(), calls[0][0].data)
        request_body = json.loads(calls[0][0].data)
        self.assertTrue(request_body["stream"])
        self.assertEqual(request_body["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0][0].get_header("X-opencode-session"), "luvira-0d9907cf80722b6e7d79ddfcc4ec1ba4")
        prompt = json.loads(request_body["messages"][0]["content"])
        self.assertTrue(any("diff_b64" in rule for rule in prompt["artifact_rules"]))
        self.assertTrue(any("GitHub CI is the merge gate" in rule for rule in prompt["artifact_rules"]))

    def test_session_id_is_stable_for_the_same_approved_task(self):
        calls = []
        def transport(request, timeout):
            calls.append(request)
            return Response(artifact_events())
        client = OpenCodeImplementationClient("secret", transport)
        envelope = {"task_id":"t", "spec_hash":"a" * 64, "base_commit":"b" * 40, "allowed_paths":["src/"], "acceptance_criteria":["test"]}
        client.generate_artifact(model="kimi-k2.6", envelope=envelope, source_snapshot=b"x")
        client.generate_artifact(model="kimi-k2.6", envelope=envelope, source_snapshot=b"x")
        self.assertEqual(calls[0].get_header("X-opencode-session"), calls[1].get_header("X-opencode-session"))
        self.assertNotIn("secret", calls[0].get_header("X-opencode-session"))

    def test_records_progress_only_after_stream_bytes_arrive(self):
        times = iter([0, 31])
        progress = []
        client = OpenCodeImplementationClient("secret", lambda *_, **__: Response(artifact_events()), lambda: next(times))
        client.generate_artifact(
            model="kimi-k2.6",
            envelope={"task_id":"t", "spec_hash":"a" * 64, "base_commit":"b" * 40, "allowed_paths":["src/"], "acceptance_criteria":["test"]},
            source_snapshot=b"x", on_progress=progress.append,
        )
        self.assertEqual(progress, [1, 2])

    def test_ignores_terminal_usage_frame_after_content(self):
        events = artifact_events()[:-1] + [
            {"choices": [], "usage": {"total_tokens": 1}},
            "[DONE]",
        ]
        client = OpenCodeImplementationClient("secret", lambda *_, **__: Response(events))
        result = client.generate_artifact(
            model="kimi-k2.6",
            envelope={"task_id":"t", "spec_hash":"a" * 64, "base_commit":"b" * 40, "allowed_paths":["src/"], "acceptance_criteria":["test"]},
            source_snapshot=b"x",
        )
        self.assertEqual(json.loads(result)["schema"], "luvira.devflow.implementation-artifact.v1")

    def test_accepts_only_a_complete_json_fence_as_a_compatibility_fallback(self):
        events = [
            {"choices": [{"delta": {"content": "```json\\n{\\\"schema\\\": \\\"luvira.devflow.implementation-artifact.v1\\\"}\\n```"}}]},
            "[DONE]",
        ]
        events = [
            {"choices": [{"delta": {"content": "```json" + chr(10) + '{"schema":"luvira.devflow.implementation-artifact.v1"}' + chr(10) + "```"}}]},
            "[DONE]",
        ]
        client = OpenCodeImplementationClient("secret", lambda *_, **__: Response(events))
        result = client.generate_artifact(
            model="kimi-k2.6",
            envelope={"task_id":"t", "spec_hash":"a" * 64, "base_commit":"b" * 40, "allowed_paths":["src/"], "acceptance_criteria":["test"]},
            source_snapshot=b"x",
        )
        self.assertEqual(json.loads(result)["schema"], "luvira.devflow.implementation-artifact.v1")

    def test_rejects_invalid_source_before_network(self):
        client = OpenCodeImplementationClient("secret", lambda *_: self.fail("network"))
        with self.assertRaisesRegex(OpenCodeImplementationError, "SOURCE_INVALID"):
            client.generate_artifact(model="kimi-k2.6", envelope={}, source_snapshot=b"\xff")
