from __future__ import annotations

import os
from pathlib import Path
import sys


def _path_env(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return Path(value).expanduser()


CONFIG_DIR = _path_env(
    "OMNI_AGENT_CONFIG_DIR", Path.home() / ".config" / "omni-autonomous-agent"
)
STATE_FILE = CONFIG_DIR / "state.json"

REPO_ROOT = _path_env("OMNI_AGENT_REPO_ROOT", Path(__file__).resolve().parent.parent)
SANDBOX_ROOT = _path_env("OMNI_AGENT_SANDBOX_ROOT", REPO_ROOT / "omni-sandbox")
ARCHIVE_ROOT = SANDBOX_ROOT / "archived"

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"
SEP = f"{DIM}{'-' * 70}{RESET}"


def supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if supports_color() else text
