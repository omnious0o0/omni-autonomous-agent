#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
CHECK_TIMEOUT="${OMNI_CHECK_TIMEOUT:-120}"

run_cli_smoke() {
  local cmd="$1"
  local out_file="$2"
  local code=0

  set +e
  timeout "${CHECK_TIMEOUT}" "${cmd}" --version >"${out_file}" 2>&1
  code=$?
  set -e

  if [[ "${code}" -eq 0 ]]; then
    return 0
  fi
  if [[ "${code}" -eq 124 ]]; then
    printf "native-agent-check failed: '%s --version' timed out after %ss\n" "${cmd}" "${CHECK_TIMEOUT}" >&2
    exit 1
  fi

  set +e
  timeout "${CHECK_TIMEOUT}" "${cmd}" --help >"${out_file}" 2>&1
  code=$?
  set -e

  if [[ "${code}" -eq 0 ]]; then
    return 0
  fi
  if [[ "${code}" -eq 124 ]]; then
    printf "native-agent-check failed: '%s --help' timed out after %ss\n" "${cmd}" "${CHECK_TIMEOUT}" >&2
    exit 1
  fi

  printf "native-agent-check failed: '%s' smoke check failed (--version and --help were non-zero)\n" "${cmd}" >&2
  exit 1
}

for cmd in codex gemini openclaw python3 timeout; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf "native-agent-check failed: missing required command '%s'\n" "$cmd" >&2
    exit 1
  fi
done

HAS_CLAUDE=0
if command -v claude >/dev/null 2>&1; then
  HAS_CLAUDE=1
fi

HAS_OPENCODE=0
if command -v opencode >/dev/null 2>&1; then
  HAS_OPENCODE=1
fi
export HAS_CLAUDE HAS_OPENCODE

OPTIONAL_WRAPPER_CANDIDATES=(aider goose plandex amp crush kiro roo cline)
OPTIONAL_WRAPPERS=()

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

mkdir -p "${HOME}" "${WORK_DIR}/bin"
export PATH="${WORK_DIR}/bin:${PATH}"

OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin" OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install" bash "${ROOT_DIR}/.omni-autonomous-agent/install.sh"

run_cli_smoke codex "${WORK_DIR}/smoke-codex.out"
run_cli_smoke gemini "${WORK_DIR}/smoke-gemini.out"
run_cli_smoke openclaw "${WORK_DIR}/smoke-openclaw.out"

for optional in "${OPTIONAL_WRAPPER_CANDIDATES[@]}"; do
  if command -v "${optional}" >/dev/null 2>&1; then
    run_cli_smoke "${optional}" "${WORK_DIR}/smoke-${optional}.out"
    OPTIONAL_WRAPPERS+=("omni-wrap-${optional}")
  fi
done

if command -v opencode >/dev/null 2>&1; then
  run_cli_smoke opencode "${WORK_DIR}/smoke-opencode.out"
fi

CLI="${WORK_DIR}/bin/omni-autonomous-agent"
WRAP_DIR="${HOME}/.local/bin"

"${CLI}" --status | grep -q "No active session"
"${CLI}" --bootstrap >"${WORK_DIR}/bootstrap.out"

python3 - <<'PY'
import json
import os
from pathlib import Path

home = Path(os.environ['HOME'])
has_claude = os.environ.get('HAS_CLAUDE') == '1'
has_opencode = os.environ.get('HAS_OPENCODE') == '1'

claude_hooks = {}
if has_claude:
    claude_path = home / '.claude' / 'settings.json'
    if not claude_path.exists():
        raise SystemExit('native-agent-check failed: missing ~/.claude/settings.json')

    claude_data = json.loads(claude_path.read_text(encoding='utf-8'))
    claude_hooks = claude_data.get('hooks', {}) if isinstance(claude_data, dict) else {}

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

if has_claude and not has(claude_hooks.get('Stop', []), 'omni-autonomous-agent --hook-stop'):
    raise SystemExit('native-agent-check failed: claude stop hook missing')

if has_claude and not has(claude_hooks.get('PreCompact', []), 'omni-autonomous-agent --hook-precompact'):
    raise SystemExit('native-agent-check failed: claude precompact hook missing')

if not has(hooks.get('AfterAgent', []), 'omni-autonomous-agent --hook-stop'):
    raise SystemExit('native-agent-check failed: gemini stop hook missing')

