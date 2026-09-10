import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from github_verified_publisher import VerifiedPublicationError
from verified_publication_service import VerifiedPublicationService, publication_text


class VerifiedPublicationServiceTest(unittest.TestCase):
    def test_uses_japanese_metadata_for_an_implementation_pr(self):
        self.assertEqual(
            publication_text("implementation"),
            ("Luvira: 実装成果物", "承認済みタスクから生成・検証された実装成果物です。独立レビューと人による確認後にマージしてください。"),
        )

    def test_uses_japanese_fallback_metadata(self):
        self.assertEqual(publication_text("other")[0], "Luvira: 承認済み成果物")

    def test_base_mismatch_becomes_one_terminal_result(self):
        spec = TaskSpec.from_dict({
            "repository": "a/b", "base_commit": "a" * 40, "requested_action": "implementation",
            "acceptance_criteria": ["test"], "budget": {"max_cost_usd": 1},
            "expiry": "2026-12-01T00:00:00Z", "execution_scope": {"allowed_paths": ["src/"]},
            "model_policy": "low-cost",
        })
        record = ExecutionRecord("execution", "task", spec.hash, 1, V3Status.ARTIFACT_VERIFIED)
        task = V3Task("task", spec, V3Status.ARTIFACT_VERIFIED, execution=record)
        tasks = type("Tasks", (), {"artifact_verified": lambda _: [task]})()
        transaction = type("Tx", (), {"record_result": lambda *_: None})()
        artifact = type("Artifact", (), {"base_commit": spec.base_commit, "diff": b""})()
        artifacts = type("Artifacts", (), {"get": lambda *_: type("Record", (), {"artifact": artifact})()})()
        publisher = type("Publisher", (), {"publish": lambda *_args, **_kwargs: (_ for _ in ()).throw(VerifiedPublicationError("publication_base_content_missing"))})()

        outcomes = VerifiedPublicationService(tasks, transaction, artifacts, lambda _: publisher).sweep()

        self.assertEqual(outcomes, [("task", "PUBLICATION_ARTIFACT_INVALID_FINAL", "execution")])
        self.assertEqual(task.status, V3Status.EXECUTION_FAILED_FINAL)
