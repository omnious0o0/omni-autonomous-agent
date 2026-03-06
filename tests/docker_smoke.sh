#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
WORK_DIR="$(mktemp -d)"
HTTP_DIR="${WORK_DIR}/http"
REPO_FIXTURE_DIR="${WORK_DIR}/repo-source"
HTTP_PORT="${OMNI_DOCKER_SMOKE_HTTP_PORT:-}"
INSTALLER_HOST="${OMNI_DOCKER_SMOKE_INSTALLER_HOST:-127.0.0.1}"
DOCKER_RUN_ARGS_RAW="${OMNI_DOCKER_SMOKE_DOCKER_RUN_ARGS:---network host}"
HTTP_SERVER_PID=""
trap 'stop_installer_server; rm -rf "${WORK_DIR}"' EXIT

if [[ -z "${HTTP_PORT}" ]]; then
  HTTP_PORT="$(
    python3 - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
  )"
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf "docker-smoke failed: missing required command '%s'\n" "$cmd" >&2
    exit 1
  fi
}

ensure_docker() {
  if ! docker info >/dev/null 2>&1; then
    printf "docker-smoke failed: docker daemon is not reachable\n" >&2
    exit 1
  fi
}

start_installer_server() {
  mkdir -p "${HTTP_DIR}"
  cp "${ROOT_DIR}/.omni-autonomous-agent/install.sh" "${HTTP_DIR}/install.sh"
  (
    cd "${HTTP_DIR}"
    python3 -m http.server "${HTTP_PORT}" >/dev/null 2>&1
  ) &
  HTTP_SERVER_PID=$!

  local attempts=0
  until curl -fsS "http://127.0.0.1:${HTTP_PORT}/install.sh" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [[ "${attempts}" -ge 20 ]]; then
      printf "docker-smoke failed: local installer server did not become ready\n" >&2
      exit 1
    fi
    sleep 1
  done
}

stop_installer_server() {
  if [[ -n "${HTTP_SERVER_PID}" ]]; then
    kill "${HTTP_SERVER_PID}" >/dev/null 2>&1 || true
    wait "${HTTP_SERVER_PID}" 2>/dev/null || true
  fi
}