if not has(hooks.get('PreCompress', []), 'omni-autonomous-agent --hook-precompact'):
    raise SystemExit('native-agent-check failed: gemini precompact hook missing')

if has_opencode:
    opencode_plugin = home / '.config' / 'opencode' / 'plugins' / 'omni-hook.ts'
    if not opencode_plugin.exists():
        raise SystemExit('native-agent-check failed: missing OpenCode plugin omni-hook.ts')

    plugin_text = opencode_plugin.read_text(encoding='utf-8')
    if 'runHook(["--hook-stop"]);' not in plugin_text:
        raise SystemExit('native-agent-check failed: OpenCode stop hook missing in plugin')
    if 'runHook(["--hook-precompact"]);' not in plugin_text:
        raise SystemExit('native-agent-check failed: OpenCode precompact hook missing in plugin')
PY

test -f "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
test -f "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_BIN' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OAA_BIN' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_AGENT_ID' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_INCLUDE_SENSITIVE_CONTEXT' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_WAKE_DEDUPE_MS' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_WAKE_DELIVER' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_HOOK_TELEMETRY' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_SESSION_KEY' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_SESSION_ID' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'OMNI_AGENT_OPENCLAW_CANCEL_ALLOWED_SENDERS' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--log-event" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--event" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--note" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--cancel-accept" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--cancel-deny" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "CANCEL_ACCEPT_TOKENS" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "CANCEL_DENY_TOKENS" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "senderAuthorizedForCancelDecision" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "cancel_decision_unauthorized" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "status.cancel_request_state === 'pending'" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q '.npm-global' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "'--session-id', route.sessionId" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--deliver" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--reply-channel" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--reply-to" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q -- "--reply-account" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "openclaw-startup-wake.json" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "acquireDedupeLock" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "startup wake skipped: unresolved session route" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "startup wake skipped: unable to read OAA status" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "startup wake skipped: duplicate restart event" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "startup wake skipped: dedupe lock unavailable" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'Request: \[redacted\]' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'startup wake queued for agent=' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q 'failed to launch startup wake ping' "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"

for wrapper in omni-wrap-codex omni-agent-wrap; do
  test -f "${WRAP_DIR}/${wrapper}"
done

for wrapper in "${OPTIONAL_WRAPPERS[@]}"; do
  test -f "${WRAP_DIR}/${wrapper}"
done

for wrapper in omni-wrap-codex omni-agent-wrap; do
  set +e
  "${WRAP_DIR}/${wrapper}" --version >/dev/null 2>&1
  code=$?
  set -e
  test "${code}" -eq 3
done

for wrapper in "${OPTIONAL_WRAPPERS[@]}"; do
  set +e
  "${WRAP_DIR}/${wrapper}" --version >/dev/null 2>&1
  code=$?
  set -e
  test "${code}" -eq 3
done

"${CLI}" --add -R "native e2e" -D dynamic >"${WORK_DIR}/native-add.out"

set +e
"${CLI}" --hook-stop >"${WORK_DIR}/hook-stop-blocked.out"
hook_stop_code=$?
set -e
test "${hook_stop_code}" -eq 2
grep -q '"template_id": "stop-blocked"' "${WORK_DIR}/hook-stop-blocked.out"

set +e
timeout 2 "${WRAP_DIR}/omni-wrap-codex" --version >"${WORK_DIR}/loop.out" 2>&1
loop_code=$?
set -e
test "${loop_code}" -eq 124

set +e
timeout 2 "${WRAP_DIR}/omni-agent-wrap" gemini --version >"${WORK_DIR}/loop-gemini.out" 2>&1
gemini_loop_code=$?
set -e
test "${gemini_loop_code}" -eq 124

