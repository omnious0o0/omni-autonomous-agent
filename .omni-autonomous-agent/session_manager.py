from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from collections import UserDict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

from .constants import (
    ARCHIVE_ROOT,
    BOLD,
    DIM,
    GREEN,
    RED,
    SANDBOX_ROOT,
    SEP,
    STATE_FILE,
    YELLOW,
    c,
)


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class _SafeFormatMap(UserDict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _fallback_template(template_id: str) -> str:
    if template_id == "stop-blocked":
        return (
            "[OAA STOP BLOCK]\n"
            "Do not stop. Continue autonomous execution now.\n"
            "Request: {request}\n"
            "Time remaining: {time_remaining}\n"
            "Report status: {report_status}\n"
            "Sandbox: {sandbox_dir}\n"
        )

    if template_id == "precompact-handoff":
        return (
            "[OAA PRECOMPACT]\n"
            "Prepare deep handoff for next model now.\n"
            "Include: completed work, failed attempts, file changes, open risks, and exact next actions.\n"
            "Request: {request}\n"
            "Elapsed: {elapsed}\n"
            "Report: {report_path}\n"
            "Log: {log_path}\n"
        )

    return ""


def render_template(template_id: str, context: dict[str, str]) -> str:
    template_path = TEMPLATE_DIR / f"{template_id}.md"
    if template_path.exists():
        raw = template_path.read_text(encoding="utf-8")
    else:
        raw = _fallback_template(template_id)
    return raw.format_map(_SafeFormatMap(context))


def _now() -> datetime:
    return datetime.now().astimezone()


def _save(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=str(STATE_FILE.parent)
    ) as temp_file:
        temp_file.write(payload)
        temp_path = Path(temp_file.name)
    temp_path.replace(STATE_FILE)


def _quarantine_state_file(reason: str) -> Path | None:
    if not STATE_FILE.exists():
        return None
    suffix = _now().strftime("%Y%m%d-%H%M%S")
    target = STATE_FILE.with_name(f"state.invalid.{suffix}.json")
    try:
        STATE_FILE.rename(target)
    except OSError:
        return None
    return target


@contextmanager
def _state_lock() -> Any:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_FILE.with_name("state.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return

        if msvcrt is not None:
            lock_fn = getattr(msvcrt, "locking", None)
            lk_lock = getattr(msvcrt, "LK_LOCK", None)
            lk_unlock = getattr(msvcrt, "LK_UNLCK", None)
            if callable(lock_fn) and lk_lock is not None and lk_unlock is not None:
                lock_fn(lock_file.fileno(), lk_lock, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    lock_fn(lock_file.fileno(), lk_unlock, 1)
                return

        yield


def _is_path_inside(root: Path, candidate: Path) -> bool:
    root_resolved = root.resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)
    return (
        candidate_resolved == root_resolved
        or root_resolved in candidate_resolved.parents
    )


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _state_is_valid(state: dict[str, Any]) -> bool:
    required = {
        "request",
        "duration_input",
        "duration_mode",
        "started_at",
        "sandbox_dir",
    }
    if not required.issubset(state.keys()):
        return False

    request = state.get("request")
    if not isinstance(request, str) or not request.strip():
        return False

    duration_input = state.get("duration_input")
    if not isinstance(duration_input, str) or not duration_input.strip():
        return False

    sandbox_dir = state.get("sandbox_dir")
    if not isinstance(sandbox_dir, str) or not sandbox_dir.strip():
        return False

    sandbox_path = Path(sandbox_dir).expanduser().resolve(strict=False)
    sandbox_root = SANDBOX_ROOT.resolve(strict=False)
    if not _is_path_inside(sandbox_root, sandbox_path):
        return False
    if sandbox_path == sandbox_root:
        return False

    if state.get("duration_mode") not in {"fixed", "dynamic"}:
        return False

    duration_minutes = state.get("duration_minutes")
    deadline = state.get("deadline")

    started_dt = _parse_iso_datetime(state.get("started_at"))
    if started_dt is None:
        return False

    if state.get("duration_mode") == "fixed":
        if not isinstance(duration_minutes, int) or duration_minutes <= 0:
            return False
        deadline_dt = _parse_iso_datetime(deadline)
        if deadline_dt is None:
            return False
    else:
        if duration_minutes is not None:
            return False
        if deadline is not None:
            return False

    await_started = state.get("await_user_started_at")
    await_deadline = state.get("await_user_deadline")
    await_question = state.get("await_user_question")

    if any(
        value is not None for value in (await_started, await_deadline, await_question)
    ):
        started_dt = _parse_iso_datetime(await_started)
        deadline_dt = _parse_iso_datetime(await_deadline)
        if started_dt is None or deadline_dt is None:
            return False
        if deadline_dt < started_dt:
            return False
        if await_question is not None and not isinstance(await_question, str):
            return False

    return True


def _load_with_error() -> tuple[dict[str, Any] | None, str | None]:
    if not STATE_FILE.exists():
        return None, None

    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"failed to read state file: {exc}"

    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"state file is corrupted JSON: {exc}"

    if not isinstance(loaded, dict):
        return None, "state file is invalid: expected a JSON object"

    if not _state_is_valid(loaded):
        return None, "state file is invalid: missing required fields"

    return loaded, None


def _clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def _header(title: str) -> None:
    print(SEP)
    print(f"  {c(BOLD, title)}")
    print(SEP)


def _row(label: str, value: str) -> None:
    print(f"  {c(DIM, label + ':'):<20} {value}")


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%a %b %d %Y %H:%M:%S %Z")


def _fmt_remaining(seconds: float | None) -> str:
    if seconds is None:
        return "dynamic"
    if seconds <= 0:
        return c(RED, "past deadline")
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    text = " ".join(parts)
    return c(YELLOW, text) if seconds < 300 else text


def _fmt_elapsed(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _slugify(text: str, max_len: int = 56) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    if not slug:
        slug = "task"
    if len(slug) <= max_len:
        return slug
    shortened = slug[:max_len].strip("-")
    return shortened or "task"


def _task_title(request: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{_slugify(request, max_len=40)}"


def _unique_sandbox_dir(task_title: str) -> Path:
    base = SANDBOX_ROOT / task_title
    if not base.exists():
        return base

    index = 1
    while True:
        candidate = SANDBOX_ROOT / f"{task_title}-{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


def _report_path(state: dict[str, Any]) -> Path:
    return Path(state["sandbox_dir"]) / "REPORT.md"


def _log_path(state: dict[str, Any]) -> Path:
    return Path(state["sandbox_dir"]) / "LOG.md"


def _write_initial_report(state: dict[str, Any]) -> None:
    report = _report_path(state)
    if report.exists():
        return
    started = _fmt_dt(datetime.fromisoformat(state["started_at"]))
    duration_text = (
        "dynamic"
        if state["duration_mode"] == "dynamic"
        else f"{state['duration_minutes']} minutes"
    )
    report.write_text(
        "\n".join(
            [
                "## 🤖 Autonomous Session Report",
                "",
                f"**📋 Request:** {state['request']}",
                f"**⏱️ Duration:** {duration_text} (started {started})",
                "**🕐 Completed at:** in progress",
                "",
                "### ✅ What was done",
                "- Session registered and workspace bootstrapped.",
                "",
                "### 📦 Outputs",
                "- Initialized REPORT.md",
                "- Initialized LOG.md",
                "",
                "### 🚦 Status",
                "IN_PROGRESS",
                "",
                "### ⚠️ Blockers / Notes",
                "- None currently.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_initial_log(state: dict[str, Any]) -> None:
    log = _log_path(state)
    if log.exists():
        return
    started = _fmt_dt(datetime.fromisoformat(state["started_at"]))
    log.write_text(
        "\n".join(
            [
                "# Autonomous Session Log",
                "",
                f"- Request: {state['request']}",
                f"- Started: {started}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _append_log(
    state: dict[str, Any], title: str, details: list[str] | None = None
) -> None:
    log = _log_path(state)
    log.parent.mkdir(parents=True, exist_ok=True)
    now_text = _fmt_dt(_now())
    lines = [f"## {now_text}", f"- {title}"]
    if details:
        lines.extend([f"- {detail}" for detail in details])
    with log.open("a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")


def _hook_template_context(
    state: dict[str, Any], snapshot: dict[str, Any], now: datetime
) -> dict[str, str]:
    deadline_text = (
        "dynamic" if snapshot["deadline"] is None else _fmt_dt(snapshot["deadline"])
    )
    return {
        "request": str(state.get("request", "")),
        "now": _fmt_dt(now),
        "deadline": deadline_text,
        "time_remaining": _fmt_remaining(snapshot["remaining_seconds"]),
        "elapsed": _fmt_elapsed(float(snapshot["elapsed_seconds"])),
        "sandbox_dir": str(state.get("sandbox_dir", "")),
        "report_path": str(_report_path(state)),
        "log_path": str(_log_path(state)),
        "report_status": _read_report_status(state),
    }


def _clear_await_user_fields(state: dict[str, Any]) -> None:
    state.pop("await_user_started_at", None)
    state.pop("await_user_deadline", None)
    state.pop("await_user_question", None)


def _await_user_deadline(state: dict[str, Any]) -> datetime | None:
    return _parse_iso_datetime(state.get("await_user_deadline"))


def _in_wrapper_hook_mode() -> bool:
    raw = os.environ.get("OMNI_AGENT_HOOK_WRAPPER", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _blocked_stop_exit_code(*, retry_immediately: bool) -> int:
    if _in_wrapper_hook_mode() and not retry_immediately:
        return 4
    return 2


def _parse_positive_minutes(raw: str, flag: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        sys.exit(f"error: {flag} must be a positive integer in minutes")
    if value <= 0:
        sys.exit(f"error: {flag} must be a positive integer in minutes")
    return value


def _status_snapshot(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    started = datetime.fromisoformat(state["started_at"])
    dynamic = state.get("duration_mode") == "dynamic"
    if dynamic:
        deadline = None
        remaining = None
        active = True
    else:
        deadline = datetime.fromisoformat(state["deadline"])
        remaining = (deadline - now).total_seconds()
        active = remaining > 0

    elapsed = max(0.0, (now - started).total_seconds())
    return {
        "started": started,
        "deadline": deadline,
        "remaining_seconds": remaining,
        "elapsed_seconds": elapsed,
        "dynamic": dynamic,
        "active": active,
    }


def _read_report_status(state: dict[str, Any]) -> str:
    report = _report_path(state)
    if not report.exists():
        return "UNKNOWN"
    try:
        text = report.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "UNKNOWN"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("###") and "status" in line.lower():
            for look_ahead in lines[index + 1 :]:
                stripped = look_ahead.strip()
                if not stripped:
                    continue
                match = re.search(
                    r"\b(COMPLETE|PARTIAL|IN_PROGRESS)\b", stripped, flags=re.IGNORECASE
                )
                if match:
                    return match.group(1).upper()
                break
            break
    return "UNKNOWN"


def _count_log_checkpoints(state: dict[str, Any]) -> int:
    log = _log_path(state)
    if not log.exists():
        return 0
    count = 0
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return 0
    for line in lines:
        if line.startswith("## "):
            count += 1
    return count


def _required_log_checkpoints(elapsed_seconds: float) -> int:
    hours = elapsed_seconds / 3600.0
    target = int(hours * 2)
    if hours > 0 and target == 0:
        target = 1
    return target


def _finalize_report(state: dict[str, Any], now: datetime) -> None:
    report = _report_path(state)
    if not report.exists():
        _write_initial_report(state)

    lines = report.read_text(encoding="utf-8").splitlines()
    started_at = datetime.fromisoformat(str(state["started_at"]))
    elapsed_seconds = (now - started_at).total_seconds()
    elapsed_text = _fmt_elapsed(elapsed_seconds)
    duration_line = f"**⏱️ Duration:** {elapsed_text}"
    completed_line = f"**🕐 Completed at:** {_fmt_dt(now)}"

    duration_line_index = -1
    completed_line_index = -1
    for index, line in enumerate(lines):
        if line.startswith("**⏱️ Duration:**"):
            duration_line_index = index
        if line.startswith("**🕐 Completed at:**"):
            completed_line_index = index

    if duration_line_index >= 0:
        lines[duration_line_index] = duration_line

    if completed_line_index >= 0:
        lines[completed_line_index] = completed_line
    elif duration_line_index >= 0:
        lines.insert(duration_line_index + 1, completed_line)
    else:
        lines.insert(0, completed_line)

    status_value = _read_report_status(state)
    if status_value not in {"COMPLETE", "PARTIAL"}:
        status_value = "PARTIAL"

    log_count = _count_log_checkpoints(state)
    log_target = _required_log_checkpoints(elapsed_seconds)
    cadence_note = (
        f"- Logging cadence below target ({log_count}/{log_target}); marked PARTIAL."
    )
    if status_value == "COMPLETE" and log_count < log_target:
        status_value = "PARTIAL"

    status_heading_index = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("###") and "status" in line.lower():
            status_heading_index = index
            break

    if status_heading_index >= 0:
        replaced = False
        for index in range(status_heading_index + 1, len(lines)):
            stripped = lines[index].strip()
            if not stripped:
                continue
            if stripped.startswith("###"):
                lines.insert(index, status_value)
                replaced = True
                break
            lines[index] = status_value
            replaced = True
            break
        if not replaced:
            lines.append(status_value)
    else:
        lines.extend(["", "### 🚦 Status", status_value])

    if status_value == "PARTIAL" and log_count < log_target:
        has_note = any(line.strip() == cadence_note for line in lines)
        if not has_note:
            blockers_index = -1
            for index, line in enumerate(lines):
                if line.strip().startswith("###") and "blockers" in line.lower():
                    blockers_index = index
                    break
            if blockers_index >= 0:
                insert_at = blockers_index + 1
                while insert_at < len(lines) and lines[insert_at].strip() == "":
                    insert_at += 1
                lines.insert(insert_at, cadence_note)
            else:
                lines.extend(["", "### ⚠️ Blockers / Notes", cadence_note])

    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_report_checkpoint(
    state: dict[str, Any], trigger: str, now: datetime
) -> None:
    snapshot = _status_snapshot(state, now)
    deadline_text = _fmt_dt(snapshot["deadline"]) if snapshot["deadline"] else "dynamic"
    remaining_text = _fmt_remaining(snapshot["remaining_seconds"])
    report = _report_path(state)
    report.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = "\n".join(
        [
            "",
            f"### 🔄 Checkpoint ({trigger}) - {_fmt_dt(now)}",
            f"- Request: {state['request']}",
            f"- Deadline: {deadline_text}",
            f"- Time remaining: {remaining_text}",
            f"- Sandbox: {state['sandbox_dir']}",
            "",
        ]
    )
    with report.open("a", encoding="utf-8") as f:
        f.write(checkpoint)


def _archive_sandbox(state: dict[str, Any], when: datetime) -> Path | None:
    source = Path(state["sandbox_dir"]).expanduser()
    if not source.exists():
        return None

    if source.is_symlink():
        raise RuntimeError(
            f"sandbox path is a symlink and cannot be archived safely: {source}"
        )

    sandbox_root = SANDBOX_ROOT.resolve()
    source_resolved = source.resolve()
    if not _is_path_inside(sandbox_root, source_resolved):
        raise RuntimeError(
            f"sandbox path escapes sandbox root and cannot be archived: {source_resolved}"
        )
    if source_resolved == sandbox_root:
        raise RuntimeError("sandbox root cannot be archived as a session directory")

    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    archive_root = ARCHIVE_ROOT.resolve()
    target = archive_root / source_resolved.name
    if target.exists():
        suffix = when.strftime("%Y%m%d-%H%M%S")
        target = archive_root / f"{source_resolved.name}-{suffix}"

    if not _is_path_inside(archive_root, target):
        raise RuntimeError(f"archive target escapes archive root: {target}")

    try:
        shutil.move(str(source_resolved), str(target))
    except OSError as exc:
        raise RuntimeError(
            f"failed to archive sandbox {source_resolved} -> {target}: {exc}"
        ) from exc
    return target


def _emit_hook_payload(continue_work: bool, message: str, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "continue": continue_work,
        "message": message,
    }
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False))


def cmd_add(request: str, duration: str) -> None:
    with _state_lock():
        request_clean = request.strip()
        if not request_clean:
            sys.exit("error: request cannot be empty")

        existing_state, existing_error = _load_with_error()
        if existing_error:
            sys.exit(
                f"error: {existing_error}. Run 'omni-autonomous-agent --cancel' to reset state safely."
            )
        if existing_state is not None:
            sys.exit(
                "error: an active session already exists. Use --status or --cancel first."
            )

        duration_input = (duration or "dynamic").strip()
        duration_mode = "dynamic"
        duration_minutes: int | None = None

        if duration_input.lower() != "dynamic":
            try:
                duration_minutes = int(duration_input)
            except ValueError:
                sys.exit(
                    "error: duration must be a positive integer in minutes, or 'dynamic'"
                )
            if duration_minutes <= 0:
                sys.exit("error: duration must be a positive integer in minutes")
            duration_mode = "fixed"

        now = _now()
        deadline = (
            now + timedelta(minutes=duration_minutes)
            if duration_minutes is not None
            else None
        )

        SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

        title = _task_title(request_clean, now)
        sandbox = _unique_sandbox_dir(title)
        sandbox.mkdir(parents=True, exist_ok=False)

        state: dict[str, Any] = {
            "version": 1,
            "request": request_clean,
            "duration_input": duration_input,
            "duration_mode": duration_mode,
            "duration_minutes": duration_minutes,
            "started_at": now.isoformat(),
            "deadline": deadline.isoformat() if deadline else None,
            "task_title": title,
            "sandbox_dir": str(sandbox),
            "status": "active",
        }

        try:
            _save(state)
            _write_initial_report(state)
            _write_initial_log(state)
            _append_log(
                state,
                "Session registered",
                details=[
                    f"Duration mode: {duration_mode}",
                    f"Deadline: {_fmt_dt(deadline) if deadline else 'dynamic'}",
                    f"Sandbox: {sandbox}",
                ],
            )
        except Exception as exc:
            _clear_state()
            shutil.rmtree(sandbox, ignore_errors=True)
            sys.exit(f"error: failed to initialize session workspace: {exc}")

        _header(f"{c(GREEN, 'OK')} Session registered")
        _row("Request", request_clean)
        _row("Now", _fmt_dt(now))
        _row(
            "Deadline",
            _fmt_dt(deadline) if deadline else c(YELLOW, "dynamic (no fixed deadline)"),
        )
        _row(
            "Time remaining",
            _fmt_remaining((deadline - now).total_seconds()) if deadline else "dynamic",
        )
        _row("Sandbox", str(sandbox))
        _row("Report", str(_report_path(state)))
        _row("Log", str(_log_path(state)))
        print(SEP)


def cmd_dummy() -> None:
    cmd_add("Dummy autonomous session", "60")


def cmd_await_user(question: str, wait_minutes: str) -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            sys.exit(
                "error: state is invalid. run 'omni-autonomous-agent --cancel' to reset."
            )
        if not state:
            sys.exit(
                "error: no active session. run 'omni-autonomous-agent --add -R \"<request>\" -D <minutes|dynamic>' first."
            )

        minutes = _parse_positive_minutes(wait_minutes.strip(), "--wait-minutes")
        now = _now()
        snapshot = _status_snapshot(state, now)
        if not snapshot["dynamic"] and not snapshot["active"]:
            sys.exit(
                "error: session deadline already passed; --await-user cannot extend a fixed session. Conclude or register a new session."
            )
        deadline = now + timedelta(minutes=minutes)

        state["await_user_started_at"] = now.isoformat()
        state["await_user_deadline"] = deadline.isoformat()
        state["await_user_question"] = question.strip()
        _save(state)

        _append_log(
            state,
            "User response window opened",
            details=[
                f"Window: {minutes} minute(s)",
                f"Deadline: {_fmt_dt(deadline)}",
                f"Question: {question.strip() or '(none)'}",
            ],
        )

        _emit_hook_payload(
            False,
            "Waiting for user response within configured window.",
            hook="await-user",
            active=True,
            block=True,
            waiting_for_user=True,
            wait_minutes=minutes,
            response_deadline=_fmt_dt(deadline),
            question=question.strip(),
        )


def cmd_user_responded(response_note: str) -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            sys.exit(
                "error: state is invalid. run 'omni-autonomous-agent --cancel' to reset."
            )
        if not state:
            sys.exit(
                "error: no active session. run 'omni-autonomous-agent --add -R \"<request>\" -D <minutes|dynamic>' first."
            )

        note = response_note.strip()
        had_wait_window = _await_user_deadline(state) is not None
        if had_wait_window:
            _clear_await_user_fields(state)
            _save(state)
            _append_log(
                state,
                "User response registered",
                details=[
                    f"Response note: {note or '(none)'}",
                    "Await-user window cleared.",
                ],
            )
            _emit_hook_payload(
                False,
                "User response registered. Continue autonomous work with new information.",
                hook="user-responded",
                active=True,
                waiting_for_user=False,
                user_response_registered=True,
                response_note=note,
            )
            return

        _append_log(
            state,
            "User response recorded without active wait window",
            details=[f"Response note: {note or '(none)'}"],
        )
        _emit_hook_payload(
            False,
            "No active user-response window. Continue autonomous execution.",
            hook="user-responded",
            active=True,
            waiting_for_user=False,
            user_response_registered=False,
            response_note=note,
        )


def cmd_require_active() -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            sys.exit(
                "error: state is invalid. run 'omni-autonomous-agent --cancel' to reset."
            )

        if not state:
            sys.exit(
                "error: no active session. run 'omni-autonomous-agent --add -R \"<request>\" -D <minutes|dynamic>' first."
            )

        snapshot = _status_snapshot(state, _now())
        if not snapshot["dynamic"] and not snapshot["active"]:
            sys.exit(
                "error: session deadline already passed. register a new session with --add."
            )

        print("active")


def _status_json_payload(
    state: dict[str, Any] | None, state_error: str | None, now: datetime
) -> dict[str, Any]:
    payload: dict[str, Any] = {"timestamp": now.isoformat()}

    if state_error:
        payload.update({"ok": False, "active": False, "state_error": state_error})
        return payload

    if not state:
        payload.update({"ok": True, "active": False, "message": "No active session."})
        return payload

    snapshot = _status_snapshot(state, now)
    await_deadline = _await_user_deadline(state)
    waiting_for_user = bool(await_deadline is not None and now < await_deadline)

    payload.update(
        {
            "ok": True,
            "active": bool(snapshot["active"]),
            "dynamic": bool(snapshot["dynamic"]),
            "request": str(state.get("request", "")),
            "started_at": snapshot["started"].isoformat(),
            "deadline": snapshot["deadline"].isoformat()
            if snapshot["deadline"] is not None
            else None,
            "time_remaining_seconds": snapshot["remaining_seconds"],
            "duration_input": str(state.get("duration_input", "")),
            "report_status": _read_report_status(state),
            "waiting_for_user": waiting_for_user,
            "response_deadline": await_deadline.isoformat()
            if waiting_for_user and await_deadline is not None
            else None,
            "await_question": str(state.get("await_user_question", "") or "")
            if waiting_for_user
            else None,
            "sandbox_dir": str(state.get("sandbox_dir", "")),
            "log_checkpoints": _count_log_checkpoints(state),
            "required_log_checkpoints": _required_log_checkpoints(
                snapshot["elapsed_seconds"]
            ),
        }
    )
    return payload


def cmd_status(*, json_output: bool = False) -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        now = _now()

        if state_error:
            if json_output:
                print(json.dumps(_status_json_payload(None, state_error, now)))
                return
            _header("omni-autonomous-agent")
            _row("State error", c(RED, state_error))
            _row(
                "Recovery",
                "Run 'omni-autonomous-agent --cancel' to reset corrupted state",
            )
            print(SEP)
            return

        if not state:
            if json_output:
                print(json.dumps(_status_json_payload(None, None, now)))
                return
            _header("omni-autonomous-agent")
            print("  No active session.")
            print(SEP)
            return

        snapshot = _status_snapshot(state, now)
        log_count = _count_log_checkpoints(state)
        log_target = _required_log_checkpoints(snapshot["elapsed_seconds"])

        if not json_output:
            active_label = "active" if snapshot["active"] else "deadline reached"
            color = GREEN if snapshot["active"] else RED
            _header(f"omni-autonomous-agent - {c(color, active_label)}")

            _row("Request", state["request"])
            _row("Current date/time", _fmt_dt(now))
            _row("Started", _fmt_dt(snapshot["started"]))

            if snapshot["dynamic"]:
                _row("Deadline", c(YELLOW, "dynamic (no fixed deadline)"))
                _row("Time remaining", "dynamic")
            else:
                _row("Deadline", _fmt_dt(snapshot["deadline"]))
                _row("Time remaining", _fmt_remaining(snapshot["remaining_seconds"]))

            _row("Duration", state["duration_input"])
            _row("Report status", _read_report_status(state))

        await_deadline = _await_user_deadline(state)
        if await_deadline is not None:
            if not snapshot["dynamic"] and not snapshot["active"]:
                _clear_await_user_fields(state)
                _save(state)
                _append_log(
                    state,
                    "Await-user window cleared at fixed-session deadline",
                    details=[
                        "Fixed deadline reached before user response window closed.",
                        "Stop is now governed by fixed-session completion rules.",
                    ],
                )
                await_deadline = None

        if await_deadline is not None:
            await_question = str(state.get("await_user_question", "") or "(none)")
            if now < await_deadline:
                await_remaining = (await_deadline - now).total_seconds()
                if not json_output:
                    _row("User response", c(YELLOW, "waiting"))
                    _row("Await question", await_question)
                    _row("Response deadline", _fmt_dt(await_deadline))
                    _row("Wait remaining", _fmt_remaining(await_remaining))
            else:
                _clear_await_user_fields(state)
                _save(state)
                _append_log(
                    state,
                    "User response window expired",
                    details=[
                        "No response received in configured window.",
                        "Proceeding with autonomous defaults.",
                    ],
                )
                if not json_output:
                    _row("User response", c(YELLOW, "window expired"))
                    _row("Await question", await_question)
                    _row("Autonomous mode", "proceeding with defaults")

        if json_output:
            print(json.dumps(_status_json_payload(state, None, now)))
            return

        _row("Log checkpoints", f"{log_count} (target >= {log_target})")
        _row("Sandbox", state["sandbox_dir"])
        print(SEP)


def cmd_cancel() -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            quarantined = _quarantine_state_file(state_error)
            _header(f"{c(YELLOW, 'Session cancelled')}")
            _row("Cancelled at", _fmt_dt(_now()))
            _row("Reason", "State file was corrupted and quarantined")
            _row("Quarantined state", str(quarantined) if quarantined else "(none)")
            print(SEP)
            return

        if not state:
            print("No active session to cancel.")
            return

        now = _now()
        _append_log(
            state,
            "Session cancelled via kill-switch",
            details=["Triggered by: --cancel"],
        )
        archive_error: str | None = None
        archived: Path | None = None
        try:
            archived = _archive_sandbox(state, now)
        except RuntimeError as exc:
            archive_error = str(exc)
        _clear_state()

        _header(f"{c(YELLOW, 'Session cancelled')}")
        _row("Cancelled at", _fmt_dt(now))
        _row("Request", state["request"])
        _row("Archived sandbox", str(archived) if archived else "(sandbox not found)")
        if archive_error:
            _row("Archive warning", c(RED, archive_error))
        print(SEP)


def cmd_hook_precompact() -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            _emit_hook_payload(
                True,
                f"State error blocks safe precompact handling: {state_error}",
                hook="precompact",
                active=True,
                block=True,
            )
            sys.exit(2)

        if not state:
            _emit_hook_payload(
                False, "No active session.", hook="precompact", active=False
            )
            return

        try:
            now = _now()
            snapshot = _status_snapshot(state, now)
            template_context = _hook_template_context(state, snapshot, now)
            template_text = render_template("precompact-handoff", template_context)
            _append_report_checkpoint(state, "precompact", now)
            _append_log(
                state,
                "Precompact checkpoint written",
                details=[
                    f"Report: {_report_path(state)}",
                    f"Trigger time: {_fmt_dt(now)}",
                ],
            )
            _emit_hook_payload(
                False,
                "Checkpoint written to REPORT.md",
                hook="precompact",
                active=True,
                report=str(_report_path(state)),
                log=str(_log_path(state)),
                template_id="precompact-handoff",
                template=template_text,
            )
        except Exception as exc:
            _emit_hook_payload(
                True,
                f"Precompact hook failed and continuation is required: {exc}",
                hook="precompact",
                active=True,
                block=True,
            )
            sys.exit(2)


def cmd_hook_stop() -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            template_text = render_template(
                "stop-blocked",
                {
                    "request": "unknown",
                    "now": _fmt_dt(_now()),
                    "deadline": "unknown",
                    "time_remaining": "unknown",
                    "report_status": "UNKNOWN",
                    "sandbox_dir": "unknown",
                },
            )
            _emit_hook_payload(
                True,
                (
                    "State file is corrupted and stop is blocked to avoid premature exit. "
                    "Run 'omni-autonomous-agent --cancel' to recover safely. "
                    f"Details: {state_error}"
                ),
                hook="stop",
                active=True,
                block=True,
                retry_immediately=False,
                state_corrupted=True,
                template_id="stop-blocked",
                template=template_text,
            )
            sys.exit(_blocked_stop_exit_code(retry_immediately=False))

        if not state:
            _emit_hook_payload(
                False, "No active session.", hook="stop", active=False, block=False
            )
            return

        try:
            now = _now()
            snapshot = _status_snapshot(state, now)
            report_status = _read_report_status(state)

            await_deadline = _await_user_deadline(state)
            if await_deadline is not None:
                if not snapshot["dynamic"] and not snapshot["active"]:
                    _clear_await_user_fields(state)
                    _save(state)
                    _append_log(
                        state,
                        "Await-user window cleared at fixed-session deadline",
                        details=[
                            "Fixed deadline reached before user response window closed.",
                            "Stop is now governed by fixed-session completion rules.",
                        ],
                    )
                    await_deadline = None

            if await_deadline is not None:
                if now < await_deadline:
                    wait_remaining = (await_deadline - now).total_seconds()
                    remaining_text = _fmt_remaining(wait_remaining)
                    template_context = _hook_template_context(state, snapshot, now)
                    template_text = render_template("stop-blocked", template_context)
                    _append_log(
                        state,
                        "Stop attempt blocked by user response window",
                        details=[
                            f"Response deadline: {_fmt_dt(await_deadline)}",
                            f"Remaining: {remaining_text}",
                            f"Question: {state.get('await_user_question', '') or '(none)'}",
                        ],
                    )
                    _emit_hook_payload(
                        True,
                        (
                            "Waiting for user response window to close. "
                            f"Time remaining: {remaining_text}."
                        ),
                        hook="stop",
                        active=True,
                        block=True,
                        waiting_for_user=True,
                        retry_immediately=False,
                        response_deadline=_fmt_dt(await_deadline),
                        template_id="stop-blocked",
                        template=template_text,
                    )
                    sys.exit(_blocked_stop_exit_code(retry_immediately=False))

                template_context = _hook_template_context(state, snapshot, now)
                template_text = render_template(
                    "user-timeout-continue", template_context
                )
                _clear_await_user_fields(state)
                _save(state)
                _append_log(
                    state,
                    "User response window expired",
                    details=[
                        "No response received in configured window.",
                        "Proceeding with autonomous defaults.",
                    ],
                )
                _emit_hook_payload(
                    True,
                    "User did not respond within configured window. Proceeding autonomously with available information.",
                    hook="stop",
                    active=True,
                    block=True,
                    user_response_timed_out=True,
                    retry_immediately=True,
                    template_id="user-timeout-continue",
                    template=template_text,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            if snapshot["dynamic"]:
                should_block = report_status not in {"COMPLETE", "PARTIAL"}
            else:
                should_block = bool(
                    snapshot["remaining_seconds"] and snapshot["remaining_seconds"] > 0
                )

            if should_block:
                remaining_text = _fmt_remaining(snapshot["remaining_seconds"])
                template_context = _hook_template_context(state, snapshot, now)
                template_text = render_template("stop-blocked", template_context)
                message = (
                    "Autonomous session is still active. Keep working. "
                    f"Time remaining: {remaining_text}. "
                    "For dynamic sessions, set report status to COMPLETE or PARTIAL before stopping."
                )
                _append_log(
                    state,
                    "Stop attempt blocked",
                    details=[
                        f"Remaining: {remaining_text}",
                        f"Report status: {report_status}",
                    ],
                )
                _emit_hook_payload(
                    True,
                    message,
                    hook="stop",
                    active=True,
                    block=True,
                    retry_immediately=True,
                    template_id="stop-blocked",
                    template=template_text,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            _append_log(
                state,
                "Stop hook allowed session closure",
                details=[
                    f"Report status: {report_status}",
                    f"Closed at: {_fmt_dt(now)}",
                ],
            )
            _finalize_report(state, now)
            _append_report_checkpoint(state, "stop-allow", now)

            try:
                archived = _archive_sandbox(state, now)
            except RuntimeError as exc:
                _append_log(
                    state,
                    "Stop hook failed while archiving sandbox",
                    details=[str(exc)],
                )
                template_context = _hook_template_context(state, snapshot, now)
                template_text = render_template("stop-blocked", template_context)
                _emit_hook_payload(
                    True,
                    (
                        "Session cannot close yet because sandbox archiving failed. "
                        "Please continue until archive succeeds. "
                        f"Details: {exc}"
                    ),
                    hook="stop",
                    active=True,
                    block=True,
                    retry_immediately=True,
                    template_id="stop-blocked",
                    template=template_text,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            _clear_state()

            _emit_hook_payload(
                False,
                "Stop allowed. Session closed and sandbox archived.",
                hook="stop",
                active=False,
                block=False,
                archived_sandbox=str(archived) if archived else None,
            )
        except Exception as exc:
            template_text = render_template(
                "stop-blocked",
                {
                    "request": str(state.get("request", "unknown")),
                    "now": _fmt_dt(_now()),
                    "deadline": "unknown",
                    "time_remaining": "unknown",
                    "report_status": _read_report_status(state),
                    "sandbox_dir": str(state.get("sandbox_dir", "unknown")),
                },
            )
            _emit_hook_payload(
                True,
                f"Stop hook failed and continuation is required: {exc}",
                hook="stop",
                active=True,
                block=True,
                retry_immediately=False,
                template_id="stop-blocked",
                template=template_text,
            )
            sys.exit(_blocked_stop_exit_code(retry_immediately=False))
