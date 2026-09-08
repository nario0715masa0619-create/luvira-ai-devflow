import base64
import unittest
from source_snapshot import SourceSnapshotBuilder, SourceSnapshotError


class SourceSnapshotTest(unittest.TestCase):
    def test_reads_only_approved_paths_from_immutable_commit(self):
        builder = SourceSnapshotBuilder(lambda sha: [{"type":"blob","path":"src/a.py","sha":"a"},{"type":"blob","path":"README.md","sha":"b"}], lambda sha: {"content": base64.b64encode(b"print(1)").decode()})
        snapshot = builder.build("a" * 40, ("src/",))
        self.assertEqual(snapshot.paths, ("src/a.py",))
        self.assertIn(b"--- src/a.py", snapshot.content)

    def test_rejects_empty_or_unapproved_scope(self):
        builder = SourceSnapshotBuilder(lambda _: [], lambda _: {})
        with self.assertRaisesRegex(SourceSnapshotError, "scope_invalid"):
            builder.build("a" * 40, ("src/",))
