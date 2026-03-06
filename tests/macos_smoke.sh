#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

for cmd in bash python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf "macos-smoke failed: missing required command '%s'\n" "$cmd" >&2
    exit 1
  fi
done

write_fake_binary() {
  local name="$1"
  cat > "${WORK_DIR}/fakebin/${name}" <<'BIN'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--exit-code" ]]; then
  exit "${2:-0}"
fi
if [[ "${1:-}" == "--version" ]]; then
  printf "0.0.0-test\n"
  exit 0
fi
exit 0
BIN
  chmod +x "${WORK_DIR}/fakebin/${name}"
}

write_openclaw_binary() {
  cat > "${WORK_DIR}/fakebin/openclaw" <<'BIN'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf "2026.3.2-test\n"
  exit 0
fi
if [[ "${1:-}" == "hooks" && "${2:-}" == "enable" ]]; then
  exit 0
fi
if [[ "${1:-}" == "hooks" && "${2:-}" == "check" ]]; then
  printf "omni-recovery ok\n"
  printf "session-memory ok\n"
  exit 0
fi
if [[ "${1:-}" == "hooks" && "${2:-}" == "list" ]]; then
  printf "omni-recovery enabled\n"
  printf "session-memory enabled\n"
  exit 0
fi
if [[ "${1:-}" == "hooks" && "${2:-}" == "info" && "${3:-}" == "omni-recovery" ]]; then
  printf "name: omni-recovery\n"
  printf "events:\n"
  printf "  - gateway:startup\n"
  printf "  - message:received\n"
  printf "  - message:transcribed\n"
  printf "  - message:preprocessed\n"
  printf "  - session:compact:before\n"
  exit 0
fi
exit 0
BIN
  chmod +x "${WORK_DIR}/fakebin/openclaw"
}

export HOME="${WORK_DIR}/home"
export OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin"
export OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install-root"
export OMNI_AGENT_CONFIG_DIR="${WORK_DIR}/config"
export OMNI_AGENT_SANDBOX_ROOT="${WORK_DIR}/sandbox"
export OMNI_AGENT_REPO_ROOT="${ROOT_DIR}"
export OMNI_AGENT_DISABLE_AUTO_UPDATE=1
export OMNI_AGENT_WRAPPER_BIN="${WORK_DIR}/wrappers"
export OMNI_AGENT_OPENCODE_PLUGIN="${WORK_DIR}/opencode/plugins/omni-hook.ts"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"

mkdir -p "${HOME}" "${WORK_DIR}/bin" "${WORK_DIR}/fakebin" "${WORK_DIR}/wrappers"
export PATH="${WORK_DIR}/fakebin:${PATH}"

for cmd in codex gemini opencode futureagent; do
  write_fake_binary "${cmd}"
done
write_openclaw_binary

bash "${ROOT_DIR}/.omni-autonomous-agent/install.sh" >/dev/null

CLI="${WORK_DIR}/bin/omni-autonomous-agent"
test -f "${CLI}"
test -f "${HOME}/.gemini/settings.json"
test -f "${WORK_DIR}/opencode/plugins/omni-hook.ts"
test -f "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
test -f "${WORK_DIR}/wrappers/omni-wrap-codex"
test -f "${WORK_DIR}/wrappers/omni-agent-wrap"

"${CLI}" --status >"${WORK_DIR}/status.out"
grep -q "No active session" "${WORK_DIR}/status.out"

set +e
"${WORK_DIR}/wrappers/omni-wrap-codex" --version >/dev/null 2>&1
wrapper_code=$?
set -e
test "${wrapper_code}" -eq 3

AGENT=futureagent "${CLI}" --bootstrap >/dev/null
test -f "${WORK_DIR}/wrappers/omni-wrap-futureagent"

"${CLI}" --add -R "macos smoke" -D dynamic >/dev/null
set +e
"${CLI}" --hook-stop >"${WORK_DIR}/hook-stop.out"
hook_code=$?
set -e
test "${hook_code}" -eq 2
grep -q '"template_id": "stop-blocked"' "${WORK_DIR}/hook-stop.out"

printf "macos-smoke passed\n"
