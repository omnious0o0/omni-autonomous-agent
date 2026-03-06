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
  ".omni-autonomous-agent/templates/stop-blocked-fixed.md"
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
  ".github/workflows/verify.yml"
  "tests/test_autonomous_agent.py"
  "tests/test_cross_platform_logic.py"
  "tests/docker_smoke.sh"
  "tests/macos_smoke.sh"
  "tests/pwsh_install_smoke.sh"
  "tests/native_agent_check.sh"
  "tests/host_agent_check.sh"
  "tests/launch_gate.sh"
  "tests/launch_gate_clean.sh"
  "tests/windows_smoke.ps1"
  ".omni-autonomous-agent/install.sh"
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
    root / ".omni-autonomous-agent" / "templates" / "stop-blocked-fixed.md",
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
    root / "tests" / "macos_smoke.sh",
    root / "tests" / "pwsh_install_smoke.sh",
    root / "tests" / "host_agent_check.sh",
    root / "tests" / "windows_smoke.ps1",
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
    '--cancel-accept',
    '--cancel-deny',
    '--hook-stop',
    '--hook-precompact',
    '--bootstrap',
    '--require-active',
    '--update',
    '--install',
    '--await-user',
    '--user-responded',
    '--log-event',
    '-R',
    '-D',
    '--event',
    '--note',
    '--wait-minutes',
    '--response-note',
    '--decision-note',
]

missing = [flag for flag in required_flags if flag not in help_result.stdout]
if missing:
    raise SystemExit('launch-gate failed: CLI help missing flags: ' + ', '.join(missing))

install_help = (root / 'install-help.md').read_text(encoding='utf-8')
if "trap 'omni-autonomous-agent --hook-stop' EXIT" in install_help:
    raise SystemExit('launch-gate failed: install-help.md contains outdated trap-only wrapper guidance')

required_install_help_markers = [
    '## 4C) Future-agent fallback (must stay generic)',
    '## 10) AI self-setup playbook (non-scripted fallback)',
    '## 10B) Provider coverage rule',
    '## 10C) Cross-OS proof rule',
    '## 10D) Preferred repo-native verification ladder',
    '## 11) Official references and troubleshooting resources',
    'does **not** require every command to be prefixed',
    'Verification grades',
    'live-verified',
    'simulated coverage only',
    'quarantined or replaced safely',
    'Do not fail a generic wrapper-based setup just because `openclaw` is absent',
    'PYTHONDONTWRITEBYTECODE=1 python3 -m unittest',
    'tests/launch_gate_clean.sh',
    'tests/pwsh_install_smoke.sh',
    'tests/native_agent_check.sh',
    'tests/host_agent_check.sh',
    'tests/macos_smoke.sh',
    'tests/windows_smoke.ps1',
    'https://docs.openclaw.ai/automation/hooks',
    'https://docs.openclaw.ai/automation/hooks#troubleshooting',
    'https://geminicli.com/docs/get-started/authentication/',
    'https://geminicli.com/docs/hooks/',
    'https://code.claude.com/docs/en/hooks',
    'https://opencode.ai/docs/plugins/',
    'https://developers.openai.com/api/docs/guides/tools-shell',
    'https://developers.openai.com/api/docs/mcp',
]
missing_markers = [marker for marker in required_install_help_markers if marker not in install_help]
if missing_markers:
    raise SystemExit(
        'launch-gate failed: install-help.md missing required self-setup markers: '
        + ', '.join(missing_markers)
    )

readme_text = (root / 'README.md').read_text(encoding='utf-8')
required_readme_markers = [
    'Work overnight',
    'Work on this for 2 hours',
    "Keep working on this until it's done",
    'Do chores until I stop you',
    "There's a memory system",
    '2 minutes',
    'install-help.md',
    'canonical hook setup playbook',
]
missing_readme_markers = [
    marker for marker in required_readme_markers if marker not in readme_text
]
if missing_readme_markers:
    raise SystemExit(
        'launch-gate failed: README.md missing required autonomous contract markers: '
        + ', '.join(missing_readme_markers)
    )

