import os
import unittest
from unittest.mock import Mock, patch

# Keep module import order from changing the webhook test fixture used by the
# rest of this suite.  Production always injects this value from Secret
# Manager; the unit suite deliberately uses a non-secret fixture.
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
import main


class EnvironmentIsolationTest(unittest.TestCase):
    def test_private_runtime_requires_every_durable_boundary(self):
        with patch.dict(os.environ, {"K_SERVICE": "staging"}), patch.multiple(
            main,
            V3_TASK_COLLECTION="devflow_staging_execution_tasks",
            BROKER_SERVICE_ACCOUNT="broker@example.iam.gserviceaccount.com",
            WORKER_JOB="luvira-devflow-isolated-worker-bootstrap-staging",
            WORKER_REGION="us-central1",
            WORKER_ARTIFACT_BUCKET="luvira-devflow-staging-123456",
            WORKER_ARTIFACT_VIEW="bootstrap-results",
            VERIFIED_ARTIFACT_COLLECTION="devflow_staging_verified_artifacts",
            IMPLEMENTATION_ARTIFACT_COLLECTION="devflow_staging_verified_implementation_artifacts",
            RUNTIME_HEALTH_COLLECTION="devflow_staging_runtime_health",
        ), patch("main.create_v3_queue_service", return_value=Mock()) as create_runtime:
            main.create_v3_queue_from_environment()

        arguments = create_runtime.call_args.kwargs
        self.assertEqual(arguments["task_collection"], "devflow_staging_execution_tasks")
        self.assertEqual(arguments["artifact_bucket"], "luvira-devflow-staging-123456")
        self.assertEqual(arguments["artifact_collection"], "devflow_staging_verified_artifacts")
        self.assertEqual(
            arguments["implementation_artifact_collection"],
            "devflow_staging_verified_implementation_artifacts",
        )
        self.assertEqual(arguments["runtime_health_collection"], "devflow_staging_runtime_health")

    def test_private_runtime_fails_closed_when_a_durable_boundary_is_missing(self):
        with patch.dict(os.environ, {"K_SERVICE": "staging"}), patch.multiple(
            main,
            V3_TASK_COLLECTION="devflow_staging_execution_tasks",
            BROKER_SERVICE_ACCOUNT="broker@example.iam.gserviceaccount.com",
            WORKER_JOB="luvira-devflow-isolated-worker-bootstrap-staging",
            WORKER_REGION="us-central1",
            WORKER_ARTIFACT_BUCKET="luvira-devflow-staging-123456",
            WORKER_ARTIFACT_VIEW="bootstrap-results",
            VERIFIED_ARTIFACT_COLLECTION="devflow_staging_verified_artifacts",
            IMPLEMENTATION_ARTIFACT_COLLECTION="",
            RUNTIME_HEALTH_COLLECTION="devflow_staging_runtime_health",
        ), patch("main.create_v3_queue_service") as create_runtime:
            self.assertIsNone(main.create_v3_queue_from_environment())

        create_runtime.assert_not_called()
