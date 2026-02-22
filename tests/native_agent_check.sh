#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"

for cmd in codex gemini openclaw python3 timeout; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf "native-agent-check failed: missing required command '%s'\n" "$cmd" >&2
    exit 1
  fi
done

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

export HOME="${WORK_DIR}/home"
export OMNI_AGENT_CONFIG_DIR="${WORK_DIR}/config"
export OMNI_AGENT_SANDBOX_ROOT="${WORK_DIR}/sandbox"
export OMNI_AGENT_REPO_ROOT="${ROOT_DIR}"
export OMNI_AGENT_DISABLE_AUTO_UPDATE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"

mkdir -p "${HOME}" "${WORK_DIR}/bin"
export PATH="${WORK_DIR}/bin:${PATH}"

OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin" OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install" bash "${ROOT_DIR}/.omni-autonomous-agent/install.sh"

CLI="${WORK_DIR}/bin/omni-autonomous-agent"
WRAP_DIR="${HOME}/.local/bin"

"${CLI}" --status | grep -q "No active session"
"${CLI}" --bootstrap >"${WORK_DIR}/bootstrap.out"

python3 - <<'PY'
import json
import os
from pathlib import Path

home = Path(os.environ['HOME'])

gemini_path = home / '.gemini' / 'settings.json'
if not gemini_path.exists():
    raise SystemExit('native-agent-check failed: missing ~/.gemini/settings.json')

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
    raise SystemExit('native-agent-check failed: gemini stop hook missing')

if not has(hooks.get('PreCompress', []), 'omni-autonomous-agent --hook-precompact'):
    raise SystemExit('native-agent-check failed: gemini precompact hook missing')
PY

test -f "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
test -f "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"

for wrapper in omni-wrap-codex omni-agent-wrap; do
  test -f "${WRAP_DIR}/${wrapper}"
done

for wrapper in omni-wrap-codex omni-agent-wrap; do
  set +e
  "${WRAP_DIR}/${wrapper}" --version >/dev/null 2>&1
  code=$?
  set -e
  test "${code}" -eq 3
done

"${CLI}" --add -R "native e2e" -D dynamic >/tmp/omni-native-add.out

set +e
timeout 2 "${WRAP_DIR}/omni-wrap-codex" --version >"${WORK_DIR}/loop.out" 2>&1
loop_code=$?
set -e
test "${loop_code}" -eq 124
grep -q '"template_id": "stop-blocked"' "${WORK_DIR}/loop.out"

python3 - <<'PY'
import json
import os
from pathlib import Path

state = json.loads(Path(os.environ['OMNI_AGENT_CONFIG_DIR'], 'state.json').read_text(encoding='utf-8'))
report = Path(state['sandbox_dir']) / 'REPORT.md'
text = report.read_text(encoding='utf-8')
report.write_text(text.replace('IN_PROGRESS', 'COMPLETE', 1), encoding='utf-8')
PY

set +e
"${WRAP_DIR}/omni-wrap-codex" --version >/dev/null 2>&1
final_code=$?
set -e
test "${final_code}" -eq 0

"${CLI}" --status | grep -q "No active session"

echo '{bad-json' >"${OMNI_AGENT_CONFIG_DIR}/state.json"
set +e
"${CLI}" --hook-stop >"${WORK_DIR}/corrupt-stop.out"
corrupt_code=$?
set -e
test "${corrupt_code}" -eq 2
grep -q "corrupted" "${WORK_DIR}/corrupt-stop.out"

"${CLI}" --cancel >/tmp/omni-native-cancel.out
"${CLI}" --status | grep -q "No active session"

if openclaw hooks list >/tmp/omni-native-hooks.out 2>/tmp/omni-native-hooks.err; then
  grep -q "omni-recovery" /tmp/omni-native-hooks.out
  grep -q "session-memory" /tmp/omni-native-hooks.out
fi

printf "native-agent-check passed\n"
