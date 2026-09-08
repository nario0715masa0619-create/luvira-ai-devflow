import unittest

from implementation_artifact_handoff import (
    ImplementationArtifactHandoff,
    ImplementationArtifactHandoffError,
    InMemoryImplementationArtifactStore,
)
from test_implementation_artifact_verifier import ENVELOPE, payload


class ImplementationArtifactHandoffTest(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryImplementationArtifactStore()
        self.handoff = ImplementationArtifactHandoff(self.store)

    def test_stores_one_verified_scoped_diff_without_publishing_it(self):
        record = self.handoff.receive_from_broker("execution-123", ENVELOPE, payload())
        self.assertEqual(record.artifact.changed_paths, ("src/example.py",))
        self.assertEqual(self.store.records["execution-123"], record)

    def test_rejects_replay_and_invalid_artifact(self):
        self.handoff.receive_from_broker("execution-123", ENVELOPE, payload())
        with self.assertRaisesRegex(ImplementationArtifactHandoffError, "replayed"):
            self.handoff.receive_from_broker("execution-123", ENVELOPE, payload())
        with self.assertRaisesRegex(ImplementationArtifactHandoffError, "rejected"):
            self.handoff.receive_from_broker("execution-456", ENVELOPE, b"{}")
