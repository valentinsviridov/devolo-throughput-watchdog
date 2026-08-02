"""Development utilities for running tests and linters via pyproject scripts."""

from __future__ import annotations

import subprocess
import sys


def _run(cmd: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", *cmd])


def test() -> None:
    """Run unit test suite using test discovery."""
    sys.exit(_run(["unittest", "discover", "-s", "tests"]))


def lint() -> None:
    """Run ruff linter and formatting checks."""
    print("=== Running Ruff Linter ===")
    if lint_code := _run(["ruff", "check", "."]):
        sys.exit(lint_code)

    print("\n=== Running Ruff Format Check ===")
    if fmt_code := _run(["ruff", "format", "--check", "."]):
        sys.exit(fmt_code)


def check() -> None:
    """Run linting, formatting check, and unit tests."""
    lint()
    print("\n=== Running Unit Tests ===")
    sys.exit(_run(["unittest", "discover", "-s", "tests"]))


def reformat() -> None:
    """Apply ruff code formatting and auto-fixable lint rules."""
    print("=== Running Ruff Fixes ===")
    _run(["ruff", "check", "--fix", "."])

    print("\n=== Running Ruff Formatter ===")
    sys.exit(_run(["ruff", "format", "."]))
