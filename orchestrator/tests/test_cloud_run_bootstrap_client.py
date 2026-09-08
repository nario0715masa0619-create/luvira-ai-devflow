import unittest

from cloud_run_bootstrap_client import CloudLoggingBootstrapReader, CloudRunBootstrapClient
from test_isolated_worker import valid_envelope


class Response:
    def __init__(self, value, status=200): self.value, self.status_code = value, status
    def json(self): return self.value


class Session:
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def post(self, url, **kwargs): self.calls.append(("post", url, kwargs)); return self.replies.pop(0)
    def get(self, url, **kwargs): self.calls.append(("get", url, kwargs)); return self.replies.pop(0)


class CloudRunBootstrapClientTest(unittest.TestCase):
    def test_starts_with_one_ephemeral_envelope_override_and_waits_for_success(self):
        base = "projects/p/locations/us-central1/jobs/j"
        session = Session([Response({"name": "operations/1"}), Response({"done": True, "response": {"name": base + "/executions/e-123"}}), Response({"conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}]})])
        client = CloudRunBootstrapClient("p", "us-central1", "j", session=session, sleep=lambda _: None)
        execution = client.start(valid_envelope())
        client.wait_for_success(execution)
        self.assertEqual(execution, "e-123")
        override = session.calls[0][2]["json"]["overrides"]["containerOverrides"][0]["env"]
        self.assertEqual([item["name"] for item in override], ["WORKER_ENVELOPE_B64"])

    def test_reads_only_the_dedicated_log_view(self):
        session = Session([Response({"entries": [{"textPayload": "LUVIRA_BOOTSTRAP_ARTIFACT_B64=abc"}]})])
        reader = CloudLoggingBootstrapReader("p", "us-central1", "b", "v", session=session)
        self.assertEqual(reader.read_stdout("e-123"), "LUVIRA_BOOTSTRAP_ARTIFACT_B64=abc")
        self.assertIn("projects/p/locations/us-central1/buckets/b/views/v", session.calls[0][2]["json"]["resourceNames"])

    def test_reads_one_completion_state_without_waiting(self):
        session = Session([Response({"conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}]})])
        client = CloudRunBootstrapClient("p", "us-central1", "j", session=session)
        self.assertEqual(client.completion_state("e-123"), "SUCCEEDED")
