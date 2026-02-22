#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "${ROOT_DIR}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="/tmp/omni-pycache"

OMNI_AGENT_EXTRA_WRAPPERS="codex,soonagent" python3 "main.py" --bootstrap >/tmp/omni-host-bootstrap.txt 2>&1

python3 - <<'PY'
import json
import subprocess
from pathlib import Path

home = Path.home()

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

if not has(hooks.get('AfterAgent', []), 'omni-autonomous-agent --hook-stop'):
    raise SystemExit('host-agent-check failed: gemini stop hook missing')

if not has(hooks.get('PreCompress', []), 'omni-autonomous-agent --hook-precompact'):
    raise SystemExit('host-agent-check failed: gemini precompact hook missing')

openclaw_hook_md = home / '.openclaw' / 'hooks' / 'omni-recovery' / 'HOOK.md'
openclaw_handler = home / '.openclaw' / 'hooks' / 'omni-recovery' / 'handler.ts'

if not openclaw_hook_md.exists() or not openclaw_handler.exists():
    raise SystemExit('host-agent-check failed: openclaw omni-recovery hook files missing')

for wrapper_name in ['omni-wrap-codex', 'omni-wrap-soonagent']:
    wrapper = home / '.local' / 'bin' / wrapper_name
    if not wrapper.exists():
        raise SystemExit(f'host-agent-check failed: missing wrapper {wrapper}')

wrapper = home / '.local' / 'bin' / 'omni-wrap-codex'
res = subprocess.run([str(wrapper), '--exit-code', '0'], capture_output=True, text=True, check=False)
if res.returncode != 3:
    raise SystemExit(f'host-agent-check failed: codex wrapper preflight expected 3 got {res.returncode}')
PY

if command -v openclaw >/dev/null 2>&1; then
  hooks_output="$(openclaw hooks list)"
  printf "%s\n" "${hooks_output}" | grep -q "omni-recovery"
  printf "%s\n" "${hooks_output}" | grep -q "session-memory"
fi

printf "host-agent-check passed\n"
