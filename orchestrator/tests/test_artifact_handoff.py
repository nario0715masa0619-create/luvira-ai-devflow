import json
import unittest

from artifact_handoff import ArtifactHandoff, ArtifactHandoffError, InMemoryVerifiedArtifactStore
from test_artifact_verifier import bootstrap_artifact
from test_isolated_worker import valid_envelope


class ArtifactHandoffTest(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryVerifiedArtifactStore()
        self.handoff = ArtifactHandoff(self.store)

    def test_broker_can_store_one_verified_result(self):
        payload = json.dumps(bootstrap_artifact(), sort_keys=True).encode()
        record = self.handoff.receive_from_broker("luvira-devflow-isolated-worker-a1b2c", valid_envelope(), payload)

        self.assertEqual(record.execution_id, "luvira-devflow-isolated-worker-a1b2c")
        self.assertEqual(record.artifact.task_id, valid_envelope()["task_id"])
        self.assertEqual(self.store.records[record.execution_id], record)

    def test_rejects_replay_even_when_bytes_are_identical(self):
        payload = json.dumps(bootstrap_artifact()).encode()
        self.handoff.receive_from_broker("luvira-devflow-isolated-worker-a1b2c", valid_envelope(), payload)

        with self.assertRaisesRegex(ArtifactHandoffError, "artifact_execution_already_received"):
            self.handoff.receive_from_broker("luvira-devflow-isolated-worker-a1b2c", valid_envelope(), payload)

    def test_rejects_invalid_execution_id_before_persistence(self):
        with self.assertRaisesRegex(ArtifactHandoffError, "worker_execution_id_invalid"):
            self.handoff.receive_from_broker("../other-execution", valid_envelope(), json.dumps(bootstrap_artifact()).encode())
        self.assertEqual(self.store.records, {})

    def test_rejects_unverified_result_before_persistence(self):
        artifact = bootstrap_artifact()
        artifact["publication"] = "create-pull-request"
        with self.assertRaisesRegex(ArtifactHandoffError, "worker_artifact_rejected"):
            self.handoff.receive_from_broker("luvira-devflow-isolated-worker-a1b2c", valid_envelope(), json.dumps(artifact).encode())
        self.assertEqual(self.store.records, {})

    def test_labels_store_outage_without_retaining_artifact(self):
        store = type("Store", (), {"put_once": lambda *_: (_ for _ in ()).throw(RuntimeError())})()
        handoff = ArtifactHandoff(store)
        with self.assertRaisesRegex(ArtifactHandoffError, "worker_artifact_store_unavailable"):
            handoff.receive_from_broker("luvira-devflow-isolated-worker-a1b2c", valid_envelope(), json.dumps(bootstrap_artifact()).encode())
