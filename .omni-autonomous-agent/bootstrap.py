from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .constants import BOLD, DIM, GREEN, SEP, YELLOW, c


KNOWN_WRAPPER_AGENTS: dict[str, str] = {
    "codex": "codex",
    "aider": "aider",
    "goose": "goose",
    "plandex": "plandex",
    "amp": "amp",
    "crush": "crush",
    "kiro": "kiro",
    "roo": "roo",
    "cline": "cline",
}


def _header(title: str) -> None:
    print(SEP)
    print(f"  {c(BOLD, title)}")
    print(SEP)


def _row(label: str, value: str) -> None:
    print(f"  {c(DIM, label + ':'):<20} {value}")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        token = uuid.uuid4().hex
        invalid_backup = path.with_name(f"{path.name}.invalid.{token}")
        try:
            path.rename(invalid_backup)
        except OSError:
            pass
        return {}
    if not isinstance(loaded, dict):
        token = uuid.uuid4().hex
        invalid_backup = path.with_name(f"{path.name}.invalid.{token}")
        try:
            path.rename(invalid_backup)
        except OSError:
            pass
        return {}
    return loaded


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2) + "\n"
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=str(path.parent)
    ) as temp_file:
        temp_file.write(payload)
        temp_path = Path(temp_file.name)

    temp_path.replace(path)


def _ensure_list(mapping: dict[str, Any], key: str) -> list[Any]:
    current = mapping.get(key)
    if isinstance(current, list):
        return current
    mapping[key] = []
    return mapping[key]


def _has_nested_command(entries: list[Any], command: str) -> bool:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def _has_named_command(entries: list[Any], command: str) -> bool:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def _configure_claude() -> tuple[bool, Path]:
    settings_path = Path.home() / ".claude" / "settings.json"
    config = _load_json(settings_path)

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        config["hooks"] = hooks

    changed = False
    stop_entries = _ensure_list(hooks, "Stop")
    stop_command = "omni-autonomous-agent --hook-stop"
    if not _has_nested_command(stop_entries, stop_command):
        stop_entries.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": stop_command,
                    }
                ]
            }
        )
        changed = True

    precompact_entries = _ensure_list(hooks, "PreCompact")
    precompact_command = "omni-autonomous-agent --hook-precompact"
    if not _has_nested_command(precompact_entries, precompact_command):
        precompact_entries.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": precompact_command,
                    }
                ]
            }
        )
        changed = True

    if changed:
        _write_json(settings_path, config)
    return changed, settings_path


def _configure_gemini() -> tuple[bool, Path]:
    settings_path = Path.home() / ".gemini" / "settings.json"
    config = _load_json(settings_path)

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        config["hooks"] = hooks

    changed = False
    after_agent = _ensure_list(hooks, "AfterAgent")
    stop_command = "omni-autonomous-agent --hook-stop"
    if not _has_named_command(after_agent, stop_command):
        after_agent.append(
            {
                "hooks": [
                    {
                        "name": "omni-stop-hook",
                        "type": "command",
                        "command": stop_command,
                        "description": "Block premature stop if deadline not reached",
                    }
                ]
            }
        )
        changed = True

    precompress = _ensure_list(hooks, "PreCompress")
    precompact_command = "omni-autonomous-agent --hook-precompact"
    if not _has_named_command(precompress, precompact_command):
        precompress.append(
            {
                "hooks": [
                    {
                        "name": "omni-precompact-hook",
                        "type": "command",
                        "command": precompact_command,
                        "description": "Write handoff to REPORT.md before context compression",
                    }
                ]
            }
        )
        changed = True

    if changed:
        _write_json(settings_path, config)
    return changed, settings_path


def _opencode_plugin_content() -> str:
    return """import type { Plugin } from \"@opencode-ai/plugin\";
import { execSync } from \"child_process\";

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export const OmniHook: Plugin = async () => {
  return {
    \"session.idle\": async () => {
      try {
        execSync(\"omni-autonomous-agent --hook-stop\", { stdio: \"pipe\" });
      } catch (error: unknown) {
        console.error(\"[omni] hook-stop blocked idle:\", errorMessage(error));
      }
    },
    \"experimental.session.compacting\": async () => {
      try {
        execSync(\"omni-autonomous-agent --hook-precompact\", { stdio: \"pipe\" });
      } catch (error: unknown) {
        console.error(\"[omni] precompact hook error:\", errorMessage(error));
      }
    },
  };
};

export default OmniHook;
"""


def _configure_opencode() -> tuple[bool, Path]:
    plugin_path = Path.home() / ".config" / "opencode" / "plugins" / "omni-hook.ts"
    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    desired = _opencode_plugin_content()
    current = plugin_path.read_text(encoding="utf-8") if plugin_path.exists() else None
    changed = current != desired
    if changed:
        plugin_path.write_text(desired, encoding="utf-8")
    return changed, plugin_path


