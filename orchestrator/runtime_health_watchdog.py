"""Persistent, read-only runtime health sampling for the autonomous broker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class RuntimeHealthWatchdog:
    """Samples external execution dependencies without admitting or mutating tasks.

    Healthy probes are cached durably to avoid spending provider quota on every
    two-minute broker sweep.  A failed sample is retried on the next sweep.
    """

    def __init__(self, store: Any, checks: dict[str, Callable[[], bool]],
                 interval: timedelta = timedelta(minutes=15), clock: Callable[[], datetime] | None = None):
        self._store, self._checks, self._interval = store, checks, interval
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def sweep(self) -> tuple[str, str]:
        now = self._clock()
        previous = self._store.current()
        if self._is_fresh_ready(previous, now):
            return "RUNTIME_HEALTH_FRESH", "cached"

        results: dict[str, str] = {}
        for name, check in self._checks.items():
            try:
                results[name] = "READY" if check() else "BLOCKED"
            except Exception:
                # Deliberately reduce unknown transport/provider failures to a
                # stable code: no credentials or provider responses are stored.
                results[name] = "UNAVAILABLE"
        status = "READY" if all(value == "READY" for value in results.values()) else "BLOCKED"
        self._store.record({"status": status, "checked_at": now, "checks": results})
        return ("RUNTIME_HEALTH_READY" if status == "READY" else "RUNTIME_HEALTH_BLOCKED", status)

    def _is_fresh_ready(self, value: dict[str, Any] | None, now: datetime) -> bool:
        if not value or value.get("status") != "READY":
            return False
        checked_at = value.get("checked_at")
        return isinstance(checked_at, datetime) and checked_at.tzinfo is not None and now - checked_at < self._interval


class FirestoreRuntimeHealthStore:
    """A separate operational aggregate; it is never part of task authority."""

    def __init__(self, client: Any, collection: str = "devflow_runtime_health"):
        self._document = client.collection(collection).document("current")

    def current(self) -> dict[str, Any] | None:
        snapshot = self._document.get()
        return snapshot.to_dict() if snapshot.exists else None

    def record(self, value: dict[str, Any]) -> None:
        self._document.set(value)
