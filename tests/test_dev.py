from __future__ import annotations

import sys
import unittest
from unittest.mock import call, patch

from devolo_watchdog import dev


class DevelopmentCommandTests(unittest.TestCase):
    @patch("devolo_watchdog.dev.subprocess.call", return_value=7)
    def test_run_uses_current_python_interpreter(self, mock_call):
        self.assertEqual(dev._run(["pytest"]), 7)
        mock_call.assert_called_once_with([sys.executable, "-m", "pytest"])

    @patch("devolo_watchdog.dev._run", return_value=0)
    def test_test_command_runs_pytest(self, mock_run):
        with self.assertRaisesRegex(SystemExit, "0"):
            dev.test()
        mock_run.assert_called_once_with(["pytest"])

    @patch("devolo_watchdog.dev._run", return_value=0)
    def test_lint_runs_all_static_checks(self, mock_run):
        dev.lint()
        self.assertEqual(
            mock_run.call_args_list,
            [
                call(["ruff", "check", "."]),
                call(["ruff", "format", "--check", "."]),
                call(["mypy", "devolo_watchdog"]),
            ],
        )

    @patch("devolo_watchdog.dev._run", return_value=2)
    def test_lint_stops_on_first_failure(self, mock_run):
        with self.assertRaisesRegex(SystemExit, "2"):
            dev.lint()
        mock_run.assert_called_once_with(["ruff", "check", "."])

    @patch("devolo_watchdog.dev._run", return_value=0)
    @patch("devolo_watchdog.dev.lint")
    def test_check_runs_lint_then_pytest(self, mock_lint, mock_run):
        with self.assertRaisesRegex(SystemExit, "0"):
            dev.check()
        mock_lint.assert_called_once_with()
        mock_run.assert_called_once_with(["pytest"])

    @patch("devolo_watchdog.dev._run", side_effect=[0, 0])
    def test_reformat_applies_lint_fixes_then_formatter(self, mock_run):
        with self.assertRaisesRegex(SystemExit, "0"):
            dev.reformat()
        self.assertEqual(
            mock_run.call_args_list,
            [call(["ruff", "check", "--fix", "."]), call(["ruff", "format", "."])],
        )
