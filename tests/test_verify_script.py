from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify.sh"


class VerificationScriptTests(unittest.TestCase):
    def test_help_describes_optional_checks(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("--skip-docker", result.stdout)
        self.assertIn("--keep-artifacts", result.stdout)

    def test_unknown_option_fails_before_running_checks(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--unknown"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option: --unknown", result.stderr)
