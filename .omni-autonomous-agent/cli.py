from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path

from .session_manager import (
    cmd_add,
    cmd_cancel,
    cmd_dummy,
    cmd_hook_precompact,
    cmd_hook_stop,
    cmd_require_active,
    cmd_status,
)
from .updater import cmd_update, maybe_auto_update


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omni-autonomous-agent",
        description="Autonomous session manager for long-running AI execution.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--add", action="store_true", help="Register a new session")
    group.add_argument("--status", action="store_true", help="Show session status")
    group.add_argument(
        "--require-active",
        action="store_true",
        help="Exit non-zero unless there is an active session",
    )
    group.add_argument("--cancel", action="store_true", help="Cancel active session")
    group.add_argument("--hook-stop", action="store_true", help="Stop hook")
    group.add_argument("--hook-precompact", action="store_true", help="Precompact hook")
    group.add_argument("--update", action="store_true", help="Update this installation")
    group.add_argument("--install", action="store_true", help="Run installer script")
    group.add_argument(
        "--bootstrap",
        action="store_true",
        help="Auto-configure hooks and wrappers with no manual setup",
    )
    group.add_argument("--dummy", action="store_true", help="Register a test session")

    parser.add_argument(
        "-R",
        "--request",
        metavar="REQUEST",
        type=str,
        help="Task request (required with --add)",
    )
    parser.add_argument(
        "-D",
        "--duration",
        metavar="MINUTES_OR_DYNAMIC",
        type=str,
        help="Duration in minutes or 'dynamic' (required with --add)",
    )
    return parser


def _run_install_script() -> None:
    script_dir = Path(__file__).resolve().parent
    install_sh = script_dir / "install.sh"
    if not install_sh.exists():
        sys.exit(f"error: install.sh not found at {install_sh}")

    subprocess.run(["bash", str(install_sh)], check=True)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not (
        args.update
        or args.install
        or args.hook_stop
        or args.hook_precompact
        or args.require_active
        or args.bootstrap
    ):
        maybe_auto_update()

    if args.add:
        missing: list[str] = []
        if not args.request:
            missing.append("-R/--request")
        if missing:
            parser.error(f"--add requires: {', '.join(missing)}")

        duration = args.duration if args.duration is not None else "dynamic"
        cmd_add(args.request, duration)
        cmd_status()
        return

    if args.status:
        cmd_status()
        return

    if args.require_active:
        cmd_require_active()
        return

    if args.cancel:
        cmd_cancel()
        return

    if args.hook_stop:
        cmd_hook_stop()
        return

    if args.hook_precompact:
        cmd_hook_precompact()
        return

    if args.update:
        cmd_update()
        return

    if args.install:
        _run_install_script()
        return

    if args.bootstrap:
        module = importlib.import_module(f"{__package__}.bootstrap")
        module.cmd_bootstrap()
        return

    if args.dummy:
        cmd_dummy()
        return

    parser.error("no command selected")


if __name__ == "__main__":
    main()
