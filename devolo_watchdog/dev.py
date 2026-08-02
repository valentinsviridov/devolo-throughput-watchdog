"""Development utilities for running tests and linters via pyproject scripts."""

from __future__ import annotations

import subprocess
import sys


def run_command(cmd: list[str]) -> int:
    """Run a development command with the active Python interpreter."""
    return subprocess.call([sys.executable, "-m", *cmd])


def test() -> None:
    """Run the test suite with configured branch coverage."""
    sys.exit(run_command(["pytest"]))


def lint() -> None:
    """Run ruff linter, formatting checks, and mypy type check."""
    print("=== Running Ruff Linter ===")
    if lint_code := run_command(["ruff", "check", "."]):
        sys.exit(lint_code)

    print("\n=== Running Ruff Format Check ===")
    if fmt_code := run_command(["ruff", "format", "--check", "."]):
        sys.exit(fmt_code)

    print("\n=== Running Mypy Type Check ===")
    if type_code := run_command(["mypy", "devolo_watchdog"]):
        sys.exit(type_code)


def check() -> None:
    """Run linting, formatting check, type check, and unit tests."""
    lint()

    print("\n=== Running Tests with Coverage ===")
    sys.exit(run_command(["pytest"]))


def reformat() -> None:
    """Apply ruff code formatting and auto-fixable lint rules."""
    print("=== Running Ruff Fixes ===")
    run_command(["ruff", "check", "--fix", "."])

    print("\n=== Running Ruff Formatter ===")
    sys.exit(run_command(["ruff", "format", "."]))
