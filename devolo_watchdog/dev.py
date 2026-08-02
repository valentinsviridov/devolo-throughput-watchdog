"""Development utilities for running tests and linters via pyproject scripts."""

from __future__ import annotations

import subprocess
import sys


def _run(cmd: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", *cmd])


def test() -> None:
    """Run the test suite with configured branch coverage."""
    sys.exit(_run(["pytest"]))


def lint() -> None:
    """Run ruff linter, formatting checks, and mypy type check."""
    print("=== Running Ruff Linter ===")
    if lint_code := _run(["ruff", "check", "."]):
        sys.exit(lint_code)

    print("\n=== Running Ruff Format Check ===")
    if fmt_code := _run(["ruff", "format", "--check", "."]):
        sys.exit(fmt_code)

    print("\n=== Running Mypy Type Check ===")
    if type_code := _run(["mypy", "devolo_watchdog"]):
        sys.exit(type_code)


def check() -> None:
    """Run linting, formatting check, type check, and unit tests."""
    lint()

    print("\n=== Running Tests with Coverage ===")
    sys.exit(_run(["pytest"]))


def reformat() -> None:
    """Apply ruff code formatting and auto-fixable lint rules."""
    print("=== Running Ruff Fixes ===")
    _run(["ruff", "check", "--fix", "."])

    print("\n=== Running Ruff Formatter ===")
    sys.exit(_run(["ruff", "format", "."]))
