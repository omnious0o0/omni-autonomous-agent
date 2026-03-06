#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
PWSH_BASE_IMAGE="${OMNI_PWSH_BASE_IMAGE:-mcr.microsoft.com/powershell:latest}"
PYTHON_BASE_IMAGE="${OMNI_PWSH_PYTHON_BASE_IMAGE:-python:3.12-slim}"
SMOKE_IMAGE_TAG="${OMNI_PWSH_SMOKE_IMAGE_TAG:-oaa-pwsh-python-smoke:latest}"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf "pwsh-install-smoke failed: missing required command '%s'\n" "$cmd" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd timeout

if ! docker info >/dev/null 2>&1; then
  printf "pwsh-install-smoke failed: docker daemon is not reachable\n" >&2
  exit 1
fi

cat > "${BUILD_DIR}/Dockerfile" <<EOF
FROM ${PWSH_BASE_IMAGE} AS pwsh
FROM ${PYTHON_BASE_IMAGE}
COPY --from=pwsh /opt/microsoft/powershell/7 /opt/microsoft/powershell/7
RUN ln -s /opt/microsoft/powershell/7/pwsh /usr/local/bin/pwsh
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
EOF

timeout 600 docker build -t "${SMOKE_IMAGE_TAG}" "${BUILD_DIR}" >/dev/null

timeout 600 docker run --rm -v "${ROOT_DIR}:/repo:ro" "${SMOKE_IMAGE_TAG}" bash -lc '
  set -euo pipefail

  WORK_DIR="$(mktemp -d)"
  trap '"'"'rm -rf "${WORK_DIR}"'"'"' EXIT

  export HOME="${WORK_DIR}/home"
  export OMNI_AGENT_CONFIG_DIR="${WORK_DIR}/config"
  export OMNI_AGENT_SANDBOX_ROOT="${WORK_DIR}/sandbox"
  export OMNI_AGENT_REPO_ROOT="/repo"
  export OMNI_AGENT_DISABLE_AUTO_UPDATE=1
  export PYTHONDONTWRITEBYTECODE=1
  export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"
  export OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin"
  export OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install-root"

  mkdir -p "${HOME}" "${WORK_DIR}/bin" "${WORK_DIR}/fakebin"
  export PATH="${WORK_DIR}/fakebin:${PATH}"

  write_fake_binary() {
    local name="$1"
    cat > "${WORK_DIR}/fakebin/${name}" <<'"'"'BIN'"'"'
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
    cat > "${WORK_DIR}/fakebin/openclaw" <<'"'"'BIN'"'"'
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

  for cmd in gemini codex opencode futureagent; do
    write_fake_binary "${cmd}"
  done
  write_openclaw_binary

  cat > "${WORK_DIR}/parse-windows-smoke.ps1" <<\PS1
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  "/repo/tests/windows_smoke.ps1",
  [ref]$null,
  [ref]$errors
) | Out-Null
if ($errors -and $errors.Count -gt 0) {
  throw (($errors | ForEach-Object { $_.Message }) -join "`n")
}
PS1
  pwsh -NoLogo -NoProfile -File "${WORK_DIR}/parse-windows-smoke.ps1" >/dev/null

  pwsh -NoLogo -NoProfile -File /repo/.omni-autonomous-agent/install.ps1 >"${WORK_DIR}/install.out"

  test -f "${WORK_DIR}/bin/omni-autonomous-agent.ps1"
  test -f "${WORK_DIR}/bin/omni-autonomous-agent.cmd"
  test -f "${HOME}/.gemini/settings.json"
  test -f "${HOME}/.config/opencode/plugins/omni-hook.ts"
  test -f "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
  test -f "${HOME}/.local/bin/omni-wrap-codex"
  test -f "${HOME}/.local/bin/omni-agent-wrap"

  pwsh -NoLogo -NoProfile -File "${WORK_DIR}/bin/omni-autonomous-agent.ps1" --status >"${WORK_DIR}/status.out"
  grep -q "No active session" "${WORK_DIR}/status.out"

  AGENT=futureagent pwsh -NoLogo -NoProfile -File "${WORK_DIR}/bin/omni-autonomous-agent.ps1" --bootstrap >/dev/null
  test -f "${HOME}/.local/bin/omni-wrap-futureagent"

  pwsh -NoLogo -NoProfile -File "${WORK_DIR}/bin/omni-autonomous-agent.ps1" --add -R "pwsh smoke" -D dynamic >/dev/null
  set +e
  pwsh -NoLogo -NoProfile -File "${WORK_DIR}/bin/omni-autonomous-agent.ps1" --hook-stop >"${WORK_DIR}/hook-stop.out"
  hook_code=$?
  set -e
  test "${hook_code}" -eq 2
  grep -q '"'"'"template_id": "stop-blocked"'"'"' "${WORK_DIR}/hook-stop.out"

  printf "pwsh-install-smoke passed\n"
'
