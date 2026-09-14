"""Durable expiry enforcement for human approval boundaries."""

from __future__ import annotations

from datetime import datetime, timezone

from execution_platform import ExecutionPlatform


class ApprovalExpiryService:
    """Expire stale approvals without coupling task admission to monitoring."""

    def __init__(self, tasks, *, clock=None):
        self.tasks = tasks
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def sweep(self):
        now = self.clock()
        outcomes = []
        for task in self.tasks.awaiting_human_approval():
            if not ExecutionPlatform.expire_approval_existing(task, now):
                continue
            try:
                # The store save is optimistic.  A concurrent approval wins
                # only if it was committed before this expiry transition.
                self.tasks.save(task)
                outcomes.append((task.task_id, "APPROVAL_EXPIRED", "deadline"))
            except Exception:
                # A transient persistence error must not block unrelated
                # approvals or task execution; the next sweep retries it.
                outcomes.append((task.task_id, "APPROVAL_EXPIRY_RETRY_PENDING", "persistence"))
        return outcomes
