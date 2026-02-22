#!/usr/bin/env bash
set -euo pipefail

BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"
SEP="${DIM}----------------------------------------------------------------------${RESET}"

REPO_URL="https://github.com/omnious0o0/omni-autonomous-agent.git"
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

SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd || true)"
if [[ -z "${SCRIPT_SOURCE_DIR}" || ! -f "${SCRIPT_SOURCE_DIR}/../main.py" ]]; then
  header "Bootstrapping repository"
  if ! command -v git >/dev/null 2>&1; then
    printf "%berror:%b git is required for install.\n" "${RED}" "${RESET}" >&2
    exit 1
  fi

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    row "Repository" "${INSTALL_DIR} (existing, pulling latest)"
    git -C "${INSTALL_DIR}" pull --ff-only
  elif [[ -e "${INSTALL_DIR}" ]]; then
    printf "%berror:%b %s exists but is not a git repository.\n" "${RED}" "${RESET}" "${INSTALL_DIR}" >&2
    printf "       Remove it manually or set OMNI_AGENT_INSTALL_DIR to a clean location.\n" >&2
    exit 1
  else
    row "Repository" "Cloning to ${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
  fi

  exec bash "${INSTALL_DIR}/.omni-autonomous-agent/install.sh"
fi

ROOT_DIR="$(cd "${SCRIPT_SOURCE_DIR}/.." >/dev/null 2>&1 && pwd)"
MAIN_SCRIPT="${ROOT_DIR}/main.py"

LOCAL_BIN="${OMNI_AGENT_LOCAL_BIN:-${HOME}/.local/bin}"
SYSTEM_BIN="/usr/local/bin"
TARGET_DIR=""
SUDO_CMD=()

mkdir -p "${LOCAL_BIN}" || true
if [[ -w "${LOCAL_BIN}" ]]; then
  TARGET_DIR="${LOCAL_BIN}"
elif [[ -d "${SYSTEM_BIN}" && -w "${SYSTEM_BIN}" ]]; then
  TARGET_DIR="${SYSTEM_BIN}"
elif [[ -d "${SYSTEM_BIN}" ]] && command -v sudo >/dev/null 2>&1; then
  TARGET_DIR="${SYSTEM_BIN}"
  if [[ -t 0 ]]; then
    SUDO_CMD=(sudo)
  else
    if sudo -n true >/dev/null 2>&1; then
      SUDO_CMD=(sudo -n)
    else
      printf "%berror:%b non-interactive install requires passwordless sudo for %s.\n" "${RED}" "${RESET}" "${SYSTEM_BIN}" >&2
      printf "       Re-run interactively, add %s to PATH, or set OMNI_AGENT_LOCAL_BIN.\n" "${LOCAL_BIN}" >&2
      exit 1
    fi
  fi
else
  printf "%berror:%b no writable install target found.\n" "${RED}" "${RESET}" >&2
  exit 1
fi

DEST="${TARGET_DIR}/${DEST_NAME}"

if [[ -d "${DEST}" && ! -L "${DEST}" ]]; then
  printf "%berror:%b %s is a directory. Refusing to overwrite it.\n" "${RED}" "${RESET}" "${DEST}" >&2
  exit 1
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

header "Installed"
row "Source" "${MAIN_SCRIPT}"
row "Link" "${DEST}"
row "Self-check" "Read ${ROOT_DIR}/install-help.md"

if "${DEST}" --bootstrap >/dev/null 2>&1; then
  row "Bootstrap" "Completed automatic hook/setup bootstrap"
else
  row "Bootstrap" "Automatic bootstrap failed"
  printf "%berror:%b bootstrap did not complete successfully.\n" "${RED}" "${RESET}" >&2
  printf "       Run '%s --bootstrap' and fix reported warnings before autonomous use.\n" "${DEST}" >&2
  exit 1
fi

printf "\nRun %b%s --status%b to verify.\n" "${BOLD}" "${DEST_NAME}" "${RESET}"
printf "Read install-help.md and apply the self-check hook guidance before autonomous sessions.\n"

if [[ ":${PATH}:" != *":${TARGET_DIR}:"* ]]; then
  printf "\n%bWarning:%b %s is not on PATH.\n" "${YELLOW}" "${RESET}" "${TARGET_DIR}"
  printf "Add this line to your shell config:\n  export PATH=\"%s:\$PATH\"\n" "${TARGET_DIR}"
fi

printf "%b\n" "${SEP}"
