#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
WORK_DIR="$(mktemp -d)"
COPY_DIR="${WORK_DIR}/release"
trap 'rm -rf "${WORK_DIR}"' EXIT

mkdir -p "${COPY_DIR}"

if ! git -C "${ROOT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf "launch-gate-clean failed: %s is not a git worktree\n" "${ROOT_DIR}" >&2
  exit 1
fi

git -C "${ROOT_DIR}" archive --format=tar HEAD | tar -xf - -C "${COPY_DIR}"

mkdir -p "${COPY_DIR}/omni-sandbox/archived"
touch "${COPY_DIR}/omni-sandbox/archived/.gitkeep"
touch "${COPY_DIR}/TASK.md"

cd "${COPY_DIR}"
bash tests/launch_gate.sh
printf "launch-gate-clean passed\n"
