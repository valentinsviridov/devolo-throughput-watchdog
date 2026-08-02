#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
skip_docker=false
keep_artifacts=false
current_stage="initialization"

usage() {
    cat <<'EOF'
Usage: scripts/verify.sh [OPTIONS]

Run the complete local verification checklist.

Options:
  --skip-docker      Skip Docker Compose validation and image build.
  --keep-artifacts   Copy the verified wheel and source archive to dist/.
  -h, --help         Show this help message.
EOF
}

fail() {
    printf 'error: %s\n' "$1" >&2
    exit 2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

stage() {
    current_stage="$1"
    printf '\n==> %s\n' "$current_stage"
}

on_error() {
    local exit_code=$?
    printf '\nFAILED during: %s (exit %d)\n' "$current_stage" "$exit_code" >&2
    exit "$exit_code"
}

while (($# > 0)); do
    case "$1" in
        --skip-docker)
            skip_docker=true
            ;;
        --keep-artifacts)
            keep_artifacts=true
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            fail "unknown option: $1"
            ;;
    esac
    shift
done

require_command uv
if [[ "$skip_docker" == false ]]; then
    require_command docker
fi

verification_dir="$(mktemp -d "${TMPDIR:-/tmp}/devolo-watchdog-verify.XXXXXX")"
cleanup() {
    if [[ -n "${verification_dir:-}" && -d "$verification_dir" ]]; then
        rm -rf -- "$verification_dir"
    fi
}
trap cleanup EXIT
trap on_error ERR

cd "$project_root"
quality_log="${verification_dir}/dev-check.log"
build_dir="${verification_dir}/dist"

stage "Quality gate (Ruff, formatting, complexity, mypy, tests, coverage)"
uv run dev-check 2>&1 | tee "$quality_log"

test_count="$(grep -Eo '[0-9]+ passed' "$quality_log" | tail -n 1 | cut -d' ' -f1 || true)"
coverage_percent="$(uv run coverage report --format=total --precision=2)"

stage "Lockfile consistency"
uv lock --check

stage "Wheel and source distribution build"
mkdir -p "$build_dir"
uv build --out-dir "$build_dir"

shopt -s nullglob
wheel_files=("$build_dir"/*.whl)
sdist_files=("$build_dir"/*.tar.gz)
shopt -u nullglob

if ((${#wheel_files[@]} != 1)); then
    fail "expected exactly one wheel, found ${#wheel_files[@]}"
fi
if ((${#sdist_files[@]} != 1)); then
    fail "expected exactly one source archive, found ${#sdist_files[@]}"
fi

wheel_path="${wheel_files[0]}"
sdist_path="${sdist_files[0]}"

stage "License embedded in wheel"
license_entry="$({
    uv run python - "$wheel_path" "$project_root/LICENSE" <<'PY'
from pathlib import Path
import sys
from zipfile import ZipFile

wheel_path = Path(sys.argv[1])
source_license = Path(sys.argv[2]).read_bytes()

with ZipFile(wheel_path) as wheel:
    matches = [
        name
        for name in wheel.namelist()
        if ".dist-info/licenses/" in name and Path(name).name == "LICENSE"
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one embedded LICENSE, found {len(matches)}")
    if wheel.read(matches[0]) != source_license:
        raise SystemExit("embedded LICENSE differs from the repository LICENSE")

print(matches[0])
PY
} 2>&1)"
printf 'Verified %s\n' "$license_entry"

artifact_summary="built and verified in a temporary directory"
if [[ "$keep_artifacts" == true ]]; then
    mkdir -p "$project_root/dist"
    cp -- "$wheel_path" "$sdist_path" "$project_root/dist/"
    artifact_summary="built, verified, and copied to dist/"
fi

if [[ "$skip_docker" == false ]]; then
    stage "Docker Compose configuration"
    docker compose config --quiet

    stage "Docker Compose image build"
    docker compose build
fi

if [[ -n "$test_count" ]]; then
    test_summary="${test_count} passed"
else
    test_summary="passed (count unavailable)"
fi

printf '\nVerification passed\n'
printf '  - uv run dev-check: passed\n'
printf '  - Ruff formatting/linting: passed\n'
printf '  - Mypy: passed\n'
printf '  - Complexity ceiling: passed\n'
printf '  - Tests: %s\n' "$test_summary"
printf '  - Branch coverage: %s%%\n' "$coverage_percent"
printf '  - uv lock --check: passed\n'
printf '  - Wheel and source distribution: %s\n' "$artifact_summary"
printf '  - License embedded in wheel: verified\n'
if [[ "$skip_docker" == false ]]; then
    printf '  - docker compose config --quiet: passed\n'
    printf '  - docker compose build: passed\n'
else
    printf '  - Docker checks: skipped (--skip-docker)\n'
fi
