"""Autonomous admission loop: approval never depends on broker availability."""
from execution_platform import ExecutionPlatformError, V3Status


class AutonomousBroker:
    def __init__(self, tasks, queue, dispatcher=None, reconciler=None, implementation=None, implementation_recovery=None, publication=None, completion=None, runtime_watchdog=None, approval_expiry=None):
        self.tasks, self.queue, self.dispatcher, self.reconciler, self.implementation, self.implementation_recovery, self.publication, self.completion, self.runtime_watchdog, self.approval_expiry = tasks, queue, dispatcher, reconciler, implementation, implementation_recovery, publication, completion, runtime_watchdog, approval_expiry

    def sweep(self):
        """Attempt each eligible persisted task; retain approval on failure."""
        outcomes = []
        if self.approval_expiry is not None:
            try:
                outcomes.extend(self.approval_expiry.sweep())
            except Exception:
                # Deadline enforcement is retried by the next sweep.  A
                # temporary Firestore read failure must not prevent recovery
                # of already-authorized work.
                outcomes.append(("approval_expiry", "APPROVAL_EXPIRY_UNAVAILABLE", "retry_pending"))
        if self.runtime_watchdog is not None:
            try:
                outcome, detail = self.runtime_watchdog.sweep()
                outcomes.append(("runtime", outcome, detail))
            except Exception:
                # Monitoring must never freeze recovery, publication, or an
                # unrelated approved task.  Its durable record is diagnostic,
                # not a second authority over the state machine.
                outcomes.append(("runtime", "RUNTIME_HEALTH_UNAVAILABLE", "monitor_unavailable"))
        if self.reconciler is not None:
            outcomes.extend(self.reconciler.sweep())
        if self.implementation_recovery is not None:
            outcomes.extend(self.implementation_recovery.sweep())
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
