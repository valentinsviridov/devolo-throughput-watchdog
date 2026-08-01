from __future__ import annotations

import unittest

# Re-export test runner for legacy executions pointing directly to test_devolo_watchdog.py
if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("tests")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
