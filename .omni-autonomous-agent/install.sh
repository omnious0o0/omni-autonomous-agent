#!/usr/bin/env bash
set -euo pipefail

BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"
SEP="${DIM}----------------------------------------------------------------------${RESET}"

REPO_URL="${OMNI_AGENT_REPO_URL:-https://github.com/omnious0o0/omni-autonomous-agent.git}"
INSTALL_DIR="${OMNI_AGENT_INSTALL_DIR:-${HOME}/.omni-autonomous-agent}"
DEST_NAME="omni-autonomous-agent"

header() {
  local title="$1"
  printf "%b\n" "${SEP}"
  printf "  %b\n" "${BOLD}${title}${RESET}"
  printf "%b\n" "${SEP}"
}

row() {
  local label="$1"
  local value="$2"
  printf "  %b%-20s%b %s\n" "${DIM}" "${label}:" "${RESET}" "${value}"
}

fail() {
  printf "%berror:%b %s\n" "${RED}" "${RESET}" "$1" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

python3_meets_minimum() {
  if ! has_cmd python3; then
    return 1
  fi

  python3 - <<'PY' >/dev/null 2>&1
import sys

sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
}

python3_version_text() {
  if ! has_cmd python3; then
    return 1
  fi

  python3 --version 2>&1 | awk '{print $2}'
}

pkg_manager() {
  local managers=(apt-get dnf yum pacman zypper apk brew)
  local manager
  for manager in "${managers[@]}"; do
    if has_cmd "${manager}"; then
      printf "%s\n" "${manager}"
      return 0
    fi
  done
  return 1
}

run_privileged() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
    return
  fi

  if ! has_cmd sudo; then
    fail "install requires elevated package installation for '$*', but sudo is unavailable"
  fi

  if [[ -t 0 ]]; then
    sudo "$@"
    return
  fi

  if sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
    return
  fi

  fail "non-interactive install cannot elevate for '$*'; rerun interactively or preinstall dependencies"
}

install_packages() {
  local manager
  if ! manager="$(pkg_manager)"; then
    fail "no supported package manager found to install required dependencies"
  fi

  case "${manager}" in
    apt-get)
      run_privileged apt-get update -qq
      run_privileged apt-get install -y -qq "$@"
      ;;
    dnf)
      run_privileged dnf install -y -q "$@"
      ;;
    yum)
      run_privileged yum install -y -q "$@"
      ;;
    pacman)
      run_privileged pacman -Sy --noconfirm "$@"
      ;;
    zypper)
      run_privileged zypper --non-interactive install "$@"
      ;;
    apk)
      run_privileged apk add --no-cache "$@"
      ;;
    brew)
      brew install "$@"
      ;;
  esac
}

ensure_python() {
  if python3_meets_minimum; then
    return
  fi

  header "Installing Python runtime"
  if has_cmd python3; then
    local current_version
    current_version="$(python3_version_text || true)"
    if [[ -n "${current_version}" ]]; then
      row "Detected" "python3 ${current_version} (requires >= 3.10)"
    fi
  fi
  local manager
  if ! manager="$(pkg_manager)"; then
    fail "python3 >= 3.10 is required and no supported package manager is available"
  fi

  case "${manager}" in
    apt-get) install_packages python3 ;;
    dnf) install_packages python3 ;;
    yum) install_packages python3 ;;
    pacman) install_packages python ;;
    zypper) install_packages python3 ;;
    apk) install_packages python3 ;;
    brew) install_packages python ;;
  esac

  python3_meets_minimum || fail "python3 >= 3.10 installation did not succeed"
}

ensure_git() {
  if has_cmd git; then
    return
  fi

  header "Installing git"
  local manager
  if ! manager="$(pkg_manager)"; then
    fail "git is required and no supported package manager is available"
  fi

  case "${manager}" in
    apt-get|dnf|yum|zypper|apk|brew) install_packages git ;;
    pacman) install_packages git ;;
  esac

  has_cmd git || fail "git installation did not succeed"
}

