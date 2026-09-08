"""Create a draft PR from one stored, verified implementation artifact."""

from execution_platform import ExecutionPlatform


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
                artifact = self._artifacts.get(record.execution_id).artifact
                url = self._publisher_for_task(task).publish(
                    task_id=task.task_id, execution_id=record.execution_id,
                    base_commit=artifact.base_commit, diff=artifact.diff,
                    title=f"Luvira: {task.spec.requested_action}",
                    body="Generated from an approved task. Draft PR; human review and merge remain required.",
                )
                ExecutionPlatform.publish_existing(task, record.execution_id)
                self._transaction.record_publication(task, record)
                outcomes.append((task.task_id, "PUBLISHED", url))
            except Exception:
                # A PR may have been created before a transport interruption.
                # Do not retry automatically and risk creating a second branch.
                outcomes.append((task.task_id, "PUBLICATION_REQUIRES_RECONCILIATION", record.execution_id))
        return outcomes
