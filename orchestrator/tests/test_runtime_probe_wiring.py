import unittest

from v3_runtime import _runtime_checks


class RuntimeProbeWiringTest(unittest.TestCase):
    def test_control_plane_probe_is_deferred_and_callable(self):
        calls = []
        tasks = type("Tasks", (), {"readiness_check": lambda _: calls.append("checked")})()

        checks = _runtime_checks(tasks, lambda: True, lambda: True)

        self.assertEqual(calls, [])
        self.assertTrue(checks["control_plane"]())
        self.assertEqual(calls, ["checked"])
