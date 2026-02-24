#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT
export WORK_DIR

export HOME="${WORK_DIR}/home"
export OMNI_AGENT_CONFIG_DIR="${WORK_DIR}/config"
export OMNI_AGENT_SANDBOX_ROOT="${WORK_DIR}/sandbox"
export OMNI_AGENT_REPO_ROOT="${ROOT_DIR}"
export OMNI_AGENT_DISABLE_AUTO_UPDATE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"

mkdir -p "$HOME" "${WORK_DIR}/fakebin" "${WORK_DIR}/bin"
export PATH="${WORK_DIR}/fakebin:$PATH"

for cmd in gemini codex openclaw plandex aider goose opencode; do
  cat > "${WORK_DIR}/fakebin/$cmd" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--exit-code" ]]; then
  exit "${2:-0}"
fi
exit 0
EOF
  chmod +x "${WORK_DIR}/fakebin/$cmd"
done

OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin" OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install" bash "${ROOT_DIR}/.omni-autonomous-agent/install.sh"

CLI="${WORK_DIR}/bin/omni-autonomous-agent"
export PATH="${WORK_DIR}/bin:$PATH"

"$CLI" --bootstrap

test -f "$HOME/.gemini/settings.json"
test -f "$HOME/.openclaw/hooks/omni-recovery/HOOK.md"
test -f "$HOME/.openclaw/hooks/omni-recovery/handler.ts"
test -f "$HOME/.local/bin/omni-wrap-codex"
test -f "$HOME/.local/bin/omni-wrap-plandex"

set +e
"$HOME/.local/bin/omni-wrap-codex" --exit-code 7
WRAP_CODE=$?
set -e
test "$WRAP_CODE" -eq 3

AWAIT_ADD_OUTPUT="$($CLI --add -R "docker await user" -D dynamic)"
printf "%s\n" "$AWAIT_ADD_OUTPUT" | grep -q "omni-autonomous-agent - active"

AWAIT_OUTPUT="$($CLI --await-user -Q "Need confirmation")"
printf "%s\n" "$AWAIT_OUTPUT" > "${WORK_DIR}/await-user-output.txt"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ['WORK_DIR'])
lines = [
    line
    for line in (work_dir / 'await-user-output.txt').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
assert payload['hook'] == 'await-user'
assert payload['wait_minutes'] == 2
assert payload['waiting_for_user'] is True
PY

RESPONDED_OUTPUT="$($CLI --user-responded --response-note "user answered")"
printf "%s\n" "$RESPONDED_OUTPUT" > "${WORK_DIR}/user-responded-output.txt"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ['WORK_DIR'])
lines = [
    line
    for line in (work_dir / 'user-responded-output.txt').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
assert payload['hook'] == 'user-responded'
assert payload['user_response_registered'] is True
assert payload['waiting_for_user'] is False
PY

"$CLI" --cancel
"$CLI" --cancel-accept --decision-note "docker cleanup"

AGENT="futureagent" "$CLI" --bootstrap
test -f "$HOME/.local/bin/omni-wrap-futureagent"

OMNI_AGENT_EXTRA_WRAPPERS="soonagent" "$CLI" --bootstrap
test -f "$HOME/.local/bin/omni-wrap-soonagent"

UNSAFE_OUTPUT="$(OMNI_AGENT_EXTRA_WRAPPERS='bad;token' "$CLI" --bootstrap)"
printf "%s\n" "$UNSAFE_OUTPUT" | grep -q "Skipped unsafe wrapper command token"
test ! -f "$HOME/.local/bin/omni-wrap-bad-token"

FIXED_OUTPUT="$($CLI --add -R "docker fixed" -D 1)"
printf "%s\n" "$FIXED_OUTPUT" | grep -q "omni-autonomous-agent - active"

set +e
"$CLI" --hook-stop > "${WORK_DIR}/fixed-hook-stop-output.txt"
FIXED_HOOK_CODE=$?
set -e
test "$FIXED_HOOK_CODE" -eq 2

"$CLI" --cancel
"$CLI" --cancel-accept --decision-note "docker fixed cleanup"

ADD_OUTPUT="$($CLI --add -R "docker verification")"
printf "%s\n" "$ADD_OUTPUT" | grep -q "omni-autonomous-agent - active"

python3 - <<'PY'
import json
import os
from pathlib import Path

state = json.loads(
    Path(os.environ['OMNI_AGENT_CONFIG_DIR'], 'state.json').read_text(encoding='utf-8')
)
assert state['duration_mode'] == 'dynamic'
PY

set +e
"$CLI" --hook-stop > "${WORK_DIR}/hook-stop-output.txt"
HOOK_CODE=$?
set -e
test "$HOOK_CODE" -eq 2

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ['WORK_DIR'])
lines = [
    line
    for line in (work_dir / 'hook-stop-output.txt').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
assert payload['continue'] is True
assert payload['block'] is True
PY

set +e
timeout 1 "$HOME/.local/bin/omni-wrap-codex" --exit-code 0
LOOP_CODE=$?
set -e
test "$LOOP_CODE" -eq 124

python3 - <<'PY'
import json
import os
from pathlib import Path

state = json.loads(
    Path(os.environ['OMNI_AGENT_CONFIG_DIR'], 'state.json').read_text(encoding='utf-8')
)
report = Path(state['sandbox_dir']) / 'REPORT.md'
text = report.read_text(encoding='utf-8')
report.write_text(text.replace('IN_PROGRESS', 'COMPLETE', 1), encoding='utf-8')
PY

set +e
"$HOME/.local/bin/omni-wrap-codex" --exit-code 7
FINAL_WRAP_CODE=$?
set -e
test "$FINAL_WRAP_CODE" -eq 7

"$CLI" --status > "${WORK_DIR}/post-final-status.txt"
grep -q "No active session" "${WORK_DIR}/post-final-status.txt"

echo "{bad-json" > "${OMNI_AGENT_CONFIG_DIR}/state.json"

set +e
"$CLI" --require-active > "${WORK_DIR}/require-active-invalid.txt"
REQUIRE_CODE=$?
set -e
test "$REQUIRE_CODE" -ne 0

"$CLI" --cancel
ls "${OMNI_AGENT_CONFIG_DIR}"/state.invalid.*.json >/dev/null

"$CLI" --status > "${WORK_DIR}/hook-stop-final.txt"

python3 - <<'PY'
import os
from pathlib import Path

archived_root = Path(os.environ['OMNI_AGENT_SANDBOX_ROOT']) / 'archived'
latest = sorted(archived_root.iterdir())[-1]
report = latest / 'REPORT.md'
text = report.read_text(encoding='utf-8')
assert '**🕐 Completed at:** in progress' not in text
assert '**⏱️ Duration:** <actual time worked>' not in text
assert '### 🚦 Status' in text
PY

test ! -f "${OMNI_AGENT_CONFIG_DIR}/state.json"
test -d "${OMNI_AGENT_SANDBOX_ROOT}/archived"
test -n "$(ls -A "${OMNI_AGENT_SANDBOX_ROOT}/archived")"

"$CLI" --status