ensure_clean_git_checkout() {
  local repo_dir="$1"
  local dirty branch

  if ! dirty="$(git -C "${repo_dir}" status --porcelain 2>/dev/null)"; then
    fail "failed to inspect existing repository state at ${repo_dir}"
  fi
  if [[ -n "${dirty}" ]]; then
    fail "existing install at ${repo_dir} has local changes. Commit or stash them before rerunning installer."
  fi

  if ! branch="$(git -C "${repo_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
    fail "failed to inspect existing repository branch at ${repo_dir}"
  fi
  if [[ "${branch}" == "HEAD" ]]; then
    fail "existing install at ${repo_dir} is in detached HEAD state. Checkout a branch before rerunning installer."
  fi
}

SCRIPT_SOURCE_DIR=""
if ! SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"; then
  SCRIPT_SOURCE_DIR=""
fi
if [[ -z "${SCRIPT_SOURCE_DIR}" || ! -f "${SCRIPT_SOURCE_DIR}/../main.py" ]]; then
  header "Bootstrapping repository"
  ensure_python
  ensure_git

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    ensure_clean_git_checkout "${INSTALL_DIR}"
    row "Repository" "${INSTALL_DIR} (existing, pulling latest)"
    GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -C "${INSTALL_DIR}" pull --ff-only
  elif [[ -e "${INSTALL_DIR}" ]]; then
    fail "${INSTALL_DIR} exists but is not a git repository. Remove it manually or set OMNI_AGENT_INSTALL_DIR to a clean location."
  else
    row "Repository" "Cloning to ${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
  fi

  exec bash "${INSTALL_DIR}/.omni-autonomous-agent/install.sh"
fi

ensure_python

ROOT_DIR="$(cd "${SCRIPT_SOURCE_DIR}/.." >/dev/null 2>&1 && pwd)"
MAIN_SCRIPT="${ROOT_DIR}/main.py"

LOCAL_BIN="${OMNI_AGENT_LOCAL_BIN:-${HOME}/.local/bin}"
SYSTEM_BIN_OVERRIDE_RAW="${OMNI_AGENT_SYSTEM_BIN:-}"
SYSTEM_BIN="${SYSTEM_BIN_OVERRIDE_RAW:-/usr/local/bin}"
TARGET_DIR=""
SUDO_CMD=()

LOCAL_BIN_CREATE_ERROR=""
if ! mkdir -p "${LOCAL_BIN}" >/dev/null 2>&1; then
  LOCAL_BIN_CREATE_ERROR="could not create ${LOCAL_BIN}"
fi
if [[ -w "${LOCAL_BIN}" ]]; then
  TARGET_DIR="${LOCAL_BIN}"
fi

if [[ -z "${TARGET_DIR}" && -n "${SYSTEM_BIN_OVERRIDE_RAW}" && ! -d "${SYSTEM_BIN}" ]]; then
  if ! mkdir -p "${SYSTEM_BIN}" >/dev/null 2>&1; then
    fail "OMNI_AGENT_SYSTEM_BIN path could not be created: ${SYSTEM_BIN}"
  fi
fi

if [[ -z "${TARGET_DIR}" ]]; then
  if [[ -d "${SYSTEM_BIN}" && -w "${SYSTEM_BIN}" ]]; then
    TARGET_DIR="${SYSTEM_BIN}"
  elif [[ -d "${SYSTEM_BIN}" ]] && has_cmd sudo; then
    TARGET_DIR="${SYSTEM_BIN}"
    if [[ -t 0 ]]; then
      SUDO_CMD=(sudo)
    elif sudo -n true >/dev/null 2>&1; then
      SUDO_CMD=(sudo -n)
    else
      fail "non-interactive install requires passwordless sudo for ${SYSTEM_BIN}; re-run interactively, add ${LOCAL_BIN} to PATH, or set OMNI_AGENT_LOCAL_BIN"
    fi
  elif [[ -n "${SYSTEM_BIN_OVERRIDE_RAW}" ]]; then
    fail "OMNI_AGENT_SYSTEM_BIN path is not writable or could not be created: ${SYSTEM_BIN}"
  else
    if [[ -n "${LOCAL_BIN_CREATE_ERROR}" ]]; then
      fail "no writable install target found. Local bin detail: ${LOCAL_BIN_CREATE_ERROR}"
    fi
    fail "no writable install target found. Set OMNI_AGENT_LOCAL_BIN or OMNI_AGENT_SYSTEM_BIN to a writable path."
  fi
fi

DEST="${TARGET_DIR}/${DEST_NAME}"

if [[ -d "${DEST}" && ! -L "${DEST}" ]]; then
  fail "${DEST} is a directory. Refusing to overwrite it."
fi

if [[ ${#SUDO_CMD[@]} -gt 0 ]]; then
  "${SUDO_CMD[@]}" chmod +x "${MAIN_SCRIPT}"
  "${SUDO_CMD[@]}" rm -f "${DEST}"
  "${SUDO_CMD[@]}" ln -s "${MAIN_SCRIPT}" "${DEST}"
else
  chmod +x "${MAIN_SCRIPT}"
  rm -f "${DEST}"
  ln -s "${MAIN_SCRIPT}" "${DEST}"
fi

BOOTSTRAP_TIMEOUT_RAW="${OMNI_AGENT_BOOTSTRAP_TIMEOUT:-120}"
BOOTSTRAP_TIMEOUT="120"
if [[ "${BOOTSTRAP_TIMEOUT_RAW}" =~ ^[1-9][0-9]*$ ]]; then
  BOOTSTRAP_TIMEOUT="${BOOTSTRAP_TIMEOUT_RAW}"
fi

run_bootstrap_with_timeout() {
  local cli_path="$1"
  local timeout_seconds="$2"

  if has_cmd timeout; then
    timeout "${timeout_seconds}" "${cli_path}" --bootstrap
    return $?
  fi

  python3 - "${timeout_seconds}" "${cli_path}" <<'PY'
import subprocess
import sys

timeout_seconds = int(sys.argv[1])
cli_path = sys.argv[2]

try:
    completed = subprocess.run([cli_path, "--bootstrap"], check=False, timeout=timeout_seconds)
except subprocess.TimeoutExpired:
    sys.exit(124)
except FileNotFoundError:
    sys.exit(127)

sys.exit(completed.returncode)
PY
}

header "Installed"
row "Source" "${MAIN_SCRIPT}"
row "Link" "${DEST}"
row "Self-check" "Read ${ROOT_DIR}/install-help.md"

if run_bootstrap_with_timeout "${DEST}" "${BOOTSTRAP_TIMEOUT}" >/dev/null 2>&1; then
  row "Bootstrap" "Completed automatic hook/setup bootstrap"
else
  bootstrap_code=$?
  row "Bootstrap" "Automatic bootstrap failed"
  if [[ "${bootstrap_code}" -eq 124 ]]; then
    fail "bootstrap timed out after ${BOOTSTRAP_TIMEOUT}s. Run '${DEST} --bootstrap' and fix reported warnings before autonomous use."
  fi
  fail "bootstrap did not complete successfully. Run '${DEST} --bootstrap' and fix reported warnings before autonomous use."
fi

printf "\nRun %b%s --status%b to verify.\n" "${BOLD}" "${DEST_NAME}" "${RESET}"
printf "Read install-help.md and apply the self-check hook guidance before autonomous sessions.\n"

if [[ ":${PATH}:" != *":${TARGET_DIR}:"* ]]; then
  printf "\n%bWarning:%b %s is not on PATH.\n" "${YELLOW}" "${RESET}" "${TARGET_DIR}"
  printf "Add this line to your shell config:\n  export PATH=\"%s:\$PATH\"\n" "${TARGET_DIR}"
fi

printf "%b\n" "${SEP}"
