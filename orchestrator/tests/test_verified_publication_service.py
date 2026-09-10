import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from github_verified_publisher import VerifiedPublicationError
from verified_publication_service import VerifiedPublicationService


class VerifiedPublicationServiceTest(unittest.TestCase):
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