prepare_repo_fixture() {
  mkdir -p "${REPO_FIXTURE_DIR}"
  rsync -a \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.ruff_cache' \
    --exclude 'omni-sandbox/archived/*' \
    "${ROOT_DIR}/" "${REPO_FIXTURE_DIR}/"

  git init -q "${REPO_FIXTURE_DIR}"
  git -C "${REPO_FIXTURE_DIR}" config user.email "docker-smoke@example.invalid"
  git -C "${REPO_FIXTURE_DIR}" config user.name "docker-smoke"
  git -C "${REPO_FIXTURE_DIR}" add .
  git -C "${REPO_FIXTURE_DIR}" commit -q -m "docker smoke fixture"
}

write_container_script() {
  cat > "${WORK_DIR}/container-check.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${OMNI_TEST_IMAGE:?missing OMNI_TEST_IMAGE}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT
export WORK_DIR

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

if [[ "${1:-}" == "agent" ]]; then
  exit 0
fi

exit 0
BIN
  chmod +x "${WORK_DIR}/fakebin/openclaw"
}

assert_timeout_loop() {
  python3 - <<'PY'
import os
import subprocess
import time

wrapper = os.path.join(os.environ["HOME"], ".local", "bin", "omni-wrap-codex")
proc = subprocess.Popen(
    [wrapper, "--exit-code", "0"],
    env=os.environ.copy(),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

deadline = time.time() + 1.0
while time.time() < deadline:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
        raise SystemExit(0)
    time.sleep(0.05)

stdout, stderr = proc.communicate(timeout=2.0)
raise SystemExit(
    "docker-smoke failed: codex wrapper exited early\n"
    f"stdout={stdout}\n"
    f"stderr={stderr}\n"
    f"returncode={proc.returncode}"
)
PY
}

export HOME="${WORK_DIR}/home"
export OMNI_AGENT_CONFIG_DIR="${WORK_DIR}/config"
export OMNI_AGENT_SANDBOX_ROOT="${WORK_DIR}/sandbox"
export OMNI_AGENT_DISABLE_AUTO_UPDATE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${WORK_DIR}/pycache"
export OMNI_AGENT_REPO_URL="file:///oaa-test/repo-source"
export OMNI_DOCKER_SMOKE_INSTALLER_HOST="${OMNI_DOCKER_SMOKE_INSTALLER_HOST:-127.0.0.1}"

mkdir -p "${HOME}" "${WORK_DIR}/fakebin" "${WORK_DIR}/bin"
export PATH="${WORK_DIR}/fakebin:${PATH}"

cat > "${HOME}/.gitconfig" <<'GITCONF'
[safe]
	directory = /oaa-test/repo-source
	directory = /oaa-test/repo-source/.git
GITCONF

for cmd in gemini codex claude plandex aider goose opencode futureagent; do
  write_fake_binary "${cmd}"
done
write_openclaw_binary

if command -v python3 >/dev/null 2>&1; then
  printf "docker-smoke failed: python3 should not be preinstalled for this scenario\n" >&2
  exit 1
fi

if command -v git >/dev/null 2>&1; then
  printf "docker-smoke failed: git should not be preinstalled for this scenario\n" >&2
  exit 1
fi

curl -fsSL --connect-timeout 5 --max-time 30 "http://${OMNI_DOCKER_SMOKE_INSTALLER_HOST}:${OMNI_DOCKER_SMOKE_HTTP_PORT}/install.sh" \
  | env \
      HOME="${HOME}" \
      PATH="${PATH}" \
      GIT_CONFIG_COUNT="2" \
      GIT_CONFIG_KEY_0="safe.directory" \
      GIT_CONFIG_VALUE_0="/oaa-test/repo-source" \
      GIT_CONFIG_KEY_1="safe.directory" \
      GIT_CONFIG_VALUE_1="/oaa-test/repo-source/.git" \
      OMNI_AGENT_CONFIG_DIR="${OMNI_AGENT_CONFIG_DIR}" \
      OMNI_AGENT_SANDBOX_ROOT="${OMNI_AGENT_SANDBOX_ROOT}" \
      OMNI_AGENT_DISABLE_AUTO_UPDATE="${OMNI_AGENT_DISABLE_AUTO_UPDATE}" \
      OMNI_AGENT_REPO_URL="${OMNI_AGENT_REPO_URL}" \
      OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin" \
      OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install" \
      bash

test -d "${WORK_DIR}/install/.git"

env \
  HOME="${HOME}" \
  PATH="${PATH}" \
  GIT_CONFIG_COUNT="2" \
  GIT_CONFIG_KEY_0="safe.directory" \
  GIT_CONFIG_VALUE_0="/oaa-test/repo-source" \
  GIT_CONFIG_KEY_1="safe.directory" \
  GIT_CONFIG_VALUE_1="/oaa-test/repo-source/.git" \
  OMNI_AGENT_CONFIG_DIR="${OMNI_AGENT_CONFIG_DIR}" \
  OMNI_AGENT_SANDBOX_ROOT="${OMNI_AGENT_SANDBOX_ROOT}" \
  OMNI_AGENT_DISABLE_AUTO_UPDATE="${OMNI_AGENT_DISABLE_AUTO_UPDATE}" \
  OMNI_AGENT_REPO_URL="${OMNI_AGENT_REPO_URL}" \
  OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin" \
  OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/install" \
  bash "${WORK_DIR}/install/.omni-autonomous-agent/install.sh" >/dev/null

cp "${WORK_DIR}/install/.omni-autonomous-agent/install.sh" "${WORK_DIR}/detached-install.sh"

mkdir -p "${WORK_DIR}/not-a-git-install"
echo "not-a-repo" > "${WORK_DIR}/not-a-git-install/marker.txt"
set +e
env \
  HOME="${HOME}" \
  PATH="${PATH}" \
  GIT_CONFIG_COUNT="2" \
  GIT_CONFIG_KEY_0="safe.directory" \
  GIT_CONFIG_VALUE_0="/oaa-test/repo-source" \
  GIT_CONFIG_KEY_1="safe.directory" \
  GIT_CONFIG_VALUE_1="/oaa-test/repo-source/.git" \
  OMNI_AGENT_CONFIG_DIR="${OMNI_AGENT_CONFIG_DIR}" \
  OMNI_AGENT_SANDBOX_ROOT="${OMNI_AGENT_SANDBOX_ROOT}" \
  OMNI_AGENT_DISABLE_AUTO_UPDATE="${OMNI_AGENT_DISABLE_AUTO_UPDATE}" \
  OMNI_AGENT_REPO_URL="${OMNI_AGENT_REPO_URL}" \
  OMNI_AGENT_LOCAL_BIN="${WORK_DIR}/bin" \
  OMNI_AGENT_INSTALL_DIR="${WORK_DIR}/not-a-git-install" \
  bash "${WORK_DIR}/detached-install.sh" >"${WORK_DIR}/non-git-install.out" 2>&1
NON_GIT_INSTALL_CODE=$?
set -e
test "${NON_GIT_INSTALL_CODE}" -ne 0
grep -q "is not a git repository" "${WORK_DIR}/non-git-install.out"

CLI="${WORK_DIR}/bin/omni-autonomous-agent"
export PATH="${WORK_DIR}/bin:${PATH}"

"${CLI}" --bootstrap

test -f "${HOME}/.gemini/settings.json"
test -f "${HOME}/.claude/settings.json"
test -f "${HOME}/.config/opencode/plugins/omni-hook.ts"
test -f "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
test -f "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
test -f "${HOME}/.local/bin/omni-wrap-codex"
test -f "${HOME}/.local/bin/omni-wrap-plandex"
test -f "${HOME}/.local/bin/omni-agent-wrap"

grep -q 'events: \["gateway:startup", "message:received", "message:transcribed", "message:preprocessed", "session:compact:before"\]' "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
grep -q -- "--hook-precompact" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "message:transcribed" "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
grep -q "message:preprocessed" "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
grep -q "session:compact:before" "${HOME}/.openclaw/hooks/omni-recovery/HOOK.md"
grep -q "\\['received', 'transcribed', 'preprocessed'\\].includes(event.action)" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "readInboundEventText" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "event.type === 'session'" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"
grep -q "event.action === 'compact:before'" "${HOME}/.openclaw/hooks/omni-recovery/handler.ts"

openclaw hooks list | grep -q "omni-recovery"
openclaw hooks info omni-recovery | grep -q "session:compact:before"
openclaw hooks check | grep -q "omni-recovery"

printf '{bad-json' > "${HOME}/.gemini/settings.json"
printf '{bad-json' > "${HOME}/.claude/settings.json"
"${CLI}" --bootstrap >/dev/null
ls "${HOME}/.gemini"/settings.json.invalid.* >/dev/null
ls "${HOME}/.claude"/settings.json.invalid.* >/dev/null

set +e
"${HOME}/.local/bin/omni-wrap-codex" --exit-code 7
WRAP_CODE=$?
set -e
test "${WRAP_CODE}" -eq 3

AWAIT_ADD_OUTPUT="$("${CLI}" --add -R "docker await user" -D dynamic)"
printf "%s\n" "${AWAIT_ADD_OUTPUT}" | grep -q "omni-autonomous-agent - active"

AWAIT_OUTPUT="$("${CLI}" --await-user -Q "Need confirmation")"
printf "%s\n" "${AWAIT_OUTPUT}" > "${WORK_DIR}/await-user-output.txt"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ["WORK_DIR"])
lines = [
    line
    for line in (work_dir / "await-user-output.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
assert payload["hook"] == "await-user"
assert payload["wait_minutes"] == 2
assert payload["waiting_for_user"] is True
PY

RESPONDED_OUTPUT="$("${CLI}" --user-responded --response-note "user answered")"
printf "%s\n" "${RESPONDED_OUTPUT}" > "${WORK_DIR}/user-responded-output.txt"

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ["WORK_DIR"])
lines = [
    line
    for line in (work_dir / "user-responded-output.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
assert payload["hook"] == "user-responded"
assert payload["user_response_registered"] is True
assert payload["waiting_for_user"] is False
PY

"${CLI}" --cancel
"${CLI}" --cancel-accept --decision-note "docker cleanup"

AGENT="futureagent" "${CLI}" --bootstrap
test -f "${HOME}/.local/bin/omni-wrap-futureagent"

OMNI_AGENT_EXTRA_WRAPPERS="soonagent" "${CLI}" --bootstrap
test -f "${HOME}/.local/bin/omni-wrap-soonagent"

UNSAFE_OUTPUT="$(OMNI_AGENT_EXTRA_WRAPPERS='bad;token' "${CLI}" --bootstrap)"
printf "%s\n" "${UNSAFE_OUTPUT}" | grep -q "Skipped unsafe wrapper command token"
test ! -f "${HOME}/.local/bin/omni-wrap-bad-token"

FIXED_OUTPUT="$("${CLI}" --add -R "docker fixed" -D 1)"
printf "%s\n" "${FIXED_OUTPUT}" | grep -q "omni-autonomous-agent - active"

set +e
"${CLI}" --hook-stop > "${WORK_DIR}/fixed-hook-stop-output.txt"
FIXED_HOOK_CODE=$?
set -e
test "${FIXED_HOOK_CODE}" -eq 2

"${CLI}" --cancel
"${CLI}" --cancel-accept --decision-note "docker fixed cleanup"

ADD_OUTPUT="$("${CLI}" --add -R "docker verification")"
printf "%s\n" "${ADD_OUTPUT}" | grep -q "omni-autonomous-agent - active"

python3 - <<'PY'
import json
import os
from pathlib import Path

state = json.loads(
    Path(os.environ["OMNI_AGENT_CONFIG_DIR"], "state.json").read_text(encoding="utf-8")
)
assert state["duration_mode"] == "dynamic"
PY

set +e
"${CLI}" --hook-stop > "${WORK_DIR}/hook-stop-output.txt"
HOOK_CODE=$?
set -e
test "${HOOK_CODE}" -eq 2

python3 - <<'PY'
import json
import os
from pathlib import Path

work_dir = Path(os.environ["WORK_DIR"])
lines = [
    line
    for line in (work_dir / "hook-stop-output.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
payload = json.loads(lines[-1])
assert payload["continue"] is True
assert payload["block"] is True
PY

assert_timeout_loop

python3 - <<'PY'
import json
import os
from pathlib import Path

state = json.loads(
    Path(os.environ["OMNI_AGENT_CONFIG_DIR"], "state.json").read_text(encoding="utf-8")
)
report = Path(state["sandbox_dir"]) / "REPORT.md"
text = report.read_text(encoding="utf-8")
report.write_text(text.replace("IN_PROGRESS", "COMPLETE", 1), encoding="utf-8")
PY

set +e
"${HOME}/.local/bin/omni-wrap-codex" --exit-code 7
FINAL_WRAP_CODE=$?
set -e
test "${FINAL_WRAP_CODE}" -eq 7

"${CLI}" --status > "${WORK_DIR}/post-final-status.txt"
grep -q "No active session" "${WORK_DIR}/post-final-status.txt"

echo "{bad-json" > "${OMNI_AGENT_CONFIG_DIR}/state.json"

set +e
"${CLI}" --require-active > "${WORK_DIR}/require-active-invalid.txt"
REQUIRE_CODE=$?
set -e
test "${REQUIRE_CODE}" -ne 0

"${CLI}" --cancel
ls "${OMNI_AGENT_CONFIG_DIR}"/state.invalid.*.json >/dev/null

"${CLI}" --status > "${WORK_DIR}/hook-stop-final.txt"

python3 - <<'PY'
import os
from pathlib import Path

archived_root = Path(os.environ["OMNI_AGENT_SANDBOX_ROOT"]) / "archived"
latest = sorted(archived_root.iterdir())[-1]
report = latest / "REPORT.md"
text = report.read_text(encoding="utf-8")
assert "**🕐 Completed at:** in progress" not in text
assert "**⏱️ Duration:** <actual time worked>" not in text
assert "### 🚦 Status" in text
PY

test ! -f "${OMNI_AGENT_CONFIG_DIR}/state.json"
test -d "${OMNI_AGENT_SANDBOX_ROOT}/archived"
test -n "$(ls -A "${OMNI_AGENT_SANDBOX_ROOT}/archived")"

"${CLI}" --status
printf "docker-smoke passed for %s\n" "${IMAGE_NAME}"
EOF
  chmod +x "${WORK_DIR}/container-check.sh"
}

run_image() {
  local image="$1"
  local -a docker_run_args=()
  read -r -a docker_run_args <<< "${DOCKER_RUN_ARGS_RAW}"
  printf "docker-smoke running %s\n" "${image}"
  docker run --rm \
    "${docker_run_args[@]}" \
    -e OMNI_TEST_IMAGE="${image}" \
    -e OMNI_DOCKER_SMOKE_HTTP_PORT="${HTTP_PORT}" \
    -e OMNI_DOCKER_SMOKE_INSTALLER_HOST="${INSTALLER_HOST}" \
    -v "${ROOT_DIR}:/src:ro" \
    -v "${WORK_DIR}:/oaa-test" \
    "${image}" /bin/sh -lc '
      set -eu
      case "${OMNI_TEST_IMAGE}" in
        alpine:*)
          apk add --no-cache bash curl >/dev/null
          ;;
        ubuntu:*|debian:*)
          export DEBIAN_FRONTEND=noninteractive
          apt-get update -qq >/dev/null
          apt-get install -y -qq bash curl >/dev/null
          ;;
        *)
          printf "docker-smoke failed: unsupported image %s\n" "${OMNI_TEST_IMAGE}" >&2
          exit 1
          ;;
      esac
      bash /oaa-test/container-check.sh
    '
}

require_cmd docker
require_cmd curl
require_cmd python3
require_cmd rsync
require_cmd git
ensure_docker
prepare_repo_fixture
start_installer_server
write_container_script

IMAGES_RAW="${OMNI_DOCKER_SMOKE_IMAGES:-ubuntu:24.04 debian:12-slim alpine:3.20}"
read -r -a IMAGES <<< "${IMAGES_RAW}"

if [[ "${#IMAGES[@]}" -eq 0 ]]; then
  printf "docker-smoke failed: no images configured\n" >&2
  exit 1
fi

for image in "${IMAGES[@]}"; do
  run_image "${image}"
done

printf "docker-smoke passed\n"