skill_text = (root / 'SKILL.md').read_text(encoding='utf-8')
required_skill_markers = [
    'Work overnight',
    'Work on this for 2 hours',
    "Keep working on this until it's done",
    'Do chores until I stop you',
    'memory system',
    'install-help.md',
    'Normal operation must not rely on manual git or GitHub commands.',
    'Normal work commands remain normal commands.',
    '`oaa <command>` alias',
    'Use wrappers only for the **agent process**',
    '2 minutes',
    '--await-user',
    '--user-responded',
    'configured',
    'callable',
    'authenticated',
    'live-verified',
    'Compaction is not completion.',
]
missing_skill_markers = [
    marker for marker in required_skill_markers if marker not in skill_text
]
if missing_skill_markers:
    raise SystemExit(
        'launch-gate failed: SKILL.md missing required autonomy contract markers: '
        + ', '.join(missing_skill_markers)
    )

gitignore_text = (root / '.gitignore').read_text(encoding='utf-8')
required_gitignore_markers = [
    'TASK.md',
    'omni-sandbox/*',
    '!omni-sandbox/archived/',
    'omni-sandbox/archived/*',
    '!omni-sandbox/archived/.gitkeep',
]
missing_gitignore_markers = [
    marker for marker in required_gitignore_markers if marker not in gitignore_text
]
if missing_gitignore_markers:
    raise SystemExit(
        'launch-gate failed: .gitignore missing required task/archive markers: '
        + ', '.join(missing_gitignore_markers)
    )

docker_smoke = (root / 'tests' / 'docker_smoke.sh').read_text(encoding='utf-8')
required_docker_markers = [
    'docker run',
    'curl -fsSL',
    'OMNI_DOCKER_SMOKE_INSTALLER_HOST',
    '--network host',
    'OMNI_DOCKER_SMOKE_IMAGES',
    'ubuntu:24.04',
    'debian:12-slim',
    'alpine:3.20',
    'session:compact:before',
]
missing_docker_markers = [
    marker for marker in required_docker_markers if marker not in docker_smoke
]
if missing_docker_markers:
    raise SystemExit(
        'launch-gate failed: docker_smoke.sh is missing required Docker verification markers: '
        + ', '.join(missing_docker_markers)
    )

pwsh_smoke = (root / 'tests' / 'pwsh_install_smoke.sh').read_text(encoding='utf-8')
required_pwsh_markers = [
    'mcr.microsoft.com/powershell:latest',
    'install.ps1',
    'omni-autonomous-agent.ps1',
    'futureagent',
    'pwsh-install-smoke passed',
]
missing_pwsh_markers = [
    marker for marker in required_pwsh_markers if marker not in pwsh_smoke
]
if missing_pwsh_markers:
    raise SystemExit(
        'launch-gate failed: pwsh_install_smoke.sh is missing required PowerShell verification markers: '
        + ', '.join(missing_pwsh_markers)
    )

windows_smoke = (root / 'tests' / 'windows_smoke.ps1').read_text(encoding='utf-8')
required_windows_markers = [
    'windows-smoke passed',
    'omni-autonomous-agent.ps1',
    'futureagent',
    'omni-wrap-codex.cmd',
]
missing_windows_markers = [
    marker for marker in required_windows_markers if marker not in windows_smoke
]
if missing_windows_markers:
    raise SystemExit(
        'launch-gate failed: windows_smoke.ps1 is missing required Windows verification markers: '
        + ', '.join(missing_windows_markers)
    )

macos_smoke = (root / 'tests' / 'macos_smoke.sh').read_text(encoding='utf-8')
required_macos_markers = [
    'macos-smoke passed',
    'futureagent',
    'omni-wrap-codex',
    'omni-agent-wrap',
]
missing_macos_markers = [
    marker for marker in required_macos_markers if marker not in macos_smoke
]
if missing_macos_markers:
    raise SystemExit(
        'launch-gate failed: macos_smoke.sh is missing required macOS verification markers: '
        + ', '.join(missing_macos_markers)
    )

workflow = (root / '.github' / 'workflows' / 'verify.yml').read_text(encoding='utf-8')
required_workflow_markers = [
    'ubuntu-latest',
    'windows-latest',
    'macos-latest',
    'tests.test_cross_platform_logic',
    'tests.test_autonomous_agent',
    'tests/launch_gate_clean.sh',
    'tests/host_agent_check.sh',
    'tests/docker_smoke.sh',
    'tests/pwsh_install_smoke.sh',
    'tests/windows_smoke.ps1',
    'tests/macos_smoke.sh',
]
missing_workflow_markers = [
    marker for marker in required_workflow_markers if marker not in workflow
]
if missing_workflow_markers:
    raise SystemExit(
        'launch-gate failed: verify workflow is missing required cross-platform markers: '
        + ', '.join(missing_workflow_markers)
    )
PY

printf "launch-gate passed\n"
