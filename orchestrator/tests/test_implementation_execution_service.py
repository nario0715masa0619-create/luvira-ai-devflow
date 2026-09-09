import unittest

from execution_platform import ExecutionRecord, TaskSpec, V3Status, V3Task
from implementation_artifact_handoff import (
    ImplementationArtifactHandoff,
    ImplementationArtifactHandoffError,
    InMemoryImplementationArtifactStore,
)
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


def validation_task():
    task = ready_task()
    task.spec = TaskSpec.from_dict({
        "repository": "a/b", "base_commit": "a" * 40, "requested_action": "validation",
        "acceptance_criteria": ["health proof"], "budget": {"max_cost_usd": 0.1},
        "expiry": "2026-12-01T00:00:00Z", "execution_scope": {"allowed_paths": ["README.md"]},
        "model_policy": "none",
    })
    task.execution.spec_hash = task.spec.hash
    return task


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

    def test_validation_policy_never_calls_provider_or_reads_source(self):
        task, calls = validation_task(), []
        tasks = type("Tasks", (), {"worker_health_verified": lambda _: [task]})()
        transaction = type("Tx", (), {
            "claim_implementation": lambda *_: self.fail("must not claim provider work"),
            "record_result": lambda _, current, __, status: calls.append(status),
        })()
        client = type("Client", (), {"generate_artifact": lambda *_args, **_kwargs: self.fail("must not call provider")})()
        service = ImplementationExecutionService(
            tasks, transaction,
            lambda _: self.fail("must not read source"), client, "kimi-k2.6",
            ImplementationArtifactHandoff(InMemoryImplementationArtifactStore()),
        )

        self.assertEqual(service.sweep(), [("task", "VALIDATION_SUCCEEDED", "execution-123")])
        self.assertEqual(task.status, V3Status.VALIDATION_SUCCEEDED)
        self.assertEqual(calls, [V3Status.VALIDATION_SUCCEEDED])

    def test_records_the_sanitized_artifact_verifier_code(self):
        task, calls = ready_task(), []
        tasks = type("Tasks", (), {"worker_health_verified": lambda _: [task]})()
        transaction = type("Tx", (), {
            "claim_implementation": lambda *_: None,
            "record_result": lambda _, current, __, status: calls.append(status),
        })()
        client = type("Client", (), {"generate_artifact": lambda *_args, **_kwargs: b"{}"})()
        handoff = type("Handoff", (), {
            "receive_from_broker": lambda *_: (_ for _ in ()).throw(
                ImplementationArtifactHandoffError("artifact_schema_mismatch")
            )
        })()
        service = ImplementationExecutionService(tasks, transaction, lambda _: b"source", client, "kimi-k2.6", handoff)

        self.assertEqual(service.sweep(), [("task", "EXECUTION_FAILED_FINAL", "execution-123")])
        self.assertEqual(task.execution.failure_code, "artifact_schema_mismatch")
