"""Reconcile a published PR to its durable merged terminal state."""

from execution_platform import ExecutionPlatform, V3Status
from github_verified_publisher import VerifiedPublicationError


class PublicationCompletionService:
    def __init__(self, tasks, transaction, publisher_for_task):
        self._tasks, self._transaction, self._publisher_for_task = tasks, transaction, publisher_for_task

    def sweep(self):
        outcomes = []
        for task in self._tasks.published():
            record = task.execution
            if record is None:
                outcomes.append((task.task_id, "PUBLICATION_LINK_UNAVAILABLE", None)); continue
            try:
                publisher = self._publisher_for_task(task)
                record.publication_url = record.publication_url or publisher.publication_url_for(
                    task.task_id, record.execution_id,
                )
                state = publisher.pull_state(record.publication_url)
            except VerifiedPublicationError:
                outcomes.append((task.task_id, "PUBLICATION_STATE_RETRY_PENDING", record.execution_id)); continue
            if state != "MERGED":
                outcomes.append((task.task_id, "PUBLICATION_AWAITING_MERGE", record.execution_id)); continue
            try:
                ExecutionPlatform.merge_existing(task, record.execution_id)
                self._transaction.record_merge(task, record)
                outcomes.append((task.task_id, "MERGED", record.execution_id))
            except Exception:
                outcomes.append((task.task_id, "MERGE_RESULT_UNAVAILABLE", record.execution_id))
        return outcomes
