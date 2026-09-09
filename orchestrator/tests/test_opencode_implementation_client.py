import json
import unittest

from opencode_implementation_client import OpenCodeImplementationClient, OpenCodeImplementationError


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.payload).encode()


class OpenCodeImplementationClientTest(unittest.TestCase):
    def test_returns_only_provider_artifact_json_without_exposing_key(self):
        calls = []
        def transport(request, timeout):
            calls.append((request, timeout))
            return Response({"choices": [{"message": {"content": '{"schema":"luvira.devflow.implementation-artifact.v1"}'}}]})
        api_key = "test-api-key-not-for-prompt"
        client = OpenCodeImplementationClient(api_key, transport)
        result = client.generate_artifact(model="kimi-k2.6", envelope={"task_id":"t","spec_hash":"a" * 64,"base_commit":"b" * 40,"allowed_paths":["src/"],"acceptance_criteria":["test"]}, source_snapshot=b"x")
        self.assertEqual(json.loads(result)["schema"], "luvira.devflow.implementation-artifact.v1")
        self.assertNotIn(api_key.encode(), calls[0][0].data)
        request_body = json.loads(calls[0][0].data)
        prompt = json.loads(request_body["messages"][0]["content"])
        self.assertTrue(any("diff_b64" in rule for rule in prompt["artifact_rules"]))
        self.assertTrue(any("GitHub CI is the merge gate" in rule for rule in prompt["artifact_rules"]))

    def test_rejects_invalid_source_before_network(self):
        client = OpenCodeImplementationClient("secret", lambda *_: self.fail("network"))
        with self.assertRaisesRegex(OpenCodeImplementationError, "SOURCE_INVALID"):
            client.generate_artifact(model="kimi-k2.6", envelope={}, source_snapshot=b"\xff")
