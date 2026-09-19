import unittest

from project_onboarding import (
    InMemoryProjectRegistry,
    NewProjectRequest,
    ProjectOnboardingError,
    ProjectOnboardingService,
    ProjectStatus,
    project_from_payload,
    project_payload,
)


class ProjectOnboardingTest(unittest.TestCase):
    def setUp(self):
        self.service = ProjectOnboardingService(InMemoryProjectRegistry())
        self.request = NewProjectRequest(
            owner="nario0715masa0619-create",
            slug="customer-portal",
            description="顧客ポータル",
        )

    def test_new_project_is_not_an_implementation_target_until_all_checks_pass(self):
        record = self.service.request(self.request)
        self.assertEqual(record.status, ProjectStatus.REQUESTED)
        with self.assertRaisesRegex(ProjectOnboardingError, "project_not_ready_for_implementation"):
            self.service.require_ready_repository(record.project_id)

        self.service.approve(record.project_id)
        self.service.begin_provisioning(record.project_id)
        ready = self.service.complete_provisioning(
            record.project_id, "nario0715masa0619-create/customer-portal", "a" * 40, True,
        )
        self.assertEqual(ready.status, ProjectStatus.READY)
        self.assertEqual(self.service.require_ready_repository(record.project_id), ready.repository)

    def test_wrong_repository_cannot_be_registered(self):
        record = self.service.request(self.request)
        self.service.approve(record.project_id)
        self.service.begin_provisioning(record.project_id)
        with self.assertRaisesRegex(ProjectOnboardingError, "project_repository_mismatch"):
            self.service.complete_provisioning(record.project_id, "owner/other-project", "a" * 40, True)

    def test_github_app_installation_is_required_before_ready(self):
        record = self.service.request(self.request)
        self.service.approve(record.project_id)
        self.service.begin_provisioning(record.project_id)
        with self.assertRaisesRegex(ProjectOnboardingError, "project_readiness_incomplete"):
            self.service.complete_provisioning(record.project_id, self.request.repository, "a" * 40, False)

    def test_invalid_slug_is_rejected_before_any_provisioning(self):
        with self.assertRaisesRegex(ProjectOnboardingError, "project_slug_invalid"):
            NewProjectRequest("nario0715masa0619-create", "Bad_Name", "x")

    def test_durable_payload_cannot_be_rebound_to_another_project_id(self):
        record = self.service.request(self.request)
        payload = project_payload(record)
        payload["project_id"] = "project-other-product"
        with self.assertRaisesRegex(ProjectOnboardingError, "project_record_identity_invalid"):
            project_from_payload(payload)
