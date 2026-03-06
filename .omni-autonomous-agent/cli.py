from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .session_manager import (
    cmd_add,
    cmd_await_user,
    cmd_cancel_accept,
    cmd_cancel_deny,
    cmd_cancel,
    cmd_claim_execution_owner,
    cmd_clear_stale_execution_owner,
    cmd_dummy,
    cmd_hook_precompact,
    cmd_hook_stop,
    cmd_log_event,
    cmd_record_openclaw_route,
    cmd_release_execution_owner,
    cmd_revise_session,
    cmd_heartbeat_execution_owner,
    cmd_require_active,
    cmd_status,
    cmd_user_responded,
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
    group.add_argument(
        "--cancel-accept",
        action="store_true",
        help="Accept a pending cancellation request",
    )
    group.add_argument(
        "--cancel-deny",
        action="store_true",
        help="Deny a pending cancellation request",
    )
    group.add_argument("--hook-stop", action="store_true", help="Stop hook")
    group.add_argument("--hook-precompact", action="store_true", help="Precompact hook")
    group.add_argument("--update", action="store_true", help="Update this installation")
    group.add_argument("--install", action="store_true", help="Run installer script")
    group.add_argument(
        "--revise-session",
        action="store_true",
        help="Revise the active session request and/or duration",
    )
    group.add_argument(
        "--bootstrap",
        action="store_true",
        help="Auto-configure hooks and wrappers with no manual setup",
    )
    group.add_argument(
        "--await-user",
        action="store_true",
        help="Open a bounded user-response window before autonomous fallback",
    )
    group.add_argument(
        "--user-responded",
        action="store_true",
        help="Register that user provided new information during await-user window",
    )
    group.add_argument(
        "--log-event",
        action="store_true",
        help="Append hook telemetry event details to the active session log",
    )
    group.add_argument(
        "--record-openclaw-route",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    group.add_argument(
        "--claim-execution-owner",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    group.add_argument(
        "--heartbeat-execution-owner",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    group.add_argument(
        "--release-execution-owner",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    group.add_argument(
        "--clear-stale-execution-owner",
        action="store_true",
        help=argparse.SUPPRESS,
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
        help="Duration in minutes or 'dynamic' (defaults to dynamic with --add)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable output for --status",
    )
    parser.add_argument(
        "-Q",
        "--question",
        metavar="QUESTION",
        type=str,
        help="Question text used with --await-user",
    )
    parser.add_argument(
        "--wait-minutes",
        metavar="MINUTES",
        type=str,
        help="User response window in minutes for --await-user (default: 2)",
    )
    parser.add_argument(
        "--response-note",
        metavar="NOTE",
        type=str,
        help="Free-form note captured with --user-responded",
    )
    parser.add_argument(
        "--decision-note",
        metavar="NOTE",
        type=str,
        help="Free-form note captured with --cancel-accept/--cancel-deny",
    )
    parser.add_argument(
        "--event",
        metavar="EVENT",
        type=str,
        help="Event identifier used with --log-event",
    )
    parser.add_argument(
        "--note",
        metavar="NOTE",
        type=str,
        help="Event note used with --log-event",
    )
    parser.add_argument(
        "--openclaw-agent-id",
        metavar="AGENT_ID",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--openclaw-session-key",
        metavar="SESSION_KEY",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--openclaw-session-id",
        metavar="SESSION_ID",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--openclaw-reply-channel",
        metavar="CHANNEL",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--openclaw-reply-to",
        metavar="TARGET",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--openclaw-reply-from",
        metavar="SOURCE",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--openclaw-reply-account",
        metavar="ACCOUNT",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--execution-owner-kind",
        metavar="KIND",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--execution-owner-label",
        metavar="LABEL",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--execution-owner-pid",
        metavar="PID",
        type=str,
        help=argparse.SUPPRESS,
    )
    return parser


def _run_install_script() -> None:
    script_dir = Path(__file__).resolve().parent
    install_sh = script_dir / "install.sh"
    install_ps1 = script_dir / "install.ps1"
    raw_timeout = os.environ.get("OMNI_AGENT_INSTALL_TIMEOUT", "900").strip()
    try:
        install_timeout = int(raw_timeout)
    except ValueError:
        install_timeout = 900
    if install_timeout <= 0:
        install_timeout = 900

    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell and install_ps1.exists():
            subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(install_ps1),
                ],
                check=True,
                timeout=install_timeout,
            )
            return

        bash_bin = shutil.which("bash")
        if bash_bin and install_sh.exists():
            subprocess.run(
                [bash_bin, str(install_sh)], check=True, timeout=install_timeout
            )
            return

        sys.exit(
            "error: no compatible installer found for Windows. "
            "Install PowerShell (pwsh/powershell) or Git Bash."
        )

    if not install_sh.exists():
        sys.exit(f"error: install.sh not found at {install_sh}")
    bash_bin = shutil.which("bash")
    if bash_bin is None:
        sys.exit("error: bash is required for --install on this platform")
    subprocess.run([bash_bin, str(install_sh)], check=True, timeout=install_timeout)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.json and not args.status:
        parser.error("--json is only supported with --status")

    if not (
        args.update
        or args.install
        or args.hook_stop
        or args.hook_precompact
        or args.require_active
        or args.cancel_accept
        or args.cancel_deny
        or args.revise_session
        or args.bootstrap
        or args.await_user
        or args.user_responded
        or args.log_event
        or args.record_openclaw_route
        or args.claim_execution_owner
        or args.heartbeat_execution_owner
        or args.release_execution_owner
        or args.clear_stale_execution_owner
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
        cmd_status(json_output=args.json)
        return

    if args.require_active:
        cmd_require_active()
        return

    if args.cancel:
        cmd_cancel()
        return

    if args.cancel_accept:
        cmd_cancel_accept(args.decision_note or "")
        return

    if args.cancel_deny:
        cmd_cancel_deny(args.decision_note or "")
        return

    if args.revise_session:
        cmd_revise_session(
            request=args.request,
            duration=args.duration,
            response_note=args.response_note or "",
        )
        cmd_status()
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

    if args.await_user:
        wait_minutes = args.wait_minutes if args.wait_minutes is not None else "2"
        cmd_await_user(args.question or "", wait_minutes)
        return

    if args.user_responded:
        cmd_user_responded(args.response_note or "")
        return

    if args.log_event:
        cmd_log_event(args.event or "", args.note or "")
        return

    if args.record_openclaw_route:
        cmd_record_openclaw_route(
            agent_id=args.openclaw_agent_id or "",
            session_key=args.openclaw_session_key or "",
            session_id=args.openclaw_session_id or "",
            channel=args.openclaw_reply_channel or "",
            reply_to=args.openclaw_reply_to or "",
            reply_from=args.openclaw_reply_from or "",
            account_id=args.openclaw_reply_account or "",
        )
        return

    owner_token = os.environ.get("OMNI_AGENT_OWNER_TOKEN", "")

    if args.claim_execution_owner:
        cmd_claim_execution_owner(
            owner_token,
            args.execution_owner_kind or "",
            args.execution_owner_label or "",
            args.execution_owner_pid or "",
        )
        return

    if args.heartbeat_execution_owner:
        cmd_heartbeat_execution_owner(owner_token)
        return

    if args.release_execution_owner:
        cmd_release_execution_owner(owner_token)
        return

    if args.clear_stale_execution_owner:
        cmd_clear_stale_execution_owner()
        return

    if args.dummy:
        cmd_dummy()
        return

    parser.error("no command selected")


if __name__ == "__main__":
    main()
