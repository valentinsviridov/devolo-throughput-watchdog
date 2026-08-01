"""Development utilities for running tests and linters via pyproject scripts."""

import subprocess
import sys


def _run(cmd: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", *cmd])


def test() -> None:
    """Run unit test suite."""
    sys.exit(_run(["unittest", "-v", "test_devolo_watchdog.py"]))


def lint() -> None:
    """Run ruff linter."""
    sys.exit(_run(["ruff", "check", "."]))


def check() -> None:
    """Run linting and unit tests."""
    print("=== Running Linter ===")
    if lint_code := _run(["ruff", "check", "."]):
        sys.exit(lint_code)

    print("\n=== Running Unit Tests ===")
    sys.exit(_run(["unittest", "-v", "test_devolo_watchdog.py"]))
