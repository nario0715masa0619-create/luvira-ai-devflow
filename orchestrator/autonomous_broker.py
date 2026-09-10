"""Autonomous admission loop: approval never depends on broker availability."""
from execution_platform import ExecutionPlatformError, V3Status


class AutonomousBroker:
    def __init__(self, tasks, queue, dispatcher=None, reconciler=None, implementation=None, publication=None, completion=None):
        self.tasks, self.queue, self.dispatcher, self.reconciler, self.implementation, self.publication, self.completion = tasks, queue, dispatcher, reconciler, implementation, publication, completion

    def sweep(self):
        """Attempt each eligible persisted task; retain approval on failure."""
        outcomes = []
        if self.reconciler is not None:
            outcomes.extend(self.reconciler.sweep())
        if self.implementation is not None:
            outcomes.extend(self.implementation.sweep())
        if self.publication is not None:
            outcomes.extend(self.publication.sweep())
        if self.completion is not None:
            outcomes.extend(self.completion.sweep())
        for task in self.tasks.eligible():
            if task.status not in {V3Status.AUTHORIZED, V3Status.EXECUTION_FAILED_RETRYABLE}:
                continue
            try:
                record, _ = self.queue.request(task.task_id)
                if self.dispatcher is None:
                    outcomes.append((task.task_id, "QUEUED", record.execution_id))
                    continue
                worker_execution_id = self.dispatcher.dispatch(task.task_id, record.execution_id)
                outcomes.append((task.task_id, "WORKER_STARTED", worker_execution_id))
            except Exception as exc:
                # A broker failure is recorded/alerted by its runner; critically,
                # this method never revokes the durable human authorization.
                # Domain errors contain only stable public codes.  Unexpected
                # exceptions are reduced to their type so no sensitive detail
                # can reach scheduler logs.
                detail = str(exc) if isinstance(exc, ExecutionPlatformError) else type(exc).__name__
                outcomes.append((task.task_id, "RETRY_PENDING", detail))
        return outcomes
