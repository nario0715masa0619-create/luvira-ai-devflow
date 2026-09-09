import unittest

from implementation_artifact_handoff import (
    FirestoreImplementationArtifactStore,
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
        with self.assertRaisesRegex(ImplementationArtifactHandoffError, "artifact_schema_mismatch"):
            self.handoff.receive_from_broker("execution-456", ENVELOPE, b"{}")


class _Document:
    def __init__(self):
        self.created = None

    def create(self, payload):
        self.created = payload


class _Collection:
    def __init__(self):
        self.document_value = _Document()

    def document(self, _execution_id):
        return self.document_value


class _Client:
    def __init__(self):
        self.collection_value = _Collection()

    def collection(self, _name):
        return self.collection_value


class FirestoreImplementationArtifactStoreTest(unittest.TestCase):
    def test_persists_only_verified_diff_for_a_later_publication_boundary(self):
        client = _Client()
        handoff = ImplementationArtifactHandoff(FirestoreImplementationArtifactStore(client))

        handoff.receive_from_broker("execution-123", ENVELOPE, payload())

        stored = client.collection_value.document_value.created
        self.assertEqual(stored["execution_id"], "execution-123")
        self.assertEqual(stored["artifact"]["publication"], "verification-only")
        self.assertEqual(stored["artifact"]["changed_paths"], ["src/example.py"])
