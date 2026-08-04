# Stable Progress Log

When performing multi-step tasks, maintain an `.agent-progress.md` file in the project root.

At the beginning of each task, add a new section. After every significant milestone, append:

* what was done;
* the observed result;
* the decision made;
* the next step.

The file must be append-only: never delete or rewrite previous entries.

Do not record hidden chain-of-thought reasoning. Record only verifiable facts, conclusions, and decisions.

# Development Tools

Read `CONTRIBUTING.md` before making changes. It describes the module layout, safety invariants, and submission checklist.

## Quick-check commands (via `uv run`)

| Command            | What it does                                                           |
| ------------------ | ---------------------------------------------------------------------- |
| `uv run dev-check` | Ruff lint + format check, mypy, pytest with branch coverage (≥ 80 %)  |
| `uv run dev-test`  | pytest only                                                            |
| `uv run dev-lint`  | Ruff lint + format check + mypy only                                   |
| `uv run dev-reformat` | Auto-fix lint violations and reformat with Ruff                    |

## Full verification gate

Run `./scripts/verify.sh` before considering any task complete. It runs `dev-check` plus lockfile validation, wheel/sdist build, license check, Compose config validation, and Docker image build. Use `--skip-docker` when Docker is unavailable.

## Key configuration (pyproject.toml)

* **Ruff**: line-length 100, target Python 3.11, selects `E F I UP B C90`, max McCabe complexity 10.
* **pytest**: test discovery in `tests/`, branch coverage via `pytest-cov`, fail-under 80 %.
* **mypy**: Python 3.11, `disallow_untyped_defs`, `warn_return_any`.

## Safety rules (from CONTRIBUTING.md)

* `run --once` never reboots without `--allow-action`.
* Low WAN throughput cannot trigger a reboot without PLC evidence (unless operator opts out).
* A reboot attempt is persisted before the API call; if persistence fails, the action is skipped.
* Every management API call counts toward the moving-window limit.
* Notification failures must never block a hardware recovery action.
* Never use a production adapter or `DW_ACTION=reboot` in automated tests.