def _configure_universal_wrapper() -> tuple[bool, Path]:
    wrapper_path = Path.home() / ".local" / "bin" / "omni-agent-wrap"
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    desired = """#!/usr/bin/env bash
set -euo pipefail

if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then
  printf '[omni] no active session. run omni-autonomous-agent --add first.\n' >&2
  exit 3
fi

while true; do
  set +e
  "$@"
  cmd_status=$?
  set -e

  set +e
  omni-autonomous-agent --hook-stop >/dev/null 2>&1
  hook_status=$?
  set -e

  if [[ "$hook_status" -eq 2 ]]; then
    continue
  fi

  if [[ "$hook_status" -eq 0 ]]; then
    exit "$cmd_status"
  fi

  printf '[omni] hook-stop failed with code %s.\n' "$hook_status" >&2
  exit "$hook_status"
done
"""
    current = (
        wrapper_path.read_text(encoding="utf-8") if wrapper_path.exists() else None
    )
    changed = current != desired
    if changed:
        wrapper_path.write_text(desired, encoding="utf-8")
    mode = wrapper_path.stat().st_mode
    wrapper_path.chmod(mode | 0o111)
    return changed, wrapper_path


def _configure_specific_wrapper(
    wrapper_name: str, agent_command: str
) -> tuple[bool, Path]:
    wrapper_path = Path.home() / ".local" / "bin" / f"omni-wrap-{wrapper_name}"
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    desired = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "\n"
        "if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then\n"
        "  printf '[omni] no active session. run omni-autonomous-agent --add first.\\n' >&2\n"
        "  exit 3\n"
        "fi\n"
        "\n"
        "while true; do\n"
        "  set +e\n"
        f'  {agent_command} "$@"\n'
        "  cmd_status=$?\n"
        "  set -e\n"
        "\n"
        "  set +e\n"
        "  omni-autonomous-agent --hook-stop >/dev/null 2>&1\n"
        "  hook_status=$?\n"
        "  set -e\n"
        "\n"
        '  if [[ "$hook_status" -eq 2 ]]; then\n'
        "    continue\n"
        "  fi\n"
        "\n"
        '  if [[ "$hook_status" -eq 0 ]]; then\n'
        '    exit "$cmd_status"\n'
        "  fi\n"
        "\n"
        "  printf '[omni] hook-stop failed with code %s.\\n' \"$hook_status\" >&2\n"
        '  exit "$hook_status"\n'
        "done\n"
    )
    current = (
        wrapper_path.read_text(encoding="utf-8") if wrapper_path.exists() else None
    )
    changed = current != desired
    if changed:
        wrapper_path.write_text(desired, encoding="utf-8")
    mode = wrapper_path.stat().st_mode
    wrapper_path.chmod(mode | 0o111)
    return changed, wrapper_path


