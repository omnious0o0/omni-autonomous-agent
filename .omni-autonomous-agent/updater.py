from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .constants import BOLD, CONFIG_DIR, DIM, GREEN, RED, SEP, c


def _header(title: str) -> None:
    print(SEP)
    print(f"  {c(BOLD, title)}")
    print(SEP)


def _command_timeout_seconds(default_seconds: int) -> int:
    raw = os.environ.get("OMNI_AGENT_COMMAND_TIMEOUT", "").strip()
    if not raw:
        return default_seconds
    try:
        parsed = int(raw)
    except ValueError:
        return default_seconds
    if parsed <= 0:
        return default_seconds
    return parsed


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=_command_timeout_seconds(30),
    )
    return (result.stdout or "").strip()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_git_worktree(repo_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_command_timeout_seconds(30),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("git rev-parse timed out") from exc
    except FileNotFoundError:
        return False
    return result.returncode == 0 and (result.stdout or "").strip() == "true"


def _auto_update_state_path() -> Path:
    return CONFIG_DIR / "update_state.json"


def _load_auto_update_state() -> dict[str, str]:
    path = _auto_update_state_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items()}


def _save_auto_update_state(payload: dict[str, str]) -> None:
    path = _auto_update_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=str(path.parent)
    ) as temp_file:
        temp_file.write(json.dumps(payload, indent=2) + "\n")
        temp_path = Path(temp_file.name)
    temp_path.replace(path)


def _parse_interval_minutes() -> int:
    raw = os.environ.get("OMNI_AGENT_AUTO_UPDATE_MINUTES", "240").strip()
    try:
        parsed = int(raw)
    except ValueError:
        return 240
    if parsed <= 0:
        return 240
    return parsed


def _should_skip_auto_update() -> bool:
    value = os.environ.get("OMNI_AGENT_DISABLE_AUTO_UPDATE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def maybe_auto_update() -> None:
    if _should_skip_auto_update():
        return

    if shutil.which("git") is None:
        return

    repo_root = _repo_root()
    try:
        if not _is_git_worktree(repo_root):
            return
    except RuntimeError:
        return

    now = datetime.now().astimezone()
    interval = timedelta(minutes=_parse_interval_minutes())

    state = _load_auto_update_state()
    last_checked_raw = state.get("last_checked")
    if last_checked_raw:
        try:
            last_checked = datetime.fromisoformat(last_checked_raw)
            if now - last_checked < interval:
                return
        except (TypeError, ValueError):
            pass

    next_state: dict[str, str] = {
        "last_checked": now.isoformat(),
    }

    try:
        dirty = _git(repo_root, "status", "--porcelain")
        branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        if dirty or branch == "HEAD":
            next_state["last_result"] = "skipped"
            _save_auto_update_state(next_state)
            return

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=_command_timeout_seconds(120),
        )
        if result.returncode == 0:
            next_state["last_result"] = "updated"
            next_state["last_output"] = (result.stdout or "").strip()
        else:
            next_state["last_result"] = "failed"
            next_state["last_output"] = (result.stderr or "").strip() or (
                result.stdout or ""
            ).strip()
    except Exception as exc:
        next_state["last_result"] = "failed"
        next_state["last_output"] = str(exc)

    _save_auto_update_state(next_state)


def cmd_update() -> None:
    repo_root = _repo_root()

    if shutil.which("git") is None:
        sys.exit("error: git is required for --update")

    try:
        is_worktree = _is_git_worktree(repo_root)
    except RuntimeError as exc:
        sys.exit(f"error: update precheck failed: {exc}")

    if not is_worktree:
        sys.exit(
            f"error: {repo_root} is not a git repository. "
            "The --update command works only for git-cloned installs."
        )

    _header("Updating omni-autonomous-agent")
    print(f"  {c(DIM, 'Checking repository state...')}")

    try:
        dirty = _git(repo_root, "status", "--porcelain")
    except subprocess.TimeoutExpired as exc:
        sys.exit(f"error: update precheck failed: git status timed out: {exc}")
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "git status failed").strip()
        sys.exit(f"error: update precheck failed: {details}")

    if dirty:
        sys.exit(
            "error: update aborted because local changes are present. "
            "Commit or stash changes before running --update."
        )

    try:
        branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    except subprocess.TimeoutExpired as exc:
        sys.exit(f"error: update precheck failed: git rev-parse timed out: {exc}")
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "git rev-parse failed").strip()
        sys.exit(f"error: update precheck failed: {details}")

    if branch == "HEAD":
        sys.exit(
            "error: update aborted because repository is in detached HEAD state. "
            "Checkout a branch first."
        )

    print(f"  {c(DIM, 'Pulling latest changes from remote...')}")

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=_command_timeout_seconds(120),
        )
    except subprocess.TimeoutExpired as exc:
        sys.exit(f"error: update failed: git pull timed out: {exc}")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or "git pull failed"
        sys.exit(f"error: update failed: {details}")

    output = (result.stdout or "").strip()
    if output:
        print(f"  {c(GREEN, 'OK')} {output}")
    else:
        print(f"  {c(GREEN, 'OK')} Update complete")
    print(SEP)
