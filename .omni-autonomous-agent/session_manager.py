from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import tempfile
import hashlib
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
    OPENCLAW_ROUTE_CACHE_FILE,
    RED,
    SANDBOX_ROOT,
    SEP,
    STATE_FILE,
    YELLOW,
    c,
)


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
CANCEL_ACCEPT_TOKEN = "..."
CANCEL_DENY_TOKEN = ".."


def _resolve_template_dir() -> Path:
    override = os.environ.get("OMNI_AGENT_TEMPLATE_DIR", "").strip()
    if not override:
        return TEMPLATE_DIR
    return Path(override).expanduser()


class _SafeFormatMap(UserDict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _include_sensitive_context() -> bool:
    return _truthy_env("OMNI_AGENT_INCLUDE_SENSITIVE_CONTEXT")


def _text_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _display_sensitive_text(value: str, *, label: str) -> str:
    clean = value.strip()
    if not clean:
        return "(none)"
    if _include_sensitive_context():
        return clean
    return f"[{label}:{_text_fingerprint(clean)}]"


def _display_path(path: Path) -> str:
    if _include_sensitive_context():
        return str(path)
    return path.name or "(path-hidden)"


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

    if template_id == "stop-blocked-fixed":
        return (
            "[OAA STOP BLOCK - FIXED SESSION]\n"
            "Do not stop. Continue autonomous execution until the fixed deadline is reached.\n"
            "Request: {request}\n"
            "Deadline: {deadline}\n"
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

    if template_id == "user-timeout-continue":
        return (
            "[OAA USER RESPONSE TIMEOUT]\n"
            "No user response arrived within the configured waiting window.\n"
            "Request: {request}\n"
            "Time remaining: {time_remaining}\n"
            "Report status: {report_status}\n"
            "Sandbox: {sandbox_dir}\n"
            "Proceed with autonomous defaults and keep working until stop conditions are truly satisfied.\n"
        )

    return ""


def _render_template_text(raw: str, context: dict[str, str]) -> str:
    try:
        return raw.format_map(_SafeFormatMap(context))
    except ValueError:
        return ""


def render_template(template_id: str, context: dict[str, str]) -> str:
    template_path = _resolve_template_dir() / f"{template_id}.md"
    if template_path.exists():
        raw = template_path.read_text(encoding="utf-8")
    else:
        raw = _fallback_template(template_id)

    rendered = _render_template_text(raw, context)
    if rendered.strip():
        return rendered

    fallback_raw = _fallback_template(template_id)
    if fallback_raw and fallback_raw != raw:
        fallback_rendered = _render_template_text(fallback_raw, context)
        if fallback_rendered.strip():
            return fallback_rendered

    return rendered


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
    reason_slug = re.sub(r"[^A-Za-z0-9]+", "-", reason).strip("-").lower()[:24]
    if not reason_slug:
        reason_slug = "invalid"
    target = STATE_FILE.with_name(f"state.invalid.{suffix}.{reason_slug}.json")
    try:
        STATE_FILE.rename(target)
    except OSError:
        try:
            shutil.copy2(STATE_FILE, target)
            STATE_FILE.unlink()
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

    cancel_state = state.get("cancel_request_state")
    cancel_requested_at = state.get("cancel_requested_at")
    cancel_pause_until = state.get("cancel_pause_until")
    cancel_denied_at = state.get("cancel_denied_at")
    cancel_denied_note = state.get("cancel_denied_note")

    cancel_fields_present = any(
        value is not None
        for value in (
            cancel_state,
            cancel_requested_at,
            cancel_pause_until,
            cancel_denied_at,
            cancel_denied_note,
        )
    )

    if cancel_fields_present:
        if cancel_state not in {"pending", "denied"}:
            return False
        requested_dt = _parse_iso_datetime(cancel_requested_at)
        if requested_dt is None:
            return False

        pause_dt = None
        if cancel_pause_until is not None:
            pause_dt = _parse_iso_datetime(cancel_pause_until)
            if pause_dt is None:
                return False

        if cancel_state == "pending":
            if pause_dt is None:
                return False
            if pause_dt < requested_dt:
                return False
            if cancel_denied_at is not None:
                return False
            if cancel_denied_note is not None:
                return False
        else:
            denied_dt = _parse_iso_datetime(cancel_denied_at)
            if denied_dt is None:
                return False
            if denied_dt < requested_dt:
                return False
            if cancel_denied_note is not None and not isinstance(
                cancel_denied_note, str
            ):
                return False

    return True


def _normalize_state_fields(state: dict[str, Any]) -> bool:
    changed = False

    policy = str(state.get("update_policy", "")).strip().lower()
    if policy in {"milestones", "final-only"}:
        pass
    else:
        request = str(state.get("request", ""))
        duration_mode = str(state.get("duration_mode", "dynamic"))
        state["update_policy"] = _infer_update_policy(request, duration_mode)
        changed = True

    runtime_bindings = state.get("runtime_bindings")
    if runtime_bindings is not None and not isinstance(runtime_bindings, dict):
        state.pop("runtime_bindings", None)
        return True

    if isinstance(runtime_bindings, dict):
        normalized_bindings = dict(runtime_bindings)
        normalized_openclaw = _coerce_openclaw_binding(
            normalized_bindings.get("openclaw")
        )
        if normalized_openclaw is None:
            if "openclaw" in normalized_bindings:
                normalized_bindings.pop("openclaw", None)
                changed = True
        elif normalized_bindings.get("openclaw") != normalized_openclaw:
            normalized_bindings["openclaw"] = normalized_openclaw
            changed = True

        if normalized_bindings:
            if normalized_bindings != runtime_bindings:
                state["runtime_bindings"] = normalized_bindings
                changed = True
        else:
            state.pop("runtime_bindings", None)
            changed = True

    return changed


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

    if _normalize_state_fields(loaded):
        _save(loaded)

    return loaded, None


def _openclaw_route_cache_ttl_seconds() -> int:
    raw = os.environ.get("OMNI_AGENT_OPENCLAW_ROUTE_CACHE_TTL_SECONDS", "600").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 600
    if value <= 0:
        return 600
    return value


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _coerce_openclaw_binding(
    raw: Any, *, require_fresh: bool = False, now: datetime | None = None
) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None

    binding: dict[str, str] = {}
    for key in (
        "agent_id",
        "session_key",
        "session_id",
        "channel",
        "to",
        "from",
        "account_id",
    ):
        value = _clean_text(raw.get(key))
        if value:
            binding[key] = value

    if "session_id" not in binding:
        return None

    if "agent_id" not in binding:
        binding["agent_id"] = "main"

    updated_at_raw = _clean_text(raw.get("updated_at"))
    updated_at_dt = _parse_iso_datetime(updated_at_raw) if updated_at_raw else None
    if require_fresh:
        reference_now = now or _now()
        if updated_at_dt is None:
            return None
        if (reference_now - updated_at_dt).total_seconds() > _openclaw_route_cache_ttl_seconds():
            return None
    if updated_at_dt is not None:
        binding["updated_at"] = updated_at_dt.isoformat()

    return binding


def _load_openclaw_route_cache(now: datetime | None = None) -> dict[str, str] | None:
    if not OPENCLAW_ROUTE_CACHE_FILE.exists():
        return None
    try:
        raw = OPENCLAW_ROUTE_CACHE_FILE.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return _coerce_openclaw_binding(parsed, require_fresh=True, now=now)


def _save_openclaw_route_cache(binding: dict[str, str]) -> None:
    normalized = _coerce_openclaw_binding(binding)
    if normalized is None:
        return
    existing = _load_openclaw_route_cache()
    normalized = _merge_openclaw_binding(existing, normalized)
    OPENCLAW_ROUTE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(OPENCLAW_ROUTE_CACHE_FILE.parent),
    ) as temp_file:
        temp_file.write(payload)
        temp_path = Path(temp_file.name)
    temp_path.replace(OPENCLAW_ROUTE_CACHE_FILE)


def _clear_openclaw_route_cache() -> None:
    try:
        if OPENCLAW_ROUTE_CACHE_FILE.exists():
            OPENCLAW_ROUTE_CACHE_FILE.unlink()
    except OSError:
        return


def _read_openclaw_binding(state: dict[str, Any]) -> dict[str, str] | None:
    runtime_bindings = state.get("runtime_bindings")
    if not isinstance(runtime_bindings, dict):
        return None
    return _coerce_openclaw_binding(runtime_bindings.get("openclaw"))


def _merge_openclaw_binding(
    current: dict[str, str] | None, incoming: dict[str, str]
) -> dict[str, str]:
    if current is None:
        return incoming

    current_session_id = _clean_text(current.get("session_id"))
    incoming_session_id = _clean_text(incoming.get("session_id"))
    current_session_key = _clean_text(current.get("session_key"))
    incoming_session_key = _clean_text(incoming.get("session_key"))

    same_route = False
    if current_session_id and incoming_session_id and current_session_id == incoming_session_id:
        same_route = True
    elif (
        current_session_key
        and incoming_session_key
        and current_session_key == incoming_session_key
    ):
        same_route = True

    if not same_route:
        return incoming

    merged = dict(current)
    merged.update(incoming)
    normalized = _coerce_openclaw_binding(merged)
    return normalized if normalized is not None else incoming


def _set_openclaw_binding(state: dict[str, Any], binding: dict[str, str]) -> bool:
    normalized = _coerce_openclaw_binding(binding)
    if normalized is None:
        return False

    runtime_bindings_raw = state.get("runtime_bindings")
    runtime_bindings = (
        dict(runtime_bindings_raw) if isinstance(runtime_bindings_raw, dict) else {}
    )
    current = _coerce_openclaw_binding(runtime_bindings.get("openclaw"))
    merged = _merge_openclaw_binding(current, normalized)
    if current == merged:
        return False

    runtime_bindings["openclaw"] = merged
    state["runtime_bindings"] = runtime_bindings
    return True


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
                f"**📋 Request:** {_display_sensitive_text(str(state['request']), label='request')}",
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
                f"- Request: {_display_sensitive_text(str(state['request']), label='request')}",
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

    sandbox_raw = str(state.get("sandbox_dir", "")).strip()
    sandbox_path = Path(sandbox_raw) if sandbox_raw else Path("sandbox")
    report_path = _report_path(state)
    log_path = _log_path(state)

    return {
        "request": _display_sensitive_text(
            str(state.get("request", "")), label="request"
        ),
        "now": _fmt_dt(now),
        "deadline": deadline_text,
        "time_remaining": _fmt_remaining(snapshot["remaining_seconds"]),
        "elapsed": _fmt_elapsed(float(snapshot["elapsed_seconds"])),
        "sandbox_dir": _display_path(sandbox_path),
        "report_path": _display_path(report_path),
        "log_path": _display_path(log_path),
        "report_status": _read_report_status(state),
        "update_policy": str(state.get("update_policy", "milestones")),
    }


def _clear_await_user_fields(state: dict[str, Any]) -> None:
    state.pop("await_user_started_at", None)
    state.pop("await_user_deadline", None)
    state.pop("await_user_question", None)


def _await_user_deadline(state: dict[str, Any]) -> datetime | None:
    return _parse_iso_datetime(state.get("await_user_deadline"))


def _cancel_pause_seconds() -> int:
    return 30


def _clear_cancel_request_fields(state: dict[str, Any]) -> None:
    state.pop("cancel_request_state", None)
    state.pop("cancel_requested_at", None)
    state.pop("cancel_pause_until", None)
    state.pop("cancel_denied_at", None)
    state.pop("cancel_denied_note", None)


def _cancel_request_state(state: dict[str, Any]) -> str | None:
    raw = state.get("cancel_request_state")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if normalized in {"pending", "denied"}:
        return normalized
    return None


def _cancel_pause_deadline(state: dict[str, Any]) -> datetime | None:
    return _parse_iso_datetime(state.get("cancel_pause_until"))


def _stop_should_block(snapshot: dict[str, Any], report_status: str) -> bool:
    if snapshot["dynamic"]:
        return report_status not in {"COMPLETE", "PARTIAL"}
    return bool(snapshot["remaining_seconds"] and snapshot["remaining_seconds"] > 0)


def _cancel_instruction_text() -> str:
    return (
        f"Reply {CANCEL_ACCEPT_TOKEN!r} to accept cancellation or {CANCEL_DENY_TOKEN!r} to deny. "
        "CLI fallback: --cancel-accept / --cancel-deny."
    )


def _infer_update_policy(request: str, duration_mode: str) -> str:
    text = request.strip().lower()
    if not text:
        return "milestones"

    silence_markers = (
        "no update",
        "no updates",
        "no progress update",
        "no progress updates",
        "silent",
        "do not message",
        "don't message",
        "dont message",
        "only final",
        "final report only",
    )
    if any(marker in text for marker in silence_markers):
        return "final-only"

    if duration_mode == "fixed":
        if re.search(r"\buntil\b", text):
            return "final-only"
        if re.search(r"\b(?:by|till|til)\s+\d{1,2}(?::\d{2})?\b", text):
            return "final-only"

    return "milestones"


def _user_update_allowed(
    state: dict[str, Any], snapshot: dict[str, Any], report_status: str
) -> bool:
    policy = str(state.get("update_policy", "milestones")).strip().lower()
    if policy != "final-only":
        return True

    if snapshot["dynamic"]:
        return report_status in {"COMPLETE", "PARTIAL"}

    remaining = snapshot["remaining_seconds"]
    if remaining is None:
        return True
    return remaining <= 0


def _sanitize_decision_note(note: str) -> str:
    cleaned = note.strip()
    if not cleaned:
        return ""
    if _include_sensitive_context():
        return cleaned
    return _display_sensitive_text(cleaned, label="decision")


def _decision_note_for_output(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if re.fullmatch(r"\[decision:[0-9a-f]{12}\]", cleaned):
        return cleaned
    return _display_sensitive_text(cleaned, label="decision")


def _in_wrapper_hook_mode() -> bool:
    raw = os.environ.get("OMNI_AGENT_HOOK_WRAPPER", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _blocked_stop_exit_code(*, retry_immediately: bool) -> int:
    if _in_wrapper_hook_mode() and not retry_immediately:
        return 4
    return 2


def _pause_then_resume_exit_code() -> int:
    if _in_wrapper_hook_mode():
        return 5
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
            f"- Request: {_display_sensitive_text(str(state['request']), label='request')}",
            f"- Deadline: {deadline_text}",
            f"- Time remaining: {remaining_text}",
            f"- Sandbox: {_display_path(Path(str(state['sandbox_dir'])))}",
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


def _parse_duration_config(duration: str) -> tuple[str, str, int | None]:
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

    return duration_input, duration_mode, duration_minutes


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

        duration_input, duration_mode, duration_minutes = _parse_duration_config(
            duration
        )

        now = _now()
        deadline = (
            now + timedelta(minutes=duration_minutes)
            if duration_minutes is not None
            else None
        )
        inherited_openclaw_binding = _load_openclaw_route_cache(now)

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
            "update_policy": _infer_update_policy(request_clean, duration_mode),
        }
        if inherited_openclaw_binding is not None:
            state["runtime_bindings"] = {"openclaw": inherited_openclaw_binding}

        try:
            _save(state)
            if inherited_openclaw_binding is not None:
                _clear_openclaw_route_cache()
            _write_initial_report(state)
            _write_initial_log(state)
            _append_log(
                state,
                "Session registered",
                details=[
                    f"Duration mode: {duration_mode}",
                f"Deadline: {_fmt_dt(deadline) if deadline else 'dynamic'}",
                f"Sandbox: {sandbox}",
                f"User updates: {state.get('update_policy', 'milestones')}",
                (
                    "OpenClaw recovery route: cached route bound to active session."
                    if inherited_openclaw_binding is not None
                    else "OpenClaw recovery route: not bound."
                ),
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


def cmd_revise_session(
    *, request: str | None, duration: str | None, response_note: str
) -> None:
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
        if request is None and duration is None and not response_note.strip():
            sys.exit(
                "error: --revise-session requires -R/--request, -D/--duration, or --response-note."
            )

        now = _now()
        previous_request = str(state.get("request", "")).strip()
        previous_duration = str(state.get("duration_input", "dynamic")).strip()
        previous_deadline = _parse_iso_datetime(state.get("deadline"))

        request_clean = previous_request
        request_changed = False
        if request is not None:
            request_clean = request.strip()
            if not request_clean:
                sys.exit("error: request cannot be empty")
            request_changed = request_clean != previous_request
            state["request"] = request_clean

        duration_changed = False
        deadline = _parse_iso_datetime(state.get("deadline"))
        if duration is not None:
            duration_input, duration_mode, duration_minutes = _parse_duration_config(
                duration
            )
            deadline = (
                now + timedelta(minutes=duration_minutes)
                if duration_minutes is not None
                else None
            )
            duration_changed = duration_input != previous_duration
            state["duration_input"] = duration_input
            state["duration_mode"] = duration_mode
            state["duration_minutes"] = duration_minutes
            state["deadline"] = deadline.isoformat() if deadline else None
        else:
            duration_input = str(state.get("duration_input", previous_duration))

        update_policy = _infer_update_policy(
            request_clean, str(state.get("duration_mode", "dynamic"))
        )
        state["update_policy"] = update_policy

        had_wait_window = _await_user_deadline(state) is not None
        if had_wait_window:
            _clear_await_user_fields(state)

        cancel_state = _cancel_request_state(state)
        cleared_cancel_state = cancel_state in {"pending", "denied"}
        if cleared_cancel_state:
            _clear_cancel_request_fields(state)

        _save(state)

        details = []
        if request_changed:
            details.append(
                f"Request: {_display_sensitive_text(previous_request, label='request')} -> "
                f"{_display_sensitive_text(request_clean, label='request')}"
            )
        if duration_changed:
            previous_deadline_text = (
                _fmt_dt(previous_deadline) if previous_deadline else "dynamic"
            )
            next_deadline_text = _fmt_dt(deadline) if deadline else "dynamic"
            details.extend(
                [
                    f"Duration: {previous_duration or 'dynamic'} -> {duration_input or 'dynamic'}",
                    f"Deadline: {previous_deadline_text} -> {next_deadline_text}",
                ]
            )
        if not request_changed and not duration_changed:
            details.append("Request and duration unchanged.")
        if had_wait_window:
            details.append("Await-user window cleared.")
        if cleared_cancel_state:
            details.append("Pending cancellation state cleared due to direct user instruction.")
        note = response_note.strip()
        if note:
            details.append(
                f"Response note: {_display_sensitive_text(note, label='response')}"
            )
        details.append(f"User updates: {update_policy}")

        _append_log(state, "Session revised", details=details)
        _append_report_checkpoint(state, "session-revised", now)

        _header(f"{c(GREEN, 'OK')} Session revised")
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
        _row("Sandbox", str(state.get("sandbox_dir", "")))
        print(SEP)


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
                f"Question: {_display_sensitive_text(question, label='question')}",
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
            question=_display_sensitive_text(question, label="question"),
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
                    f"Response note: {_display_sensitive_text(note, label='response')}",
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
                response_note=_display_sensitive_text(note, label="response"),
            )
            return

        _append_log(
            state,
            "User response recorded without active wait window",
            details=[
                f"Response note: {_display_sensitive_text(note, label='response')}"
            ],
        )
        _emit_hook_payload(
            False,
            "No active user-response window. Continue autonomous execution.",
            hook="user-responded",
            active=True,
            waiting_for_user=False,
            user_response_registered=True,
            late_user_response=True,
            response_note=_display_sensitive_text(note, label="response"),
        )


def _normalize_hook_event_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip()).strip("-")
    if not cleaned:
        return ""
    return cleaned.lower()[:72]


def _normalize_hook_event_note(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) > 240:
        return collapsed[:240]
    return collapsed


def cmd_log_event(event: str, note: str) -> None:
    event_name = _normalize_hook_event_name(event)
    if not event_name:
        sys.exit("error: --log-event requires --event")

    note_text = _normalize_hook_event_note(note)

    with _state_lock():
        state, state_error = _load_with_error()
        if state_error or not state:
            return

        now = _now()
        details = [f"Event: {event_name}"]
        if note_text:
            details.append(f"Note: {note_text}")
        _append_log(state, "Hook telemetry", details=details)
        _append_report_checkpoint(state, f"hook:{event_name}", now)


def cmd_record_openclaw_route(
    *,
    agent_id: str,
    session_key: str,
    session_id: str,
    channel: str,
    reply_to: str,
    reply_from: str,
    account_id: str,
) -> None:
    binding = _coerce_openclaw_binding(
        {
            "agent_id": agent_id,
            "session_key": session_key,
            "session_id": session_id,
            "channel": channel,
            "to": reply_to,
            "from": reply_from,
            "account_id": account_id,
            "updated_at": _now().isoformat(),
        }
    )
    if binding is None:
        return

    try:
        _save_openclaw_route_cache(binding)
    except OSError:
        pass

    with _state_lock():
        state, state_error = _load_with_error()
        if state_error or not state:
            return
        if _set_openclaw_binding(state, binding):
            _save(state)
            _append_log(
                state,
                "OpenClaw recovery route updated",
                details=[
                    "Session key: "
                    + _display_sensitive_text(
                        binding.get("session_key", ""), label="openclaw-session"
                    ),
                    "Session id: "
                    + _display_sensitive_text(
                        binding.get("session_id", ""), label="openclaw-session-id"
                    ),
                    f"Channel: {binding.get('channel', '(unknown)')}",
                ],
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
        payload.update(
            {
                "ok": False,
                "active": False,
                "state_error": state_error,
                "session_registered": False,
                "lifecycle_state": "state_error",
                "closure_pending": False,
            }
        )
        return payload

    if not state:
        payload.update(
            {
                "ok": True,
                "active": False,
                "message": "No active session.",
                "session_registered": False,
                "lifecycle_state": "none",
                "closure_pending": False,
            }
        )
        return payload

    snapshot = _status_snapshot(state, now)
    await_deadline = _await_user_deadline(state)
    waiting_for_user = bool(await_deadline is not None and now < await_deadline)
    report_status = _read_report_status(state)
    closure_pending = bool(not snapshot["dynamic"] and not snapshot["active"])
    cancel_state = _cancel_request_state(state) or "none"
    cancel_requested_at = _parse_iso_datetime(state.get("cancel_requested_at"))
    cancel_pause_until = _cancel_pause_deadline(state)
    cancel_denied_at = _parse_iso_datetime(state.get("cancel_denied_at"))
    cancel_pause_remaining_seconds: float | None = None
    if cancel_state == "pending" and cancel_pause_until is not None:
        cancel_pause_remaining_seconds = max(
            0.0, (cancel_pause_until - now).total_seconds()
        )
    report_status_effective = report_status
    if closure_pending and report_status_effective not in {"COMPLETE", "PARTIAL"}:
        report_status_effective = "PARTIAL"
    openclaw_binding = _read_openclaw_binding(state)

    lifecycle_state = "active"
    if closure_pending:
        lifecycle_state = "deadline_reached_waiting_closure"

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
            "update_policy": str(state.get("update_policy", "milestones")),
            "report_status": report_status,
            "report_status_effective": report_status_effective,
            "session_registered": True,
            "lifecycle_state": lifecycle_state,
            "closure_pending": closure_pending,
            "waiting_for_user": waiting_for_user,
            "response_deadline": await_deadline.isoformat()
            if waiting_for_user and await_deadline is not None
            else None,
            "await_question": str(state.get("await_user_question", "") or "")
            if waiting_for_user
            else None,
            "sandbox_dir": str(state.get("sandbox_dir", "")),
            "cancel_request_state": cancel_state,
            "cancel_requested_at": cancel_requested_at.isoformat()
            if cancel_requested_at is not None
            else None,
            "cancel_pause_until": cancel_pause_until.isoformat()
            if cancel_state == "pending" and cancel_pause_until is not None
            else None,
            "cancel_pause_remaining_seconds": cancel_pause_remaining_seconds
            if cancel_state == "pending"
            else None,
            "cancel_denied_at": cancel_denied_at.isoformat()
            if cancel_state == "denied" and cancel_denied_at is not None
            else None,
            "cancel_denied_note": _decision_note_for_output(
                state.get("cancel_denied_note")
            )
            if cancel_state == "denied"
            else None,
            "log_checkpoints": _count_log_checkpoints(state),
            "required_log_checkpoints": _required_log_checkpoints(
                snapshot["elapsed_seconds"]
            ),
            "runtime_bindings": {"openclaw": openclaw_binding}
            if openclaw_binding is not None
            else None,
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

            _row("User updates", str(state.get("update_policy", "milestones")))
            report_status = _read_report_status(state)
            closure_pending = bool(not snapshot["dynamic"] and not snapshot["active"])
            report_display = report_status
            if closure_pending and report_status not in {"COMPLETE", "PARTIAL"}:
                report_display = f"{report_status} (closure pending)"

            _row("Duration", state["duration_input"])
            _row("Report status", report_display)
            if closure_pending:
                _row("Closure", c(YELLOW, "pending stop/cancel after fixed deadline"))

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

        cancel_state = _cancel_request_state(state)
        if not json_output and cancel_state == "pending":
            pause_until = _cancel_pause_deadline(state)
            _row("Cancel request", c(YELLOW, "pending user decision"))
            if pause_until is not None and now < pause_until:
                pause_remaining = (pause_until - now).total_seconds()
                _row("AI pause", _fmt_remaining(pause_remaining))
                _row("Pause until", _fmt_dt(pause_until))
            else:
                _row("AI pause", "elapsed; autonomous work resumed")
            _row(
                "User reply",
                f"{CANCEL_ACCEPT_TOKEN} (accept), {CANCEL_DENY_TOKEN} (deny)",
            )

        if not json_output and cancel_state == "denied":
            denied_at = _parse_iso_datetime(state.get("cancel_denied_at"))
            _row("Cancel request", c(YELLOW, "denied by user"))
            if denied_at is not None:
                _row("Denied at", _fmt_dt(denied_at))
            denied_note = _decision_note_for_output(state.get("cancel_denied_note"))
            if denied_note:
                _row("Decision note", denied_note)

        if json_output:
            print(json.dumps(_status_json_payload(state, None, now)))
            return

        _row("Log checkpoints", f"{log_count} (target >= {log_target})")
        _row("Sandbox", state["sandbox_dir"])
        print(SEP)


def _cancel_active_session(
    state: dict[str, Any], now: datetime, *, trigger: str
) -> tuple[Path | None, str | None]:
    _append_log(
        state,
        "Session cancelled",
        details=[f"Triggered by: {trigger}"],
    )
    archive_error: str | None = None
    archived: Path | None = None
    try:
        archived = _archive_sandbox(state, now)
    except RuntimeError as exc:
        archive_error = str(exc)
    _clear_openclaw_route_cache()
    _clear_state()
    return archived, archive_error


def cmd_cancel() -> None:
    with _state_lock():
        state, state_error = _load_with_error()
        if state_error:
            quarantined = _quarantine_state_file(state_error)
            if quarantined is not None:
                _clear_openclaw_route_cache()
                _header(f"{c(YELLOW, 'Session cancelled')}")
                _row("Cancelled at", _fmt_dt(_now()))
                _row("Reason", "State file was corrupted and quarantined")
                _row("Quarantined state", str(quarantined))
                print(SEP)
                return

            _header(f"{c(RED, 'Session cancellation failed')}")
            _row("Cancelled at", _fmt_dt(_now()))
            _row("Reason", "State file is corrupted and could not be quarantined")
            _row("Recovery", f"Manually move or delete {STATE_FILE} and retry --cancel")
            print(SEP)
            sys.exit(1)

        if not state:
            print("No active session to cancel.")
            return

        now = _now()
        report_status = _read_report_status(state)
        snapshot = _status_snapshot(state, now)
        should_block = _stop_should_block(snapshot, report_status)

        cancel_state = _cancel_request_state(state)
        pause_until = _cancel_pause_deadline(state)
        if cancel_state == "pending":
            _header("Cancellation request pending")
            _row(
                "Requested at",
                _fmt_dt(datetime.fromisoformat(state["cancel_requested_at"])),
            )
            if pause_until is not None and now < pause_until:
                _row("AI pause", _fmt_remaining((pause_until - now).total_seconds()))
                _row("Pause until", _fmt_dt(pause_until))
            else:
                _row("AI pause", "elapsed")
            _row("Decision", _cancel_instruction_text())
            print(SEP)
            return

        _clear_cancel_request_fields(state)
        pause_seconds = _cancel_pause_seconds()
        pause_until = now + timedelta(seconds=pause_seconds)
        state["cancel_request_state"] = "pending"
        state["cancel_requested_at"] = now.isoformat()
        state["cancel_pause_until"] = pause_until.isoformat()
        _save(state)

        _append_log(
            state,
            "Cancellation requested; awaiting explicit user decision",
            details=[
                f"AI pause window: {pause_seconds} second(s)",
                f"Pause until: {_fmt_dt(pause_until)}",
                f"Decision: {_cancel_instruction_text()}",
            ],
        )

        _header(f"{c(YELLOW, 'Cancellation request sent')}")
        _row("Requested at", _fmt_dt(now))
        _row("AI pause window", f"{pause_seconds} second(s)")
        _row("Pause until", _fmt_dt(pause_until))
        _row("Decision", _cancel_instruction_text())
        if should_block:
            _row(
                "Policy",
                "Autonomous stop remains blocked until user decision or normal stop conditions are met.",
            )
        print(SEP)


def cmd_cancel_accept(decision_note: str) -> None:
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
        if _cancel_request_state(state) != "pending":
            sys.exit("error: no pending cancellation request to accept")

        now = _now()
        note = decision_note.strip()
        _append_log(
            state,
            "Cancellation request accepted by user",
            details=[
                f"Decision note: {_display_sensitive_text(note, label='decision')}",
                f"Accept token: {CANCEL_ACCEPT_TOKEN}",
            ],
        )
        archived, archive_error = _cancel_active_session(
            state, now, trigger="--cancel-accept"
        )

        _header(f"{c(YELLOW, 'Session cancelled')}")
        _row("Cancelled at", _fmt_dt(now))
        _row("Decision", "User accepted cancellation request")
        if note:
            _row("Decision note", _display_sensitive_text(note, label="decision"))
        _row("Archived sandbox", str(archived) if archived else "(sandbox not found)")
        if archive_error:
            _row("Archive warning", c(RED, archive_error))
        print(SEP)


def cmd_cancel_deny(decision_note: str) -> None:
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
        if _cancel_request_state(state) != "pending":
            sys.exit("error: no pending cancellation request to deny")

        now = _now()
        note = decision_note.strip()
        stored_note = _sanitize_decision_note(note)
        state["cancel_request_state"] = "denied"
        state["cancel_denied_at"] = now.isoformat()
        state["cancel_denied_note"] = stored_note
        state.pop("cancel_pause_until", None)
        _save(state)

        _append_log(
            state,
            "Cancellation request denied by user",
            details=[
                f"Decision note: {stored_note or '(none)'}",
                "Stop remains blocked until normal stop conditions are met.",
            ],
        )

        _emit_hook_payload(
            False,
            "User denied cancellation request. Continue autonomous work until stop conditions are met.",
            hook="cancel-deny",
            active=True,
            cancellation_denied=True,
            waiting_for_cancel_decision=False,
            denied_at=_fmt_dt(now),
            decision_note=stored_note,
        )


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
            user_update_allowed = _user_update_allowed(state, snapshot, report_status)

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
                    pause_seconds = max(1, math.ceil(wait_remaining))
                    _append_log(
                        state,
                        "Stop attempt blocked by user response window",
                        details=[
                            f"Response deadline: {_fmt_dt(await_deadline)}",
                            f"Remaining: {remaining_text}",
                            f"Recheck in: {pause_seconds}s",
                            "Question: "
                            + _display_sensitive_text(
                                str(state.get("await_user_question", "")),
                                label="question",
                            ),
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
                        pause_then_resume_seconds=pause_seconds,
                        retry_immediately=False,
                        response_deadline=_fmt_dt(await_deadline),
                        template_id="stop-blocked",
                        template=template_text,
                        user_update_allowed=user_update_allowed,
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
                    user_update_allowed=user_update_allowed,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            should_block = _stop_should_block(snapshot, report_status)
            cancel_state = _cancel_request_state(state)

            if cancel_state == "pending" and should_block:
                pause_until = _cancel_pause_deadline(state)
                template_context = _hook_template_context(state, snapshot, now)
                template_text = render_template("stop-blocked", template_context)
                if pause_until is not None and now < pause_until:
                    pause_remaining = (pause_until - now).total_seconds()
                    remaining_text = _fmt_remaining(pause_remaining)
                    pause_seconds = max(1, math.ceil(pause_remaining))
                    _append_log(
                        state,
                        "Stop attempt blocked by pending cancellation request",
                        details=[
                            f"AI pause remaining: {remaining_text}",
                            f"Pause until: {_fmt_dt(pause_until)}",
                            f"Decision: {_cancel_instruction_text()}",
                        ],
                    )
                    _emit_hook_payload(
                        True,
                        (
                            "Cancellation request is pending user decision. "
                            f"Pause for {pause_seconds} seconds, then resume autonomous work if no decision arrives."
                        ),
                        hook="stop",
                        active=True,
                        block=True,
                        cancel_request_pending=True,
                        waiting_for_cancel_decision=True,
                        cancel_pause_deadline=_fmt_dt(pause_until),
                        cancel_pause_remaining=remaining_text,
                        cancel_decision_instructions=_cancel_instruction_text(),
                        pause_then_resume_seconds=pause_seconds,
                        retry_immediately=False,
                        template_id="stop-blocked",
                        template=template_text,
                        user_update_allowed=user_update_allowed,
                    )
                    sys.exit(_pause_then_resume_exit_code())

                _append_log(
                    state,
                    "Cancellation request still pending; pause window elapsed",
                    details=[
                        "No user decision received yet.",
                        "Autonomous work must continue until decision or normal stop conditions are met.",
                    ],
                )
                _emit_hook_payload(
                    True,
                    (
                        "Cancellation request is still pending user decision. "
                        "Resume autonomous work now; stop remains blocked until user decides or stop conditions are met."
                    ),
                    hook="stop",
                    active=True,
                    block=True,
                    cancel_request_pending=True,
                    waiting_for_cancel_decision=True,
                    cancel_pause_elapsed=True,
                    cancel_decision_instructions=_cancel_instruction_text(),
                    retry_immediately=True,
                    template_id="stop-blocked",
                    template=template_text,
                    user_update_allowed=user_update_allowed,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            if cancel_state == "denied" and should_block:
                template_context = _hook_template_context(state, snapshot, now)
                template_text = render_template("stop-blocked", template_context)
                denied_at = _parse_iso_datetime(state.get("cancel_denied_at"))
                _append_log(
                    state,
                    "Stop attempt blocked: user denied cancellation request",
                    details=[
                        f"Denied at: {_fmt_dt(denied_at) if denied_at is not None else 'unknown'}",
                        "Stop remains blocked until normal stop conditions are met.",
                    ],
                )
                _emit_hook_payload(
                    True,
                    "User denied cancellation request. Keep working until normal stop conditions are met.",
                    hook="stop",
                    active=True,
                    block=True,
                    cancel_request_denied=True,
                    waiting_for_cancel_decision=False,
                    cancel_denied_at=_fmt_dt(denied_at)
                    if denied_at is not None
                    else "unknown",
                    retry_immediately=True,
                    template_id="stop-blocked",
                    template=template_text,
                    user_update_allowed=user_update_allowed,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            if should_block:
                remaining_text = _fmt_remaining(snapshot["remaining_seconds"])
                template_context = _hook_template_context(state, snapshot, now)
                if snapshot["dynamic"]:
                    template_id = "stop-blocked"
                    template_text = render_template(template_id, template_context)
                    message = (
                        "Autonomous session is still active. Keep working. "
                        f"Time remaining: {remaining_text}. "
                        "For dynamic sessions, set report status to COMPLETE or PARTIAL before stopping."
                    )
                else:
                    template_id = "stop-blocked-fixed"
                    template_text = render_template(template_id, template_context)
                    message = (
                        "Autonomous fixed session is still active. Keep working until the fixed deadline is reached. "
                        f"Time remaining: {remaining_text}."
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
                    template_id=template_id,
                    template=template_text,
                    user_update_allowed=user_update_allowed,
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
                    user_update_allowed=user_update_allowed,
                )
                sys.exit(_blocked_stop_exit_code(retry_immediately=True))

            _clear_openclaw_route_cache()
            _clear_state()

            _emit_hook_payload(
                False,
                "Stop allowed. Session closed and sandbox archived.",
                hook="stop",
                active=False,
                block=False,
                archived_sandbox=str(archived) if archived else None,
                user_update_allowed=True,
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
