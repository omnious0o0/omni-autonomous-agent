#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "${ROOT_DIR}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"

required_paths=(
  ".omni-autonomous-agent"
  ".omni-autonomous-agent/templates"
  ".omni-autonomous-agent/templates/stop-blocked.md"
  ".omni-autonomous-agent/templates/precompact-handoff.md"
  ".omni-autonomous-agent/templates/user-timeout-continue.md"
  "omni-sandbox"
  "omni-sandbox/archived"
  "main.py"
  "SKILL.md"
  "README.md"
  "TASK.md"
  "LICENSE"
  "install-help.md"
  ".gitignore"
  "tests/test_autonomous_agent.py"
  "tests/docker_smoke.sh"
  "tests/native_agent_check.sh"
  "tests/host_agent_check.sh"
  "tests/launch_gate.sh"
  ".omni-autonomous-agent/install.ps1"
)

for path in "${required_paths[@]}"; do
  if [[ ! -e "${path}" ]]; then
    printf "launch-gate failed: missing required path %s\n" "${path}" >&2
    exit 1
  fi
done

if [[ -n "$(find . -type d -name '__pycache__' -print -quit)" ]]; then
  printf "launch-gate failed: __pycache__ directories must not exist\n" >&2
  exit 1
fi

if [[ -d ".ruff_cache" ]]; then
  printf "launch-gate failed: .ruff_cache must not exist\n" >&2
  exit 1
fi

non_gitkeep_items="$(find "omni-sandbox/archived" -mindepth 1 -not -name '.gitkeep' -print -quit)"
if [[ -n "${non_gitkeep_items}" ]]; then
  printf "launch-gate failed: omni-sandbox/archived must be empty before launch\n" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
import re

root = Path(".").resolve()
files = [
    root / "main.py",
    root / "README.md",
    root / "SKILL.md",
    root / "TASK.md",
    root / "LICENSE",
    root / "install-help.md",
    root / ".omni-autonomous-agent" / "templates" / "stop-blocked.md",
    root / ".omni-autonomous-agent" / "templates" / "precompact-handoff.md",
    root / ".omni-autonomous-agent" / "templates" / "user-timeout-continue.md",
    root / ".omni-autonomous-agent" / "constants.py",
    root / ".omni-autonomous-agent" / "cli.py",
    root / ".omni-autonomous-agent" / "session_manager.py",
    root / ".omni-autonomous-agent" / "bootstrap.py",
    root / ".omni-autonomous-agent" / "updater.py",
    root / ".omni-autonomous-agent" / "install.sh",
    root / ".omni-autonomous-agent" / "install.ps1",
    root / "tests" / "test_autonomous_agent.py",
    root / "tests" / "docker_smoke.sh",
    root / "tests" / "host_agent_check.sh",
]

patterns = [
    re.compile(r"/home/[A-Za-z0-9._-]+"),
    re.compile(r"C:\\\\Users\\\\"),
    re.compile(r"/Users/[A-Za-z0-9._-]+"),
]

violations: list[str] = []
for file_path in files:
    text = file_path.read_text(encoding="utf-8")
    for pattern in patterns:
        if pattern.search(text):
            violations.append(f"{file_path}: matched {pattern.pattern}")

if violations:
    raise SystemExit("launch-gate failed: machine-specific paths found\n" + "\n".join(violations))
PY

python3 - <<'PY'
import subprocess
import sys
from pathlib import Path

root = Path('.').resolve()
help_result = subprocess.run(
    [sys.executable, str(root / 'main.py'), '--help'],
    cwd=root,
    check=False,
    capture_output=True,
    text=True,
)

if help_result.returncode != 0:
    raise SystemExit('launch-gate failed: main.py --help returned non-zero')

required_flags = [
    '--add',
    '--status',
    '--cancel',
    '--hook-stop',
    '--hook-precompact',
    '--bootstrap',
    '--require-active',
    '--update',
    '--install',
    '--await-user',
    '--user-responded',
    '-R',
    '-D',
    '--wait-minutes',
    '--response-note',
]

missing = [flag for flag in required_flags if flag not in help_result.stdout]
if missing:
    raise SystemExit('launch-gate failed: CLI help missing flags: ' + ', '.join(missing))

install_help = (root / 'install-help.md').read_text(encoding='utf-8')
if "trap 'omni-autonomous-agent --hook-stop' EXIT" in install_help:
    raise SystemExit('launch-gate failed: install-help.md contains outdated trap-only wrapper guidance')

required_install_help_markers = [
    '## 10) AI self-setup playbook (non-scripted fallback)',
    '## 11) Official references and troubleshooting resources',
    'https://docs.openclaw.ai/automation/hooks',
    'https://docs.openclaw.ai/automation/hooks#troubleshooting',
    'https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html',
    'https://code.claude.com/docs/en/hooks',
]
missing_markers = [marker for marker in required_install_help_markers if marker not in install_help]
if missing_markers:
    raise SystemExit(
        'launch-gate failed: install-help.md missing required self-setup markers: '
        + ', '.join(missing_markers)
    )
PY

printf "launch-gate passed\n"
