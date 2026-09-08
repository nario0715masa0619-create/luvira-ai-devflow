import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from implementation_artifact_handoff import ImplementationArtifactHandoff, InMemoryImplementationArtifactStore
from implementation_execution_service import ImplementationExecutionService
from test_implementation_artifact_verifier import payload


def ready_task():
    spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40, "requested_action": "implementation",
        "acceptance_criteria": ["test"], "budget": {"max_cost_usd": 1},
        "expiry": "2026-12-01T00:00:00Z", "execution_scope": {"allowed_paths": ["src/"]},
        "model_policy": "low-cost-first:opencode-go",
    })
    execution = ExecutionRecord("execution-123", "task", spec.hash, 1, V3Status.WORKER_HEALTH_VERIFIED)
    return V3Task("task", spec, V3Status.WORKER_HEALTH_VERIFIED, execution=execution)


class ImplementationExecutionServiceTest(unittest.TestCase):
    def test_claims_before_one_verified_provider_result(self):
        task, calls = ready_task(), []
        tasks = type("Tasks", (), {"worker_health_verified": lambda _: [task]})()
        transaction = type("Tx", (), {
            "claim_implementation": lambda _, current, __: calls.append(current.status),
            "record_result": lambda _, current, __, status: calls.append(status),
        })()
        client = type("Client", (), {"generate_artifact": lambda _self, **kwargs: payload(
            task_id=kwargs["envelope"]["task_id"], spec_hash=kwargs["envelope"]["spec_hash"],
            base_commit=kwargs["envelope"]["base_commit"],
        )})()
        service = ImplementationExecutionService(tasks, transaction, lambda _: b"source", client, "kimi-k2.6", ImplementationArtifactHandoff(InMemoryImplementationArtifactStore()))

        self.assertEqual(service.sweep(), [("task", "IMPLEMENTATION_ARTIFACT_VERIFIED", "execution-123")])
        self.assertEqual(task.status, V3Status.ARTIFACT_VERIFIED)
        self.assertEqual(calls, [V3Status.IMPLEMENTATION_GENERATING, V3Status.ARTIFACT_VERIFIED])
