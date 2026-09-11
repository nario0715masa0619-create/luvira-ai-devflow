"""Create a draft PR from one stored, verified implementation artifact."""

from execution_platform import ExecutionPlatform, V3Status
from github_verified_publisher import VerifiedPublicationError


FINAL_PUBLICATION_CODES = frozenset({
    "publication_base_content_missing",
    "publication_base_content_already_exists",
    "publication_base_content_invalid",
    "publication_patch_context_mismatch",
    "publication_patch_offset_invalid",
    "publication_patch_hunk_invalid",
    "publication_patch_hunk_required",
    "publication_patch_header_invalid",
    "publication_patch_path_invalid",
    "publication_patch_not_utf8",
})

PUBLICATION_TEXT = {
    "implementation": (
        "Luvira: 実装成果物",
        "承認済みタスクから生成・検証された実装成果物です。独立レビューと人による確認後にマージしてください。",
    ),
    "validation": (
        "Luvira: 検証結果",
        "承認済みタスクから生成・検証された結果です。人による確認後にマージしてください。",
    ),
}


def publication_text(requested_action: str) -> tuple[str, str]:
    """Keep generated PR metadata human-readable in the repository language."""
    return PUBLICATION_TEXT.get(
        requested_action,
        ("Luvira: 承認済み成果物", "承認済みタスクから生成・検証された成果物です。人による確認後にマージしてください。"),
    )


class VerifiedPublicationService:
    def __init__(self, tasks, transaction, artifacts, publisher_for_task):
        self._tasks, self._transaction = tasks, transaction
        self._artifacts, self._publisher_for_task = artifacts, publisher_for_task

    def sweep(self):
        outcomes = []
        for task in self._tasks.artifact_verified():
            record = task.execution
            if record is None:
                outcomes.append((task.task_id, "PUBLICATION_STATE_INVALID", None)); continue
            try:
                publisher = self._publisher_for_task(task)
                # Publication is recover-first.  A transport loss after
                # GitHub created a PR must converge on that PR, never create
                # another branch on each scheduler sweep.
                lookup = getattr(publisher, "publication_url_for", None)
                url = lookup(task.task_id, record.execution_id) if callable(lookup) else None
                if url is None:
                    artifact = self._artifacts.get(record.execution_id).artifact
                    title, body = publication_text(task.spec.requested_action)
                    url = publisher.publish(
                        task_id=task.task_id, execution_id=record.execution_id,
                        base_commit=artifact.base_commit, diff=artifact.diff,
                        title=title, body=body,
                    )
                ExecutionPlatform.publish_existing(task, record.execution_id, url)
                self._transaction.record_publication(task, record)
                outcomes.append((task.task_id, "PUBLISHED", url))
            except VerifiedPublicationError as exc:
                # These errors prove that the verified artifact cannot be
                # applied to its immutable base.  They are not transport
                # outages and must become a durable terminal result instead
                # of generating an alert every broker sweep.
                if str(exc) in FINAL_PUBLICATION_CODES:
                    ExecutionPlatform.fail_existing(
                        task, record.execution_id, f"{str(exc).upper()}_FINAL",
                    )
                    self._transaction.record_result(
                        task, record, V3Status.EXECUTION_FAILED_FINAL,
                    )
                    outcomes.append((task.task_id, "PUBLICATION_ARTIFACT_INVALID_FINAL", record.execution_id))
                    continue
                code = str(exc).upper()
                ExecutionPlatform.note_publication_recovery(task, record.execution_id, code)
                self._transaction.record_publication_recovery(task, record)
                outcomes.append((task.task_id, code, record.execution_id))
            except Exception:
                # A PR may have been created before a transport interruption.
                # Do not retry automatically and risk creating a second branch.
                code = "PUBLICATION_UNAVAILABLE_RETRYABLE"
                ExecutionPlatform.note_publication_recovery(task, record.execution_id, code)
                self._transaction.record_publication_recovery(task, record)
                outcomes.append((task.task_id, code, record.execution_id))
        return outcomes
