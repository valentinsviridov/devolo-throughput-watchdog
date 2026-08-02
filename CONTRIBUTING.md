# Contributing

## Development setup

The project supports Python 3.11 and 3.12 and uses `uv` to keep development and CI environments aligned.

```bash
uv sync --locked
uv run dev-check
```

`dev-check` runs Ruff linting and formatting checks, mypy, and the pytest suite with branch coverage. Coverage below 80% fails the test command.

## Where changes belong

- `config.py`: environment parsing, defaults, and validation.
- `probes.py`: side-effecting adapters for ping, iperf3, and the devolo API.
- `models.py`: data passed between probes, policy, and persistence.
- `policy.py`: pure classification and state-transition rules.
- `runner.py`: scheduling, persistence, action orchestration, and signals.
- `actions.py`: hardware-changing operations.
- `state.py`: atomic state and heartbeat storage.
- `__main__.py`: CLI parsing and diagnostic commands.
- `core.py` and `network.py`: compatibility entry points; new runtime behavior should use the modules above.

Keep policy decisions pure where possible. Hardware and subprocess access should stay behind adapters so tests can replace them without network or device access.

## Safety invariants

Changes must preserve these rules:

1. `run --once` never reboots hardware unless `--allow-action` is present.
2. Low WAN throughput cannot trigger a reboot without PLC evidence unless the operator explicitly sets `DW_REQUIRE_PLC_EVIDENCE_FOR_REBOOT=false`.
3. A reboot attempt is recorded before the management API is called. If a configured state file cannot be written, the action is skipped.
4. Every management API call—accepted, rejected, or failed—counts toward the moving-window limit.
5. Invalid configuration must fail closed with exit code 3.

Add a regression test whenever one of these rules or an external command/API adapter changes.

## Before submitting a change

```bash
./scripts/verify.sh
```

This runs the quality gate, checks the lockfile, builds fresh package artifacts, verifies the
embedded license, validates the Compose configuration, and builds the container image. Use
`--skip-docker` when Docker is unavailable and `--keep-artifacts` when the built wheel and source
archive should remain in `dist/`.

Never use a production adapter or `DW_ACTION=reboot` for automated tests.

Update `README.md` and `devolo-throughput-watchdog.env.example` whenever a setting, default, CLI flag, exit code, or operational behavior changes.
