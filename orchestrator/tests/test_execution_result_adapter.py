import base64
import json
import unittest

from artifact_handoff import ArtifactHandoff, InMemoryVerifiedArtifactStore
from execution_result_adapter import BootstrapResultAdapter, ExecutionResultAdapterError, RESULT_PREFIX, extract_bootstrap_artifact
from test_artifact_verifier import bootstrap_artifact
from test_isolated_worker import valid_envelope


class FakeClient:
    def __init__(self):
        self.calls = []

    def start(self, envelope):
        self.calls.append(("start", envelope))
        return "luvira-devflow-isolated-worker-a1b2c"

    def wait_for_success(self, execution_id):
        self.calls.append(("wait", execution_id))


class FakeLogs:
    def __init__(self, stdout):
        self.stdout = stdout
        self.calls = []

    def read_stdout(self, execution_id):
        self.calls.append(execution_id)
        return self.stdout


def output_line(artifact=None):
    payload = json.dumps(artifact or bootstrap_artifact(), sort_keys=True).encode()
    return RESULT_PREFIX + base64.b64encode(payload).decode()


class ExecutionResultAdapterTest(unittest.TestCase):
    def test_extracts_exactly_one_result_line(self):
        payload = extract_bootstrap_artifact("routine log\n" + output_line() + "\ncompleted")
        self.assertEqual(json.loads(payload)["status"], "ENVELOPE_VALIDATED_NO_MODEL_EXECUTION")

    def test_rejects_missing_or_duplicate_result_line(self):
        with self.assertRaisesRegex(ExecutionResultAdapterError, "missing_or_ambiguous"):
            extract_bootstrap_artifact("completed")
        with self.assertRaisesRegex(ExecutionResultAdapterError, "missing_or_ambiguous"):
            extract_bootstrap_artifact(output_line() + "\n" + output_line())

    def test_waits_for_success_before_reading_and_handing_off(self):
        client = FakeClient()
        logs = FakeLogs(output_line())
        store = InMemoryVerifiedArtifactStore()
        adapter = BootstrapResultAdapter(client, logs, ArtifactHandoff(store))

        record = adapter.execute_and_verify(valid_envelope())

        self.assertEqual(client.calls[0][0], "start")
        self.assertEqual(client.calls[1], ("wait", record.execution_id))
        self.assertEqual(logs.calls, [record.execution_id])
        self.assertIn(record.execution_id, store.records)

    def test_does_not_persist_when_log_artifact_is_invalid(self):
        client = FakeClient()
        logs = FakeLogs(RESULT_PREFIX + "not-base64")
        store = InMemoryVerifiedArtifactStore()
        adapter = BootstrapResultAdapter(client, logs, ArtifactHandoff(store))

        with self.assertRaisesRegex(ExecutionResultAdapterError, "encoding_invalid"):
            adapter.execute_and_verify(valid_envelope())
        self.assertEqual(store.records, {})
