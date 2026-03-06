#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
WORK_DIR="$(mktemp -d)"
COPY_DIR="${WORK_DIR}/release"
trap 'rm -rf "${WORK_DIR}"' EXIT

mkdir -p "${COPY_DIR}"

rsync -a \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '.ruff_cache' \
  --exclude 'omni-sandbox/archived/***' \
  "${ROOT_DIR}/" "${COPY_DIR}/"

mkdir -p "${COPY_DIR}/omni-sandbox/archived"
touch "${COPY_DIR}/omni-sandbox/archived/.gitkeep"

cd "${COPY_DIR}"
bash tests/launch_gate.sh
printf "launch-gate-clean passed\n"
