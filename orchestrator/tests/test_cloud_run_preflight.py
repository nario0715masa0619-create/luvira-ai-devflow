import unittest

from cloud_run_preflight import (
    broker_identity_check,
    cloud_run_checkers,
    worker_job_check,
)


class CloudRunPreflightTest(unittest.TestCase):
    def test_worker_job_requires_expected_name_and_image(self):
        healthy = worker_job_check(
            lambda *_: {"name": "worker", "image": "sha256:abc"},
            "project", "us-central1", "worker",
        )
        missing_image = worker_job_check(
            lambda *_: {"name": "worker"}, "project", "us-central1", "worker"
        )

        self.assertTrue(healthy.passed)
        self.assertEqual(healthy.code, "OK")
        self.assertFalse(missing_image.passed)
        self.assertEqual(missing_image.code, "WORKER_JOB_INVALID")

    def test_worker_job_provider_failure_is_reduced_to_stable_code(self):
        def unavailable(*_):
            raise RuntimeError("raw Cloud Run response with credentials")

        result = worker_job_check(unavailable, "project", "us-central1", "worker")

        self.assertFalse(result.passed)
        self.assertEqual(result.code, "WORKER_JOB_UNAVAILABLE")

    def test_broker_identity_requires_both_minimum_roles(self):
        required_roles = {
            "roles/run.jobsExecutorWithOverrides",
            "roles/run.viewer",
        }
        healthy = broker_identity_check(
            lambda *_: required_roles,
            "project", "us-central1", "worker", "broker@example.com",
        )
        insufficient = broker_identity_check(
            lambda *_: {"roles/run.viewer"},
            "project", "us-central1", "worker", "broker@example.com",
        )

        self.assertTrue(healthy.passed)
        self.assertFalse(insufficient.passed)
        self.assertEqual(insufficient.code, "BROKER_IAM_INSUFFICIENT")

    def test_broker_identity_provider_failure_is_reduced_to_stable_code(self):
        def unavailable(*_):
            raise RuntimeError("raw IAM policy")

        result = broker_identity_check(
            unavailable, "project", "us-central1", "worker", "broker@example.com"
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.code, "BROKER_IAM_UNAVAILABLE")

    def test_factory_preserves_required_preflight_order(self):
        checkers = cloud_run_checkers(
            lambda *_: {"name": "worker", "image": "sha256:abc"},
            lambda *_: {
                "roles/run.jobsExecutorWithOverrides",
                "roles/run.viewer",
            },
            "project",
            "us-central1",
            "worker",
            "broker@example.com",
        )

        self.assertEqual([checker(None).name for checker in checkers], [
            "BROKER_IDENTITY",
            "WORKER_JOB",
        ])
