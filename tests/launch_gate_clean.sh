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

python3 - "${ROOT_DIR}" "${COPY_DIR}" <<'PY'
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
copy_dir = Path(sys.argv[2]).resolve()

paths = subprocess.check_output(
    [
        "git",
        "-C",
        str(root),
        "ls-files",
        "--cached",
        "--modified",
        "--deduplicate",
        "-z",
    ]
)

for raw_rel in paths.decode("utf-8").split("\0"):
    if not raw_rel:
        continue
    rel = Path(raw_rel)
    source = root / rel
    target = copy_dir / rel
    if not source.exists():
        raise SystemExit(
            f"launch-gate-clean failed: tracked path missing from working tree: {raw_rel}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if target.exists() or target.is_symlink():
            target.unlink()
        os.symlink(os.readlink(source), target)
        continue
    shutil.copy2(source, target)
PY

mkdir -p "${COPY_DIR}/omni-sandbox/archived"
touch "${COPY_DIR}/omni-sandbox/archived/.gitkeep"
touch "${COPY_DIR}/TASK.md"

cd "${COPY_DIR}"
bash tests/launch_gate.sh
printf "launch-gate-clean passed\n"
