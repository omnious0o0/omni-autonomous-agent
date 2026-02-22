#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"

export HOME="/tmp/omni-home"
export OMNI_AGENT_CONFIG_DIR="/tmp/omni-config"
export OMNI_AGENT_SANDBOX_ROOT="/tmp/omni-sandbox"
export OMNI_AGENT_REPO_ROOT="${ROOT_DIR}"
export OMNI_AGENT_DISABLE_AUTO_UPDATE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="/tmp/omni-pycache"

mkdir -p "$HOME" /tmp/fakebin /tmp/omni-bin
export PATH="/tmp/fakebin:$PATH"

for cmd in gemini codex openclaw plandex aider goose opencode; do
  cat > "/tmp/fakebin/$cmd" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--exit-code" ]]; then
  exit "${2:-0}"
fi
exit 0
EOF
  chmod +x "/tmp/fakebin/$cmd"
done

OMNI_AGENT_LOCAL_BIN="/tmp/omni-bin" OMNI_AGENT_INSTALL_DIR="/tmp/omni-install" bash "${ROOT_DIR}/.omni-autonomous-agent/install.sh"

CLI="/tmp/omni-bin/omni-autonomous-agent"
export PATH="/tmp/omni-bin:$PATH"

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
"$CLI" --hook-stop > /tmp/fixed-hook-stop-output.txt
FIXED_HOOK_CODE=$?
set -e
test "$FIXED_HOOK_CODE" -eq 2

"$CLI" --cancel

ADD_OUTPUT="$($CLI --add -R "docker verification")"
printf "%s\n" "$ADD_OUTPUT" | grep -q "omni-autonomous-agent - active"

python3 - <<'PY'
import json
from pathlib import Path
state = json.loads(Path('/tmp/omni-config/state.json').read_text(encoding='utf-8'))
assert state['duration_mode'] == 'dynamic'
PY

set +e
"$CLI" --hook-stop > /tmp/hook-stop-output.txt
HOOK_CODE=$?
set -e
test "$HOOK_CODE" -eq 2

python3 - <<'PY'
import json
from pathlib import Path
lines = [line for line in Path('/tmp/hook-stop-output.txt').read_text(encoding='utf-8').splitlines() if line.strip()]
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
from pathlib import Path
state = json.loads(Path('/tmp/omni-config/state.json').read_text(encoding='utf-8'))
report = Path(state['sandbox_dir']) / 'REPORT.md'
text = report.read_text(encoding='utf-8')
report.write_text(text.replace('IN_PROGRESS', 'COMPLETE', 1), encoding='utf-8')
PY

set +e
"$HOME/.local/bin/omni-wrap-codex" --exit-code 7
FINAL_WRAP_CODE=$?
set -e
test "$FINAL_WRAP_CODE" -eq 7

"$CLI" --status > /tmp/post-final-status.txt
grep -q "No active session" /tmp/post-final-status.txt

echo "{bad-json" > /tmp/omni-config/state.json

set +e
"$CLI" --require-active > /tmp/require-active-invalid.txt
REQUIRE_CODE=$?
set -e
test "$REQUIRE_CODE" -ne 0

"$CLI" --cancel
ls /tmp/omni-config/state.invalid.*.json >/dev/null

"$CLI" --status > /tmp/hook-stop-final.txt

python3 - <<'PY'
import json
from pathlib import Path
archived_root = Path('/tmp/omni-sandbox/archived')
latest = sorted(archived_root.iterdir())[-1]
report = latest / 'REPORT.md'
text = report.read_text(encoding='utf-8')
assert '**🕐 Completed at:** in progress' not in text
assert '**⏱️ Duration:** <actual time worked>' not in text
assert '### 🚦 Status' in text
PY

test ! -f /tmp/omni-config/state.json
test -d /tmp/omni-sandbox/archived
test -n "$(ls -A /tmp/omni-sandbox/archived)"

"$CLI" --status