"${CLI}" --await-user -Q "Need constraints confirmation" >"${WORK_DIR}/await-user.out"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ['WORK_DIR'])
lines = [
    line
    for line in (work_dir / 'await-user.out').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
if payload.get('hook') != 'await-user':
    raise SystemExit('native-agent-check failed: await-user hook payload missing')
if not bool(payload.get('waiting_for_user')):
    raise SystemExit('native-agent-check failed: await-user waiting_for_user should be true')
if payload.get('wait_minutes') != 2:
    raise SystemExit('native-agent-check failed: await-user default wait should be 2 minutes')
PY

set +e
"${WRAP_DIR}/omni-agent-wrap" codex --version >"${WORK_DIR}/await-wrapper.out" 2>&1
await_wrap_code=$?
set -e
test "${await_wrap_code}" -eq 4
grep -q '"waiting_for_user": true' "${WORK_DIR}/await-wrapper.out"

"${CLI}" --user-responded --response-note "User replied in native check" >"${WORK_DIR}/user-responded.out"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ['WORK_DIR'])
lines = [
    line
    for line in (work_dir / 'user-responded.out').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
if payload.get('hook') != 'user-responded':
    raise SystemExit('native-agent-check failed: user-responded hook payload missing')
if not bool(payload.get('user_response_registered')):
    raise SystemExit('native-agent-check failed: user-responded should register response')
if bool(payload.get('waiting_for_user')):
    raise SystemExit('native-agent-check failed: waiting_for_user should be false after user-responded')
PY

"${CLI}" --hook-precompact >"${WORK_DIR}/precompact.out"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ['WORK_DIR'])
lines = [
    line
    for line in (work_dir / 'precompact.out').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
if payload.get('template_id') != 'precompact-handoff':
    raise SystemExit('native-agent-check failed: precompact template_id mismatch')
if bool(payload.get('continue')):
    raise SystemExit('native-agent-check failed: precompact should not request continue=true')

state = json.loads(Path(os.environ['OMNI_AGENT_CONFIG_DIR'], 'state.json').read_text(encoding='utf-8'))
report = Path(state['sandbox_dir']) / 'REPORT.md'
if 'Checkpoint (precompact)' not in report.read_text(encoding='utf-8'):
    raise SystemExit('native-agent-check failed: precompact checkpoint missing from REPORT.md')
PY

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

"${CLI}" --cancel >"${WORK_DIR}/native-cancel.out"
"${CLI}" --status | grep -q "No active session"

set +e
timeout "${CHECK_TIMEOUT}" openclaw hooks list >"${WORK_DIR}/native-hooks.out" 2>"${WORK_DIR}/native-hooks.err"
hooks_code=$?
set -e
if [[ "${hooks_code}" -eq 124 ]]; then
  printf "native-agent-check failed: openclaw hooks list timed out after %ss\n" "${CHECK_TIMEOUT}" >&2
  exit 1
fi
if [[ "${hooks_code}" -ne 0 ]]; then
  printf "native-agent-check failed: openclaw hooks list returned %s\n" "${hooks_code}" >&2
  exit 1
fi
grep -q "omni-recovery" "${WORK_DIR}/native-hooks.out"
if ! grep -q "session-memory" "${WORK_DIR}/native-hooks.out"; then
  printf "native-agent-check note: session-memory not available; continuing with omni-recovery only\n" >&2
fi

set +e
timeout "${CHECK_TIMEOUT}" openclaw hooks info omni-recovery >"${WORK_DIR}/native-hook-info.out" 2>"${WORK_DIR}/native-hook-info.err"
hook_info_code=$?
set -e
if [[ "${hook_info_code}" -eq 124 ]]; then
  printf "native-agent-check failed: openclaw hooks info timed out after %ss\n" "${CHECK_TIMEOUT}" >&2
  exit 1
fi
if [[ "${hook_info_code}" -ne 0 ]]; then
  printf "native-agent-check failed: openclaw hooks info returned %s\n" "${hook_info_code}" >&2
  exit 1
fi
grep -q "gateway:startup" "${WORK_DIR}/native-hook-info.out"
grep -q "message:received" "${WORK_DIR}/native-hook-info.out"
grep -q "message:transcribed" "${WORK_DIR}/native-hook-info.out"
grep -q "message:preprocessed" "${WORK_DIR}/native-hook-info.out"
grep -q "session:compact:before" "${WORK_DIR}/native-hook-info.out"

set +e
timeout "${CHECK_TIMEOUT}" openclaw hooks check >"${WORK_DIR}/native-hooks-check.out" 2>"${WORK_DIR}/native-hooks-check.err"
hooks_check_code=$?
set -e
if [[ "${hooks_check_code}" -eq 124 ]]; then
  printf "native-agent-check failed: openclaw hooks check timed out after %ss\n" "${CHECK_TIMEOUT}" >&2
  exit 1
fi
if [[ "${hooks_check_code}" -ne 0 ]]; then
  printf "native-agent-check failed: openclaw hooks check returned %s\n" "${hooks_check_code}" >&2
  exit 1
fi

printf "native-agent-check passed\n"
