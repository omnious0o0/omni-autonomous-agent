#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "${ROOT_DIR}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"
export OMNI_AGENT_CONFIG_DIR="${WORK_DIR}/host-config"
export OMNI_AGENT_SANDBOX_ROOT="${WORK_DIR}/host-sandbox"
export OMNI_AGENT_REPO_ROOT="${ROOT_DIR}"
export OMNI_AGENT_DISABLE_AUTO_UPDATE=1

HAS_CLAUDE=0
if command -v claude >/dev/null 2>&1; then
  HAS_CLAUDE=1
fi

HAS_OPENCODE=0
if command -v opencode >/dev/null 2>&1; then
  HAS_OPENCODE=1
fi
export HAS_CLAUDE HAS_OPENCODE

rm -rf "${OMNI_AGENT_CONFIG_DIR}" "${OMNI_AGENT_SANDBOX_ROOT}"
mkdir -p "${OMNI_AGENT_CONFIG_DIR}" "${OMNI_AGENT_SANDBOX_ROOT}"

CHECK_TIMEOUT="${OMNI_CHECK_TIMEOUT:-120}"

set +e
OMNI_AGENT_EXTRA_WRAPPERS="codex,soonagent" timeout "${CHECK_TIMEOUT}" python3 "main.py" --bootstrap >"${WORK_DIR}/host-bootstrap.txt" 2>&1
BOOTSTRAP_CODE=$?
set -e
if [[ "${BOOTSTRAP_CODE}" -eq 124 ]]; then
  printf "host-agent-check failed: bootstrap timed out after %ss\n" "${CHECK_TIMEOUT}" >&2
  exit 1
fi
if [[ "${BOOTSTRAP_CODE}" -ne 0 ]]; then
  printf "host-agent-check failed: bootstrap returned %s\n" "${BOOTSTRAP_CODE}" >&2
  exit 1
fi

python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path

home = Path.home()
has_claude = os.environ.get('HAS_CLAUDE') == '1'
has_opencode = os.environ.get('HAS_OPENCODE') == '1'

claude_hooks = {}
if has_claude:
    claude_path = home / '.claude' / 'settings.json'
    if not claude_path.exists():
        raise SystemExit('host-agent-check failed: missing ~/.claude/settings.json')

    claude_data = json.loads(claude_path.read_text(encoding='utf-8'))
    claude_hooks = claude_data.get('hooks', {}) if isinstance(claude_data, dict) else {}

gemini_path = home / '.gemini' / 'settings.json'
if not gemini_path.exists():
    raise SystemExit('host-agent-check failed: missing ~/.gemini/settings.json')

data = json.loads(gemini_path.read_text(encoding='utf-8'))
hooks = data.get('hooks', {}) if isinstance(data, dict) else {}

def has(entries, command):
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get('hooks', []):
            if isinstance(hook, dict) and hook.get('command') == command:
                return True
    return False

if has_claude and not has(claude_hooks.get('Stop', []), 'omni-autonomous-agent --hook-stop'):
    raise SystemExit('host-agent-check failed: claude stop hook missing')

if has_claude and not has(claude_hooks.get('PreCompact', []), 'omni-autonomous-agent --hook-precompact'):
    raise SystemExit('host-agent-check failed: claude precompact hook missing')

if not has(hooks.get('AfterAgent', []), 'omni-autonomous-agent --hook-stop'):
    raise SystemExit('host-agent-check failed: gemini stop hook missing')

if not has(hooks.get('PreCompress', []), 'omni-autonomous-agent --hook-precompact'):
    raise SystemExit('host-agent-check failed: gemini precompact hook missing')

if has_opencode:
    opencode_plugin = home / '.config' / 'opencode' / 'plugins' / 'omni-hook.ts'
    if not opencode_plugin.exists():
        raise SystemExit('host-agent-check failed: missing OpenCode plugin omni-hook.ts')

    plugin_text = opencode_plugin.read_text(encoding='utf-8')
    if 'runHook(["--hook-stop"]);' not in plugin_text:
        raise SystemExit('host-agent-check failed: OpenCode stop hook missing in plugin')
    if 'runHook(["--hook-precompact"]);' not in plugin_text:
        raise SystemExit('host-agent-check failed: OpenCode precompact hook missing in plugin')

openclaw_hook_md = home / '.openclaw' / 'hooks' / 'omni-recovery' / 'HOOK.md'
openclaw_handler = home / '.openclaw' / 'hooks' / 'omni-recovery' / 'handler.ts'

if not openclaw_hook_md.exists() or not openclaw_handler.exists():
    raise SystemExit('host-agent-check failed: openclaw omni-recovery hook files missing')

for wrapper_name in ['omni-wrap-codex', 'omni-wrap-soonagent']:
    wrapper = home / '.local' / 'bin' / wrapper_name
    if not wrapper.exists():
        raise SystemExit(f'host-agent-check failed: missing wrapper {wrapper}')

wrapper = home / '.local' / 'bin' / 'omni-wrap-codex'
try:
    res = subprocess.run([str(wrapper), '--exit-code', '0'], capture_output=True, text=True, check=False, timeout=30)
except subprocess.TimeoutExpired as exc:
    raise SystemExit(f'host-agent-check failed: codex wrapper preflight timed out after {exc.timeout}s')
if res.returncode != 3:
    raise SystemExit(f'host-agent-check failed: codex wrapper preflight expected 3 got {res.returncode}')
PY

if command -v openclaw >/dev/null 2>&1; then
  set +e
  hooks_output="$(timeout "${CHECK_TIMEOUT}" openclaw hooks list)"
  hooks_code=$?
  set -e
  if [[ "${hooks_code}" -eq 124 ]]; then
    printf "host-agent-check failed: openclaw hooks list timed out after %ss\n" "${CHECK_TIMEOUT}" >&2
    exit 1
  fi
  if [[ "${hooks_code}" -ne 0 ]]; then
    printf "host-agent-check failed: openclaw hooks list returned %s\n" "${hooks_code}" >&2
    exit 1
  fi
  printf "%s\n" "${hooks_output}" | grep -q "omni-recovery"
  if ! printf "%s\n" "${hooks_output}" | grep -q "session-memory"; then
    printf "host-agent-check note: session-memory not available; continuing with omni-recovery only\n" >&2
  fi
fi

printf "host-agent-check passed\n"
