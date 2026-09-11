from datetime import datetime, timedelta, timezone
import unittest

from runtime_health_watchdog import RuntimeHealthWatchdog


class Store:
    def __init__(self, current=None):
        self.value = current
        self.writes = []

    def current(self):
        return self.value

    def record(self, value):
        self.value = value
        self.writes.append(value)


class RuntimeHealthWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 11, tzinfo=timezone.utc)

    def test_records_only_stable_health_codes(self):
        store = Store()
        watchdog = RuntimeHealthWatchdog(
            store, {"store": lambda: True, "provider": lambda: (_ for _ in ()).throw(RuntimeError("secret"))},
            clock=lambda: self.now,
        )

        self.assertEqual(watchdog.sweep(), ("RUNTIME_HEALTH_BLOCKED", "BLOCKED"))
        self.assertEqual(store.writes, [{"status": "BLOCKED", "checked_at": self.now,
                                         "checks": {"store": "READY", "provider": "UNAVAILABLE"}}])

    def test_healthy_record_is_cached_but_a_failure_is_not(self):
        store = Store({"status": "READY", "checked_at": self.now - timedelta(minutes=5), "checks": {}})
        called = []
        watchdog = RuntimeHealthWatchdog(store, {"provider": lambda: called.append(True) or True}, clock=lambda: self.now)

        self.assertEqual(watchdog.sweep(), ("RUNTIME_HEALTH_FRESH", "cached"))
        self.assertEqual(called, [])

        store.value = {"status": "BLOCKED", "checked_at": self.now, "checks": {}}
        self.assertEqual(watchdog.sweep(), ("RUNTIME_HEALTH_READY", "READY"))
        self.assertEqual(called, [True])