def _sanitize_wrapper_name(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    return cleaned or "agent"


def _is_safe_wrapper_command(command: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._/-]+", command))


def _wrapper_candidates(env_agent: str) -> dict[str, str]:
    candidates: dict[str, str] = dict(KNOWN_WRAPPER_AGENTS)

    env_value = env_agent.strip().lower()
    if env_value:
        env_bin = env_value.split()[0]
        if env_bin and any(ch.isalpha() for ch in env_bin):
            candidates[_sanitize_wrapper_name(env_bin)] = env_bin

    extra_raw = os.environ.get("OMNI_AGENT_EXTRA_WRAPPERS", "")
    for token in [item.strip() for item in extra_raw.split(",") if item.strip()]:
        candidates[_sanitize_wrapper_name(token)] = token

    return candidates


def _forced_wrapper_names() -> set[str]:
    forced: set[str] = set()
    extra_raw = os.environ.get("OMNI_AGENT_EXTRA_WRAPPERS", "")
    for token in [item.strip() for item in extra_raw.split(",") if item.strip()]:
        forced.add(_sanitize_wrapper_name(token))
    return forced


def _openclaw_hook_md() -> str:
    return """---
name: omni-recovery
description: \"Re-orients the agent after gateway startup so autonomous sessions can resume\"
metadata:
  openclaw:
    emoji: \"🔁\"
    events: [\"gateway:startup\"]
---
# omni-recovery
Runs omni-autonomous-agent --status on every gateway startup and injects the result
so the agent knows if a task was in progress and can resume.
"""


def _openclaw_handler_ts() -> str:
    return """import { execSync } from 'child_process';

const handler = async (event: any) => {
  if (event.type !== 'gateway' || event.action !== 'startup') return;

  try {
    const status = execSync('omni-autonomous-agent --status', { stdio: 'pipe' }).toString().trim();
    if (status) {
      event.messages.push(`[omni] Task state on gateway startup:\n${status}`);
    }
  } catch {
    return;
  }
};

export default handler;
"""


def _configure_openclaw() -> tuple[bool, Path]:
    hook_dir = Path.home() / ".openclaw" / "hooks" / "omni-recovery"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_md = hook_dir / "HOOK.md"
    handler_ts = hook_dir / "handler.ts"

    changed = False
    md_content = _openclaw_hook_md()
    handler_content = _openclaw_handler_ts()

    current_md = hook_md.read_text(encoding="utf-8") if hook_md.exists() else None
    if current_md != md_content:
        hook_md.write_text(md_content, encoding="utf-8")
        changed = True

    current_handler = (
        handler_ts.read_text(encoding="utf-8") if handler_ts.exists() else None
    )
    if current_handler != handler_content:
        handler_ts.write_text(handler_content, encoding="utf-8")
        changed = True

    commands = [
        ["openclaw", "hooks", "enable", "omni-recovery"],
        ["openclaw", "hooks", "enable", "session-memory"],
    ]
    failures: list[str] = []
    for command in commands:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            command_text = " ".join(command)
            details = (result.stderr or result.stdout or "command failed").strip()
            failures.append(f"{command_text}: {details}")

    if failures:
        raise RuntimeError("; ".join(failures))

    return changed, hook_dir


def _safe_apply(
    label: str,
    apply_fn: Callable[[], tuple[bool, Path]],
    configured: list[str],
    warnings: list[str],
) -> bool:
    try:
        changed, path = apply_fn()
        configured.append(
            f"{label} {'updated' if changed else 'already set'} at {path}"
        )
        return True
    except Exception as exc:
        warnings.append(f"{label} failed: {exc}")
        return False


def cmd_bootstrap() -> None:
    _header("Autonomous bootstrap")

    hook_capable = {
        "claude": shutil.which("claude") is not None,
        "gemini": shutil.which("gemini") is not None,
        "opencode": shutil.which("opencode") is not None,
        "openclaw": shutil.which("openclaw") is not None,
    }

    env_agent = os.environ.get("AGENT", "").strip()
    _row("Detected AGENT", env_agent or "(not set)")

    env_agent_token = env_agent.lower().split()[0] if env_agent.strip() else ""
    forced_wrappers = _forced_wrapper_names()
    wrapper_targets: dict[str, str] = {}
    skipped_wrappers: list[str] = []
    for wrapper_name, wrapper_cmd in _wrapper_candidates(env_agent).items():
        if not _is_safe_wrapper_command(wrapper_cmd):
            skipped_wrappers.append(wrapper_cmd)
            continue
        if (
            shutil.which(wrapper_cmd) is not None
            or wrapper_cmd == env_agent_token
            or wrapper_name in forced_wrappers
        ):
            wrapper_targets[wrapper_name] = wrapper_cmd

    configured: list[str] = []
    warnings: list[str] = []
    failed_targets: list[str] = []

    if hook_capable["claude"]:
        if not _safe_apply("Claude hooks", _configure_claude, configured, warnings):
            failed_targets.append("Claude hooks")

    if hook_capable["gemini"]:
        if not _safe_apply("Gemini hooks", _configure_gemini, configured, warnings):
            failed_targets.append("Gemini hooks")

    if hook_capable["opencode"]:
        if not _safe_apply(
            "OpenCode plugin", _configure_opencode, configured, warnings
        ):
            failed_targets.append("OpenCode plugin")

    if hook_capable["openclaw"]:
        if not _safe_apply("OpenClaw hooks", _configure_openclaw, configured, warnings):
            failed_targets.append("OpenClaw hooks")

    for wrapper_name, wrapper_cmd in wrapper_targets.items():
        if not _safe_apply(
            f"{wrapper_name} wrapper",
            lambda name=wrapper_name, cmd=wrapper_cmd: _configure_specific_wrapper(
                name, cmd
            ),
            configured,
            warnings,
        ):
            failed_targets.append(f"{wrapper_name} wrapper")

    if not _safe_apply(
        "Universal wrapper", _configure_universal_wrapper, configured, warnings
    ):
        failed_targets.append("Universal wrapper")

    if not any(hook_capable.values()) and not wrapper_targets and not env_agent:
        _row("Warning", c(YELLOW, "No supported native-hook agent binary detected"))

    for item in configured:
        _row("Bootstrap", item)

    for warning in warnings:
        _row("Warning", c(YELLOW, warning))

    for skipped in skipped_wrappers:
        _row("Warning", c(YELLOW, f"Skipped unsafe wrapper command token: {skipped}"))

    _row("Next", "Run 'omni-autonomous-agent --status' to verify CLI availability")
    print(SEP)

    if failed_targets:
        raise SystemExit(2)
