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

OPENCLAW_PLUGIN_ID = "omni-autonomous-agent"


def _is_windows() -> bool:
    return os.name == "nt"


def _wrapper_bin_dir() -> Path:
    override = os.environ.get("OMNI_AGENT_WRAPPER_BIN", "").strip()
    if override:
        return Path(override).expanduser()

    if _is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data).expanduser() / "omni-autonomous-agent" / "bin"
        return Path.home() / "AppData" / "Local" / "omni-autonomous-agent" / "bin"

    return Path.home() / ".local" / "bin"


def _wrapper_filename(base_name: str) -> str:
    if _is_windows():
        return f"{base_name}.cmd"
    return base_name


def _path_override(env_name: str, default_path: Path) -> Path:
    override = os.environ.get(env_name, "").strip()
    if override:
        return Path(override).expanduser()
    return default_path


def _default_opencode_plugin_path() -> Path:
    config_dir = os.environ.get("OPENCODE_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "plugins" / "omni-hook.ts"
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        return Path(config_home).expanduser() / "opencode" / "plugins" / "omni-hook.ts"
    return Path.home() / ".config" / "opencode" / "plugins" / "omni-hook.ts"


def _openclaw_plugin_dir() -> Path:
    return _path_override(
        "OMNI_AGENT_OPENCLAW_PLUGIN_DIR",
        Path(__file__).resolve().parent / "openclaw-plugin",
    )


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


def _has_hook_command(entries: list[Any], command: str) -> bool:
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
    settings_path = _path_override(
        "OMNI_AGENT_CLAUDE_SETTINGS", Path.home() / ".claude" / "settings.json"
    )
    config = _load_json(settings_path)

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        config["hooks"] = hooks

    changed = False
    stop_entries = _ensure_list(hooks, "Stop")
    stop_command = "omni-autonomous-agent --hook-stop"
    if not _has_hook_command(stop_entries, stop_command):
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
    if not _has_hook_command(precompact_entries, precompact_command):
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
    settings_path = _path_override(
        "OMNI_AGENT_GEMINI_SETTINGS", Path.home() / ".gemini" / "settings.json"
    )
    config = _load_json(settings_path)

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        config["hooks"] = hooks

    changed = False
    after_agent = _ensure_list(hooks, "AfterAgent")
    stop_command = "omni-autonomous-agent --hook-stop"
    if not _has_hook_command(after_agent, stop_command):
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
    if not _has_hook_command(precompress, precompact_command):
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
import { execFileSync } from \"child_process\";

function toText(value: unknown): string {
  if (typeof value === \"string\") {
    return value.trim();
  }
  if (value instanceof Buffer) {
    return value.toString(\"utf-8\").trim();
  }
  return \"\";
}

function commandOutput(error: unknown): string {
  if (typeof error === \"object\" && error !== null) {
    const details = error as { stdout?: Buffer | string; stderr?: Buffer | string };
    const output = [toText(details.stdout), toText(details.stderr)].filter(Boolean).join(\"\\n\");
    if (output) {
      return output;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function runHook(args: string[]): void {
  try {
    execFileSync(\"omni-autonomous-agent\", args, {
      stdio: [\"ignore\", \"pipe\", \"pipe\"],
      encoding: \"utf-8\",
    });
  } catch (error: unknown) {
    throw new Error(commandOutput(error));
  }
}

export const OmniHook: Plugin = async () => {
  return {
    \"session.idle\": async () => {
      runHook([\"--hook-stop\"]);
    },
    \"experimental.session.compacting\": async () => {
      runHook([\"--hook-precompact\"]);
    },
  };
};

export default OmniHook;
"""


def _configure_opencode() -> tuple[bool, Path]:
    plugin_path = _path_override(
        "OMNI_AGENT_OPENCODE_PLUGIN", _default_opencode_plugin_path()
    )
    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    desired = _opencode_plugin_content()
    current = plugin_path.read_text(encoding="utf-8") if plugin_path.exists() else None
    changed = current != desired
    if changed:
        plugin_path.write_text(desired, encoding="utf-8")
    return changed, plugin_path


def _universal_wrapper_script() -> str:
    if _is_windows():
        return """@echo off
setlocal

omni-autonomous-agent --require-active >nul 2>&1
if errorlevel 1 (
  >&2 echo [omni] no active session. run omni-autonomous-agent --add first.
  exit /b 3
)

:loop
omni-autonomous-agent --require-active >nul 2>&1
if errorlevel 1 exit /b 0

%*
set CMD_STATUS=%ERRORLEVEL%

set OMNI_AGENT_HOOK_WRAPPER=1
omni-autonomous-agent --hook-stop
set HOOK_STATUS=%ERRORLEVEL%
set OMNI_AGENT_HOOK_WRAPPER=

if "%HOOK_STATUS%"=="2" goto loop
if "%HOOK_STATUS%"=="5" (
  timeout /t 30 /nobreak >nul
  omni-autonomous-agent --require-active >nul 2>&1
  if errorlevel 1 exit /b %CMD_STATUS%
  goto loop
)
if "%HOOK_STATUS%"=="4" exit /b %HOOK_STATUS%
if "%HOOK_STATUS%"=="0" exit /b %CMD_STATUS%

>&2 echo [omni] hook-stop failed with code %HOOK_STATUS%.
exit /b %HOOK_STATUS%
"""

    return """#!/usr/bin/env bash
set -euo pipefail

if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then
  printf '[omni] no active session. run omni-autonomous-agent --add first.\n' >&2
  exit 3
fi

while true; do
  if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then
    exit 0
  fi

  set +e
  "$@"
  cmd_status=$?
  set -e

  set +e
  hook_output="$(OMNI_AGENT_HOOK_WRAPPER=1 omni-autonomous-agent --hook-stop 2>&1)"
  hook_status=$?
  set -e

  if [[ "$hook_status" -eq 2 ]]; then
    if [[ -n "$hook_output" ]]; then
      printf '%s\n' "$hook_output" >&2
    fi
    continue
  fi

  if [[ "$hook_status" -eq 5 ]]; then
    if [[ -n "$hook_output" ]]; then
      printf '%s\n' "$hook_output" >&2
    fi
    sleep 30
    if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then
      exit "$cmd_status"
    fi
    continue
  fi

  if [[ "$hook_status" -eq 4 ]]; then
    if [[ -n "$hook_output" ]]; then
      printf '%s\n' "$hook_output" >&2
    fi
    exit "$hook_status"
  fi

  if [[ "$hook_status" -eq 0 ]]; then
    exit "$cmd_status"
  fi

  if [[ -n "$hook_output" ]]; then
    printf '%s\n' "$hook_output" >&2
  fi
  printf '[omni] hook-stop failed with code %s.\n' "$hook_status" >&2
  exit "$hook_status"
done
"""


def _specific_wrapper_script(agent_command: str) -> str:
    if _is_windows():
        return (
            "@echo off\n"
            "setlocal\n"
            "\n"
            "omni-autonomous-agent --require-active >nul 2>&1\n"
            "if errorlevel 1 (\n"
            "  >&2 echo [omni] no active session. run omni-autonomous-agent --add first.\n"
            "  exit /b 3\n"
            ")\n"
            "\n"
            ":loop\n"
            "omni-autonomous-agent --require-active >nul 2>&1\n"
            "if errorlevel 1 exit /b 0\n"
            "\n"
            f"{agent_command} %*\n"
            "set CMD_STATUS=%ERRORLEVEL%\n"
            "\n"
            "set OMNI_AGENT_HOOK_WRAPPER=1\n"
            "omni-autonomous-agent --hook-stop\n"
            "set HOOK_STATUS=%ERRORLEVEL%\n"
            "set OMNI_AGENT_HOOK_WRAPPER=\n"
            "\n"
            'if "%HOOK_STATUS%"=="2" goto loop\n'
            'if "%HOOK_STATUS%"=="5" (\n'
            "  timeout /t 30 /nobreak >nul\n"
            "  omni-autonomous-agent --require-active >nul 2>&1\n"
            "  if errorlevel 1 exit /b %CMD_STATUS%\n"
            "  goto loop\n"
            ")\n"
            'if "%HOOK_STATUS%"=="4" exit /b %HOOK_STATUS%\n'
            'if "%HOOK_STATUS%"=="0" exit /b %CMD_STATUS%\n'
            "\n"
            ">&2 echo [omni] hook-stop failed with code %HOOK_STATUS%.\n"
            "exit /b %HOOK_STATUS%\n"
        )

    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "\n"
        "if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then\n"
        "  printf '[omni] no active session. run omni-autonomous-agent --add first.\\n' >&2\n"
        "  exit 3\n"
        "fi\n"
        "\n"
        "while true; do\n"
        "  if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then\n"
        "    exit 0\n"
        "  fi\n"
        "\n"
        "  set +e\n"
        f'  {agent_command} "$@"\n'
        "  cmd_status=$?\n"
        "  set -e\n"
        "\n"
        "  set +e\n"
        '  hook_output="$(OMNI_AGENT_HOOK_WRAPPER=1 omni-autonomous-agent --hook-stop 2>&1)"\n'
        "  hook_status=$?\n"
        "  set -e\n"
        "\n"
        '  if [[ "$hook_status" -eq 2 ]]; then\n'
        '    if [[ -n "$hook_output" ]]; then\n'
        '      printf "%s\\n" "$hook_output" >&2\n'
        "    fi\n"
        "    continue\n"
        "  fi\n"
        "\n"
        '  if [[ "$hook_status" -eq 5 ]]; then\n'
        '    if [[ -n "$hook_output" ]]; then\n'
        '      printf "%s\\n" "$hook_output" >&2\n'
        "    fi\n"
        "    sleep 30\n"
        "    if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then\n"
        '      exit "$cmd_status"\n'
        "    fi\n"
        "    continue\n"
        "  fi\n"
        "\n"
        '  if [[ "$hook_status" -eq 4 ]]; then\n'
        '    if [[ -n "$hook_output" ]]; then\n'
        '      printf "%s\\n" "$hook_output" >&2\n'
        "    fi\n"
        '    exit "$hook_status"\n'
        "  fi\n"
        "\n"
        '  if [[ "$hook_status" -eq 0 ]]; then\n'
        '    exit "$cmd_status"\n'
        "  fi\n"
        "\n"
        '  if [[ -n "$hook_output" ]]; then\n'
        '    printf "%s\\n" "$hook_output" >&2\n'
        "  fi\n"
        "  printf '[omni] hook-stop failed with code %s.\\n' \"$hook_status\" >&2\n"
        '  exit "$hook_status"\n'
        "done\n"
    )


def _configure_universal_wrapper() -> tuple[bool, Path]:
    wrapper_path = _wrapper_bin_dir() / _wrapper_filename("omni-agent-wrap")
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    desired = _universal_wrapper_script()
    current = (
        wrapper_path.read_text(encoding="utf-8") if wrapper_path.exists() else None
    )
    changed = current != desired
    if changed:
        wrapper_path.write_text(desired, encoding="utf-8")
    if not _is_windows():
        mode = wrapper_path.stat().st_mode
        wrapper_path.chmod(mode | 0o111)
    return changed, wrapper_path


def _configure_specific_wrapper(
    wrapper_name: str, agent_command: str
) -> tuple[bool, Path]:
    wrapper_path = _wrapper_bin_dir() / _wrapper_filename(f"omni-wrap-{wrapper_name}")
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    desired = _specific_wrapper_script(agent_command)
    current = (
        wrapper_path.read_text(encoding="utf-8") if wrapper_path.exists() else None
    )
    changed = current != desired
    if changed:
        wrapper_path.write_text(desired, encoding="utf-8")
    if not _is_windows():
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


def _cli_candidate_paths(command: str) -> list[Path]:
    candidates: list[Path] = []
    home = Path.home()
    if _is_windows():
        candidates.extend(
            [
                home / "AppData" / "Roaming" / "npm" / f"{command}.cmd",
                home / "AppData" / "Roaming" / "npm" / f"{command}.exe",
                home / ".local" / "bin" / f"{command}.cmd",
                home / ".local" / "bin" / f"{command}.exe",
            ]
        )
    else:
        candidates.extend(
            [
                home / ".local" / "bin" / command,
                home / ".npm-global" / "bin" / command,
                home / ".pnpm-global" / "bin" / command,
            ]
        )
    return candidates


def _has_cli(command: str) -> bool:
    if shutil.which(command) is not None:
        return True
    return any(candidate.exists() for candidate in _cli_candidate_paths(command))


def _openclaw_hook_md() -> str:
    return """---
name: omni-recovery
description: \"Auto-resume active autonomous sessions on startup, react to enriched inbound messages, and checkpoint before compaction\"
metadata:
  openclaw:
    emoji: \"🔁\"
    events: [\"gateway:startup\", \"message:received\", \"message:transcribed\", \"message:preprocessed\", \"session:compact:before\"]
---
# omni-recovery
Runs Omni Autonomous Agent recovery flows for OpenClaw events:

- On `gateway:startup`: if an OAA session is active, queue a resume ping turn.
- On inbound message events: process cancellation decisions (`...` accept, `..` deny), auto-register await-user responses, and keep the recovery route binding fresh.
- On `session:compact:before`: write the OAA precompact handoff/checkpoint before OpenClaw compacts the session.

Note: OpenClaw hooks are event-driven and do not provide true idle timers.
"""


def _openclaw_handler_ts() -> str:
    return """import { createHash } from 'crypto';
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'fs';
import { delimiter, dirname, join } from 'path';
import { spawn, spawnSync } from 'child_process';

type StatusPayload = {
  active?: boolean;
  waiting_for_user?: boolean;
  cancel_request_state?: string;
  request?: string;
  dynamic?: boolean;
  deadline?: string | null;
  report_status?: string;
  started_at?: string;
  runtime_bindings?: {
    openclaw?: OpenclawBinding;
  } | null;
};

type OpenclawBinding = {
  agent_id?: string;
  session_key?: string;
  session_id?: string;
  channel?: string;
  to?: string;
  from?: string;
  account_id?: string;
  updated_at?: string;
};

type SessionRoute = {
  sessionKey: string;
  sessionId: string;
  channel?: string;
  to?: string;
  from?: string;
  accountId?: string;
};

type DedupeResult = {
  decision: 'disabled' | 'recorded' | 'duplicate' | 'lock-unavailable' | 'error';
};

const STARTUP_WAKE_COOLDOWN_MS = 15_000;
let lastStartupWakeMs = 0;
const includeSensitiveContext = process.env.OMNI_AGENT_INCLUDE_SENSITIVE_CONTEXT === '1';
const deliverStartupWake = process.env.OMNI_AGENT_OPENCLAW_WAKE_DELIVER !== '0';
const hookTelemetryEnabled = process.env.OMNI_AGENT_HOOK_TELEMETRY !== '0';

const parseAllowedSenders = (raw: string | undefined): Set<string> => {
  const allowed = new Set<string>();
  const source = (raw ?? '').trim();
  if (!source) return allowed;
  for (const token of source.split(',')) {
    const value = token.trim().toLowerCase();
    if (value) allowed.add(value);
  }
  return allowed;
};

const cancelAllowedSenders = parseAllowedSenders(
  process.env.OMNI_AGENT_OPENCLAW_CANCEL_ALLOWED_SENDERS,
);

const parsePositiveInt = (raw: string | undefined, fallback: number): number => {
  const value = Number.parseInt((raw ?? '').trim(), 10);
  if (Number.isFinite(value) && value > 0) return value;
  return fallback;
};

const AGENT_WAKE_RETRY_ATTEMPTS = parsePositiveInt(
  process.env.OMNI_AGENT_OPENCLAW_AGENT_RETRY_ATTEMPTS,
  5,
);
const AGENT_WAKE_RETRY_DELAY_MS = parsePositiveInt(
  process.env.OMNI_AGENT_OPENCLAW_AGENT_RETRY_DELAY_MS,
  1_500,
);
const DETACHED_LAUNCH_VERIFY_MS = parsePositiveInt(
  process.env.OMNI_AGENT_OPENCLAW_DETACHED_VERIFY_MS,
  250,
);
const DETACHED_LAUNCH_POLL_MS = parsePositiveInt(
  process.env.OMNI_AGENT_OPENCLAW_DETACHED_POLL_MS,
  25,
);
const syncAgentLaunch = process.env.OMNI_AGENT_OPENCLAW_SYNC_LAUNCH === '1';

const normalizeTelemetryText = (raw: string, maxLen: number): string =>
  raw.replace(/\\s+/g, ' ').trim().slice(0, maxLen);

const readCandidateString = (...candidates: unknown[]): string => {
  for (const candidate of candidates) {
    if (typeof candidate !== 'string') continue;
    const normalized = candidate.trim();
    if (normalized) return normalized;
  }
  return '';
};

const readRecordString = (record: Record<string, unknown> | null, key: string): string =>
  record && typeof record[key] === 'string' ? String(record[key]).trim() : '';

const shortFingerprint = (raw: string | undefined): string => {
  const value = (raw ?? '').trim();
  if (!value) return 'none';
  return createHash('sha256').update(value).digest('hex').slice(0, 12);
};

const CANCEL_ACCEPT_TOKENS = new Set([
  '...',
  '/accept-cancel',
  '/cancel-accept',
  'accept cancel',
  'approve cancel',
]);

const CANCEL_DENY_TOKENS = new Set([
  '..',
  '/deny-cancel',
  '/cancel-deny',
  'deny cancel',
  'reject cancel',
]);

const normalizeInboundMessage = (raw: string): string =>
  raw.replace(/\\s+/g, ' ').trim().toLowerCase();

const parseCancelDecision = (raw: string): 'accept' | 'deny' | null => {
  const normalized = normalizeInboundMessage(raw);
  if (!normalized) return null;
  if (CANCEL_ACCEPT_TOKENS.has(normalized)) return 'accept';
  if (CANCEL_DENY_TOKENS.has(normalized)) return 'deny';
  return null;
};

const STARTUP_WAKE_PERSISTED_DEDUPE_MS = parsePositiveInt(
  process.env.OMNI_AGENT_OPENCLAW_WAKE_DEDUPE_MS,
  60_000,
);
const INBOUND_FORWARD_DEDUPE_MS = parsePositiveInt(
  process.env.OMNI_AGENT_OPENCLAW_MESSAGE_DEDUPE_MS,
  5_000,
);

const buildRuntimeEnv = () => {
  const env = { ...process.env };
  const pathEntries: string[] = [];

  const addEntries = (value: string | undefined) => {
    if (!value) return;
    for (const rawEntry of value.split(delimiter)) {
      const entry = rawEntry.trim();
      if (entry) pathEntries.push(entry);
    }
  };

  addEntries(process.env.PATH);

  const home = (process.env.HOME ?? process.env.USERPROFILE ?? '').trim();
  if (home) {
    pathEntries.push(join(home, '.local', 'bin'));
    pathEntries.push(join(home, '.npm-global', 'bin'));
    pathEntries.push(join(home, '.pnpm-global', 'bin'));
    if (process.platform === 'win32') {
      pathEntries.push(join(home, 'AppData', 'Roaming', 'npm'));
      pathEntries.push(join(home, 'AppData', 'Local', 'omni-autonomous-agent', 'bin'));
    }
  }

  if (process.platform === 'win32') {
    pathEntries.push('C:/Program Files/nodejs', 'C:/Program Files (x86)/nodejs');
  } else {
    pathEntries.push('/usr/local/bin', '/usr/bin', '/bin');
  }

  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const entry of pathEntries) {
    if (seen.has(entry)) continue;
    seen.add(entry);
    deduped.push(entry);
  }

  env.PATH = deduped.join(delimiter);
  return env;
};

const runtimeEnv = buildRuntimeEnv();

const resolveHome = (): string => (process.env.HOME ?? process.env.USERPROFILE ?? '').trim();

const resolveConfigDir = (): string | null => {
  const configOverride = process.env.OMNI_AGENT_CONFIG_DIR?.trim();
  if (configOverride) return configOverride;
  const home = resolveHome();
  if (!home) return null;
  if (process.platform === 'win32') {
    const appData =
      (process.env.LOCALAPPDATA ?? process.env.APPDATA ?? '').trim() || join(home, 'AppData', 'Local');
    return join(appData, 'omni-autonomous-agent');
  }
  if (process.platform === 'darwin') {
    return join(home, 'Library', 'Application Support', 'omni-autonomous-agent');
  }
  const xdgConfigHome = process.env.XDG_CONFIG_HOME?.trim();
  if (xdgConfigHome) return join(xdgConfigHome, 'omni-autonomous-agent');
  return join(home, '.config', 'omni-autonomous-agent');
};

const resolveNamedDedupeFile = (fileName: string): string | null => {
  const configDir = resolveConfigDir();
  if (!configDir) return null;
  return join(configDir, fileName);
};

const resolveWakeDedupeFile = (): string | null =>
  resolveNamedDedupeFile('openclaw-startup-wake.json');

const resolveInboundForwardDedupeFile = (): string | null =>
  resolveNamedDedupeFile('openclaw-inbound-forward.json');

const readPersistedOpenclawBinding = (status: StatusPayload | null): OpenclawBinding | null => {
  const binding = status?.runtime_bindings?.openclaw;
  if (!binding || typeof binding !== 'object' || Array.isArray(binding)) return null;

  const sessionId = readCandidateString((binding as OpenclawBinding).session_id);
  if (!sessionId) return null;

  const normalized: OpenclawBinding = {
    session_id: sessionId,
  };

  const agentId = readCandidateString((binding as OpenclawBinding).agent_id);
  if (agentId) normalized.agent_id = agentId;

  const sessionKey = readCandidateString((binding as OpenclawBinding).session_key);
  if (sessionKey) normalized.session_key = sessionKey;

  const channel = readCandidateString((binding as OpenclawBinding).channel);
  if (channel) normalized.channel = channel;

  const to = readCandidateString((binding as OpenclawBinding).to);
  if (to) normalized.to = to;

  const from = readCandidateString((binding as OpenclawBinding).from);
  if (from) normalized.from = from;

  const accountId = readCandidateString((binding as OpenclawBinding).account_id);
  if (accountId) normalized.account_id = accountId;

  const updatedAt = readCandidateString((binding as OpenclawBinding).updated_at);
  if (updatedAt) normalized.updated_at = updatedAt;

  if (!normalized.agent_id) normalized.agent_id = 'main';
  return normalized;
};

const readJsonObject = (path: string): Record<string, unknown> | null => {
  try {
    const raw = readFileSync(path, 'utf-8');
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
};

const readSessionsFromCliStores = (
  payload: Record<string, unknown>,
  targetAgentId: string,
): Record<string, unknown> | null => {
  const stores = Array.isArray(payload.stores) ? payload.stores : [];
  const entries: Record<string, unknown> = {};

  for (const store of stores) {
    if (!store || typeof store !== 'object' || Array.isArray(store)) continue;
    const storeRecord = store as Record<string, unknown>;
    const storePath = readRecordString(storeRecord, 'path');
    if (!storePath) continue;

    const storeAgentId = readRecordString(storeRecord, 'agentId');
    if (storeAgentId && storeAgentId !== targetAgentId) continue;

    const storeEntries = readJsonObject(storePath);
    if (!storeEntries) continue;

    for (const [sessionKey, entry] of Object.entries(storeEntries)) {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
      const record = entry as Record<string, unknown>;
      const sessionId = readCandidateString(record.sessionId);
      if (!sessionId) continue;

      const sessionKeyAgentId = sessionKey.split(':')[1]?.trim() ?? '';
      const recordAgentId = readCandidateString(record.agentId);
      const effectiveAgentId = recordAgentId || sessionKeyAgentId || storeAgentId;
      if (effectiveAgentId && effectiveAgentId !== targetAgentId) continue;

      entries[sessionKey] = record;
    }
  }

  return Object.keys(entries).length > 0 ? entries : null;
};

const readSessionsFromCli = (
  targetAgentId: string,
): Record<string, unknown> | null => {
  const result = spawnWithShimFallback(openclawBin, ['sessions', '--json', '--all-agents'], {
    stdio: 'pipe',
    encoding: 'utf-8',
    env: runtimeEnv,
  });

  if (typeof result.status === 'number' && result.status !== 0) {
    return null;
  }
  if (result.error) {
    return null;
  }

  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
  if (!output) return null;

  try {
    const parsed = JSON.parse(output);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return null;
    }

    const payload = parsed as Record<string, unknown>;
    const storeEntries = readSessionsFromCliStores(payload, targetAgentId);
    const sessions = (parsed as Record<string, unknown>).sessions;
    const entries: Record<string, unknown> = storeEntries ? { ...storeEntries } : {};
    if (Array.isArray(sessions)) {
      for (const session of sessions) {
        if (!session || typeof session !== 'object' || Array.isArray(session)) continue;
        const record = session as Record<string, unknown>;
        const sessionKey = readCandidateString(record.key);
        const sessionId = readCandidateString(record.sessionId);
        const agentId = readCandidateString(record.agentId);
        if (!sessionKey || !sessionId) continue;
        if (agentId && agentId !== targetAgentId) continue;
        if (entries[sessionKey] !== undefined) continue;
        entries[sessionKey] = record;
      }
    }

    return Object.keys(entries).length > 0 ? entries : null;
  } catch {
    return null;
  }
};

const acquireDedupeLock = (lockDir: string): boolean => {
  try {
    mkdirSync(dirname(lockDir), { recursive: true });
    mkdirSync(lockDir);
    return true;
  } catch {
    try {
      const lockStat = statSync(lockDir);
      if (Date.now() - lockStat.mtimeMs > 30_000) {
        rmSync(lockDir, { recursive: true, force: true });
        mkdirSync(lockDir);
        return true;
      }
    } catch {
      return false;
    }
    return false;
  }
};

const rememberStartupWake = (dedupeKey: string): DedupeResult => {
  const dedupeFile = resolveWakeDedupeFile();
  if (!dedupeFile) return { decision: 'disabled' };

  const lockDir = `${dedupeFile}.lock`;
  if (!acquireDedupeLock(lockDir)) {
    return { decision: 'lock-unavailable' };
  }

  try {
    const now = Date.now();
    const existing = readJsonObject(dedupeFile);
    const entriesRaw = existing?.entries;
    const entries: Record<string, number> = {};

    if (entriesRaw && typeof entriesRaw === 'object' && !Array.isArray(entriesRaw)) {
      for (const [key, value] of Object.entries(entriesRaw as Record<string, unknown>)) {
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        if (now - value > STARTUP_WAKE_PERSISTED_DEDUPE_MS) continue;
        entries[key] = value;
      }
    }

    const seenAt = entries[dedupeKey];
    if (typeof seenAt === 'number' && now - seenAt < STARTUP_WAKE_PERSISTED_DEDUPE_MS) {
      return { decision: 'duplicate' };
    }

    entries[dedupeKey] = now;
    mkdirSync(dirname(dedupeFile), { recursive: true });
    writeFileSync(dedupeFile, JSON.stringify({ entries }, null, 2), 'utf-8');
  } catch {
    return { decision: 'error' };
  } finally {
    rmSync(lockDir, { recursive: true, force: true });
  }

  return { decision: 'recorded' };
};

const forgetStartupWake = (dedupeKey: string): void => {
  const dedupeFile = resolveWakeDedupeFile();
  if (!dedupeFile) return;

  const lockDir = `${dedupeFile}.lock`;
  if (!acquireDedupeLock(lockDir)) return;

  try {
    const existing = readJsonObject(dedupeFile);
    const entriesRaw = existing?.entries;
    if (!entriesRaw || typeof entriesRaw !== 'object' || Array.isArray(entriesRaw)) return;

    const nextEntries: Record<string, number> = {};
    for (const [key, value] of Object.entries(entriesRaw as Record<string, unknown>)) {
      if (key === dedupeKey) continue;
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      nextEntries[key] = value;
    }

    mkdirSync(dirname(dedupeFile), { recursive: true });
    writeFileSync(dedupeFile, JSON.stringify({ entries: nextEntries }, null, 2), 'utf-8');
  } catch {
    return;
  } finally {
    rmSync(lockDir, { recursive: true, force: true });
  }
};

const rememberInboundForward = (dedupeKey: string): DedupeResult => {
  const dedupeFile = resolveInboundForwardDedupeFile();
  if (!dedupeFile) return { decision: 'disabled' };

  const lockDir = `${dedupeFile}.lock`;
  if (!acquireDedupeLock(lockDir)) {
    return { decision: 'lock-unavailable' };
  }

  try {
    const now = Date.now();
    const existing = readJsonObject(dedupeFile);
    const entriesRaw = existing?.entries;
    const entries: Record<string, number> = {};

    if (entriesRaw && typeof entriesRaw === 'object' && !Array.isArray(entriesRaw)) {
      for (const [key, value] of Object.entries(entriesRaw as Record<string, unknown>)) {
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        if (now - value > INBOUND_FORWARD_DEDUPE_MS) continue;
        entries[key] = value;
      }
    }

    const seenAt = entries[dedupeKey];
    if (typeof seenAt === 'number' && now - seenAt < INBOUND_FORWARD_DEDUPE_MS) {
      return { decision: 'duplicate' };
    }

    entries[dedupeKey] = now;
    mkdirSync(dirname(dedupeFile), { recursive: true });
    writeFileSync(dedupeFile, JSON.stringify({ entries }, null, 2), 'utf-8');
  } catch {
    return { decision: 'error' };
  } finally {
    rmSync(lockDir, { recursive: true, force: true });
  }

  return { decision: 'recorded' };
};

const resolveOpenclawBinary = () => {
  const override = process.env.OMNI_AGENT_OPENCLAW_BIN?.trim();
  if (override) return override;

  const home = resolveHome();
  const candidates: string[] = [];
  if (home) {
    if (process.platform === 'win32') {
      candidates.push(join(home, 'AppData', 'Roaming', 'npm', 'openclaw.cmd'));
      candidates.push(join(home, 'AppData', 'Roaming', 'npm', 'openclaw.exe'));
      candidates.push(join(home, 'AppData', 'Local', 'Programs', 'openclaw', 'openclaw.exe'));
      candidates.push(join(home, '.local', 'bin', 'openclaw.cmd'));
      candidates.push(join(home, '.local', 'bin', 'openclaw.exe'));
    } else {
      candidates.push(join(home, '.npm-global', 'bin', 'openclaw'));
      candidates.push(join(home, '.local', 'bin', 'openclaw'));
      candidates.push(join(home, '.pnpm-global', 'bin', 'openclaw'));
    }
  }
  if (process.platform === 'win32') {
    candidates.push('C:/Program Files/nodejs/openclaw.cmd');
    candidates.push('C:/Program Files/nodejs/openclaw.exe');
  } else {
    candidates.push('/usr/local/bin/openclaw', '/usr/bin/openclaw');
  }

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }

  return 'openclaw';
};

const openclawBin = resolveOpenclawBinary();

const resolveOaaBinary = () => {
  const override = process.env.OMNI_AGENT_OAA_BIN?.trim();
  if (override) return override;

  const home = resolveHome();
  const candidates: string[] = [];
  if (home) {
    if (process.platform === 'win32') {
      candidates.push(join(home, 'AppData', 'Local', 'omni-autonomous-agent', 'bin', 'omni-autonomous-agent.cmd'));
      candidates.push(join(home, '.local', 'bin', 'omni-autonomous-agent.cmd'));
      candidates.push(join(home, '.local', 'bin', 'omni-autonomous-agent.exe'));
    } else {
      candidates.push(join(home, '.local', 'bin', 'omni-autonomous-agent'));
      candidates.push(join(home, '.npm-global', 'bin', 'omni-autonomous-agent'));
    }
  }

  if (process.platform === 'win32') {
    candidates.push('C:/Program Files/omni-autonomous-agent/omni-autonomous-agent.cmd');
  } else {
    candidates.push('/usr/local/bin/omni-autonomous-agent', '/usr/bin/omni-autonomous-agent');
  }

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }

  return 'omni-autonomous-agent';
};

const oaaBin = resolveOaaBinary();

const quotePosixArg = (value: string): string =>
  `'${value.replace(/'/g, `'\\''`)}'`;

const quoteWindowsArg = (value: string): string =>
  `"${value.replace(/(["^%])/g, '^$1')}"`;

const buildShellCommand = (command: string, args: string[]): string => {
  const quote = process.platform === 'win32' ? quoteWindowsArg : quotePosixArg;
  return [command, ...args].map(quote).join(' ');
};

const spawnWithShimFallback = (
  command: string,
  args: string[],
  options: Parameters<typeof spawnSync>[2],
) => {
  const direct = spawnSync(command, args, options);
  const errorCode =
    direct.error && typeof direct.error === 'object' && 'code' in direct.error
      ? String((direct.error as NodeJS.ErrnoException).code ?? '')
      : '';
  if (errorCode !== 'EPERM') {
    return direct;
  }

  return spawnSync(buildShellCommand(command, args), {
    ...options,
    shell: true,
  });
};

const runOaa = (args: string[]) => {
  const result = spawnWithShimFallback(oaaBin, args, {
    stdio: 'pipe',
    encoding: 'utf-8',
    env: runtimeEnv,
  });

  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
  if (typeof result.status === 'number') {
    return {
      ok: result.status === 0,
      output,
    };
  }

  if (result.error) {
    const reason = result.error instanceof Error ? result.error.message : String(result.error);
    return {
      ok: false,
      output: [output, reason].filter(Boolean).join('\\n').trim(),
    };
  }

  return {
    ok: result.status === 0,
    output,
  };
};

const recordHookTelemetry = (eventName: string, note: string) => {
  if (!hookTelemetryEnabled) return;
  const eventText = normalizeTelemetryText(eventName, 72).toLowerCase();
  if (!eventText) return;
  const noteText = normalizeTelemetryText(note, 240) || 'none';
  runOaa(['--log-event', '--event', eventText, '--note', noteText]);
};

const readStatusPayload = (): StatusPayload | null => {
  const status = runOaa(['--status', '--json']);
  if (!status.ok || !status.output) return null;
  try {
    return JSON.parse(status.output) as StatusPayload;
  } catch {
    return null;
  }
};

const requireActiveSession = (): boolean => {
  const active = runOaa(['--require-active']);
  return active.ok;
};

const loadOpenclawSessions = (
  targetAgentId: string,
): Record<string, unknown> | null => {
  const home = resolveHome();
  const fileSessions = home
    ? readJsonObject(
        join(home, '.openclaw', 'agents', targetAgentId, 'sessions', 'sessions.json'),
      )
    : null;
  const cliSessions = readSessionsFromCli(targetAgentId);
  return mergeSessionEntryMaps(cliSessions, fileSessions);
};

const readEventSessionKey = (event: any): string =>
  typeof event?.sessionKey === 'string' ? event.sessionKey.trim() : '';

const agentIdFromSessionKey = (sessionKey: string): string => {
  if (!sessionKey.startsWith('agent:')) return '';
  const parts = sessionKey.split(':');
  return parts[1]?.trim() ?? '';
};

const routeFromBinding = (
  binding: OpenclawBinding | null,
  targetAgentId: string,
): SessionRoute | null => {
  if (!binding?.session_id) return null;

  const agentId = readCandidateString(binding.agent_id) || targetAgentId;
  const sessionKey =
    readCandidateString(binding.session_key) || `agent:${agentId}:main`;

  return {
    sessionKey,
    sessionId: binding.session_id,
    channel: readCandidateString(binding.channel) || undefined,
    to: readCandidateString(binding.to) || undefined,
    from: readCandidateString(binding.from) || undefined,
    accountId: readCandidateString(binding.account_id) || undefined,
  };
};

const routeFromEntry = (
  sessionKey: string,
  entry: unknown,
): { route: SessionRoute; updatedAt: number } | null => {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;

  const record = entry as Record<string, unknown>;
  const sessionId = typeof record.sessionId === 'string' ? record.sessionId.trim() : '';
  if (!sessionId) return null;

  const deliveryContext =
    record.deliveryContext && typeof record.deliveryContext === 'object' && !Array.isArray(record.deliveryContext)
      ? (record.deliveryContext as Record<string, unknown>)
      : null;
  const origin =
    record.origin && typeof record.origin === 'object' && !Array.isArray(record.origin)
      ? (record.origin as Record<string, unknown>)
      : null;

  const channel =
    readRecordString(deliveryContext, 'channel') ||
    readRecordString(record, 'lastChannel') ||
    readRecordString(origin, 'surface') ||
    readRecordString(origin, 'provider');
  const to =
    readRecordString(deliveryContext, 'to') ||
    readRecordString(record, 'lastTo') ||
    readRecordString(origin, 'to');
  const from = readRecordString(origin, 'from');
  const accountId =
    readRecordString(deliveryContext, 'accountId') ||
    readRecordString(record, 'lastAccountId') ||
    readRecordString(origin, 'accountId');
  const updatedAt =
    typeof record.updatedAt === 'number' && Number.isFinite(record.updatedAt)
      ? record.updatedAt
      : 0;

  return {
    route: {
      sessionKey,
      sessionId,
      channel: channel || undefined,
      to: to || undefined,
      from: from || undefined,
      accountId: accountId || undefined,
    },
    updatedAt,
  };
};

const mergeSessionRoutes = (
  primary: SessionRoute | null,
  fallback: SessionRoute | null,
): SessionRoute | null => {
  if (!primary) return fallback;
  if (!fallback) return primary;

  if (
    fallback.sessionId &&
    primary.sessionId &&
    fallback.sessionId !== primary.sessionId
  ) {
    return primary;
  }

  return {
    sessionKey: primary.sessionKey || fallback.sessionKey,
    sessionId: primary.sessionId || fallback.sessionId,
    channel: primary.channel || fallback.channel,
    to: primary.to || fallback.to,
    from: primary.from || fallback.from,
    accountId: primary.accountId || fallback.accountId,
  };
};

const routeDeliveryScore = (route: SessionRoute): number => {
  let score = 0;
  if (route.channel) score += 2;
  if (route.to) score += 4;
  if (route.from) score += 2;
  if (route.accountId) score += 1;
  return score;
};

const routeSubagentPenalty = (route: SessionRoute): number =>
  route.sessionKey.includes(':subagent:') ? 1 : 0;

const sessionEntrySignalScore = (sessionKey: string, entry: unknown): number => {
  const candidate = routeFromEntry(sessionKey, entry);
  if (!candidate) return 0;
  let score = routeDeliveryScore(candidate.route) * 10;
  if (candidate.updatedAt > 0) score += 1;
  return score;
};

const mergeSessionEntryMaps = (
  primary: Record<string, unknown> | null,
  fallback: Record<string, unknown> | null,
): Record<string, unknown> | null => {
  if (!primary) return fallback;
  if (!fallback) return primary;

  const merged = { ...fallback };
  for (const [sessionKey, entry] of Object.entries(primary)) {
    const existing = merged[sessionKey];
    if (existing === undefined) {
      merged[sessionKey] = entry;
      continue;
    }

    const entryScore = sessionEntrySignalScore(sessionKey, entry);
    const existingScore = sessionEntrySignalScore(sessionKey, existing);
    if (entryScore >= existingScore) {
      merged[sessionKey] = entry;
    }
  }

  return merged;
};

const sessionKeyMatchesTargetAgent = (
  sessionKey: string,
  targetAgentId: string,
): boolean => {
  const agentId = agentIdFromSessionKey(sessionKey);
  if (!agentId) return true;
  return agentId === targetAgentId;
};

const selectSessionRoute = (
  sessions: Record<string, unknown>,
  targetAgentId: string,
  preferredKeys: string[],
  preferredSessionId: string,
): SessionRoute | null => {
  const preferredKeySet = new Set(
    preferredKeys.map((value) => value.trim()).filter(Boolean),
  );
  const candidates: Array<{
    route: SessionRoute;
    updatedAt: number;
    score: number;
  }> = [];

  let bestRoute: SessionRoute | null = null;
  let bestScore = Number.NEGATIVE_INFINITY;
  let bestUpdatedAt = -1;

  for (const [sessionKey, entry] of Object.entries(sessions)) {
    const candidate = routeFromEntry(sessionKey, entry);
    if (!candidate) continue;
    if (!sessionKeyMatchesTargetAgent(candidate.route.sessionKey, targetAgentId)) continue;

    let score = 0;
    if (preferredKeySet.has(candidate.route.sessionKey)) score += 240;
    if (preferredSessionId && candidate.route.sessionId === preferredSessionId) score += 220;
    if (candidate.route.sessionKey.startsWith(`agent:${targetAgentId}:`)) score += 30;
    if (candidate.route.channel && candidate.route.to) score += 40;
    if (candidate.route.accountId) score += 10;
    if (candidate.route.sessionKey.includes(':subagent:')) score -= 180;
    candidates.push({
      route: candidate.route,
      updatedAt: candidate.updatedAt,
      score,
    });

    if (
      bestRoute === null ||
      score > bestScore ||
      (score === bestScore && candidate.updatedAt > bestUpdatedAt)
    ) {
      bestRoute = candidate.route;
      bestScore = score;
      bestUpdatedAt = candidate.updatedAt;
    }
  }

  const sameSessionRoutes = bestRoute
    ? candidates.filter((candidate) => candidate.route.sessionId === bestRoute?.sessionId)
    : [];

  if (sameSessionRoutes.length > 1) {
    const freshestRoute = [...sameSessionRoutes].sort((left, right) => {
      const subagentDiff =
        routeSubagentPenalty(left.route) - routeSubagentPenalty(right.route);
      if (subagentDiff !== 0) return subagentDiff;
      if (right.updatedAt !== left.updatedAt) return right.updatedAt - left.updatedAt;
      if (right.score !== left.score) return right.score - left.score;
      const metadataDiff =
        routeDeliveryScore(right.route) - routeDeliveryScore(left.route);
      if (metadataDiff !== 0) return metadataDiff;
      return right.route.sessionKey.localeCompare(left.route.sessionKey);
    })[0]?.route ?? null;

    const mostDeliverableRoute = [...sameSessionRoutes].sort((left, right) => {
      const subagentDiff =
        routeSubagentPenalty(left.route) - routeSubagentPenalty(right.route);
      if (subagentDiff !== 0) return subagentDiff;
      const metadataDiff =
        routeDeliveryScore(right.route) - routeDeliveryScore(left.route);
      if (metadataDiff !== 0) return metadataDiff;
      if (right.score !== left.score) return right.score - left.score;
      if (right.updatedAt !== left.updatedAt) return right.updatedAt - left.updatedAt;
      return right.route.sessionKey.localeCompare(left.route.sessionKey);
    })[0]?.route ?? null;

    return mergeSessionRoutes(freshestRoute, mostDeliverableRoute);
  }

  return bestRoute;
};

const persistOpenclawRoute = (targetAgentId: string, route: SessionRoute): void => {
  const args = [
    '--record-openclaw-route',
    '--openclaw-agent-id',
    targetAgentId,
    '--openclaw-session-id',
    route.sessionId,
  ];

  if (route.sessionKey) args.push('--openclaw-session-key', route.sessionKey);
  if (route.channel) args.push('--openclaw-reply-channel', route.channel);
  if (route.to) args.push('--openclaw-reply-to', route.to);
  if (route.from) args.push('--openclaw-reply-from', route.from);
  if (route.accountId) args.push('--openclaw-reply-account', route.accountId);

  runOaa(args);
};

const eventMatchesActiveRoute = (
  event: any,
  targetAgentId: string,
  status: StatusPayload,
  route: SessionRoute,
): boolean => {
  const eventSessionKey = readEventSessionKey(event);
  if (eventSessionKey) {
    if (route.sessionKey === eventSessionKey) return true;

    const persistedBinding = readPersistedOpenclawBinding(status);
    if (readCandidateString(persistedBinding?.session_key) === eventSessionKey) {
      return true;
    }

    const sessions = loadOpenclawSessions(targetAgentId);
    const eventRoute = sessions
      ? routeFromEntry(eventSessionKey, sessions[eventSessionKey])?.route ?? null
      : null;
    if (!eventRoute) {
      return false;
    }

    const persistedSessionId = readCandidateString(persistedBinding?.session_id);
    if (eventRoute.sessionId === route.sessionId) {
      return true;
    }
    if (persistedSessionId && eventRoute.sessionId === persistedSessionId) {
      return true;
    }

    return false;
  }

  return eventEnvelopeMatchesRoute(route, event);
};

const resolveTargetAgentId = (event: any, status?: StatusPayload | null): string | null => {
  const override = process.env.OMNI_AGENT_OPENCLAW_AGENT_ID?.trim();
  if (override) return override;

  const sessionKey = readEventSessionKey(event);
  const sessionAgentId = agentIdFromSessionKey(sessionKey);
  if (sessionAgentId) return sessionAgentId;

  const explicitSessionKey = process.env.OMNI_AGENT_OPENCLAW_SESSION_KEY?.trim();
  const explicitAgentId = agentIdFromSessionKey(explicitSessionKey ?? '');
  if (explicitAgentId) return explicitAgentId;

  const persistedBinding = readPersistedOpenclawBinding(status ?? null);
  const persistedAgentId = readCandidateString(persistedBinding?.agent_id);
  if (persistedAgentId) return persistedAgentId;

  return 'main';
};

const resolveSessionRoute = (
  event: any,
  targetAgentId: string,
  status?: StatusPayload | null,
  options?: { includeEventDelivery?: boolean },
): SessionRoute | null => {
  const explicitSessionKey = process.env.OMNI_AGENT_OPENCLAW_SESSION_KEY?.trim();
  const eventSessionKey = readEventSessionKey(event);
  const persistedBinding = readPersistedOpenclawBinding(status ?? null);
  const persistedRoute = routeFromBinding(persistedBinding, targetAgentId);
  const fallbackSessionKey =
    readCandidateString(persistedBinding?.session_key) || `agent:${targetAgentId}:main`;
  const includeEventDelivery = options?.includeEventDelivery !== false;
  const finalizeRoute = (route: SessionRoute | null): SessionRoute | null => {
    if (!route) return null;
    if (!includeEventDelivery || event?.type !== 'message') return route;
    return mergeEventDeliveryContext(route, event);
  };

  const overrideSessionId = process.env.OMNI_AGENT_OPENCLAW_SESSION_ID?.trim();
  if (overrideSessionId) {
    return finalizeRoute({
      sessionKey: eventSessionKey || explicitSessionKey || fallbackSessionKey,
      sessionId: overrideSessionId,
      channel: persistedRoute?.channel,
      to: persistedRoute?.to,
      from: persistedRoute?.from,
      accountId: persistedRoute?.accountId,
    });
  }

  const sessions = loadOpenclawSessions(targetAgentId);
  if (!sessions) return finalizeRoute(persistedRoute);

  const selected = selectSessionRoute(
    sessions,
    targetAgentId,
    [
      eventSessionKey,
      explicitSessionKey ?? '',
      readCandidateString(persistedBinding?.session_key),
    ],
    readCandidateString(persistedBinding?.session_id),
  );
  if (selected) return finalizeRoute(mergeSessionRoutes(selected, persistedRoute));

  return finalizeRoute(persistedRoute);
};

const readEventAccountId = (event: any): string =>
  readCandidateString(
    event?.context?.accountId,
    event?.context?.metadata?.accountId,
    event?.accountId,
  );

const readEventChannel = (event: any): string =>
  readCandidateString(
    event?.context?.channel,
    event?.context?.metadata?.channel,
    event?.context?.surface,
    event?.context?.metadata?.surface,
    event?.context?.provider,
    event?.context?.metadata?.provider,
    event?.channel,
  );

const readInboundSender = (event: any): string =>
  readCandidateString(
    event?.context?.from,
    event?.context?.metadata?.from,
    event?.context?.metadata?.senderId,
    event?.from,
  );

const readInboundRecipient = (event: any): string =>
  readCandidateString(
    event?.context?.to,
    event?.context?.metadata?.to,
    event?.to,
  );

const readInboundEventText = (event: any): string => {
  const candidates = [
    event?.context?.bodyForAgent,
    event?.context?.transcript,
    event?.context?.content,
    event?.context?.body,
    event?.context?.text,
    event?.context?.message,
    event?.context?.metadata?.bodyForAgent,
    event?.context?.metadata?.transcript,
    event?.context?.metadata?.content,
    event?.context?.metadata?.text,
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== 'string') continue;
    const normalized = candidate.replace(/\\s+/g, ' ').trim();
    if (normalized) return normalized;
  }
  return '';
};

const readInboundMessageId = (event: any): string =>
  readCandidateString(
    event?.context?.messageId,
    event?.context?.metadata?.messageId,
    event?.messageId,
    event?.id,
  );

const readEventDirection = (event: any): string =>
  readCandidateString(
    event?.context?.direction,
    event?.context?.metadata?.direction,
    event?.direction,
  );

const readEventRole = (event: any): string =>
  readCandidateString(
    event?.context?.role,
    event?.context?.metadata?.role,
    event?.role,
  );

const normalizeRouteIdentity = (value: string | undefined): string =>
  (value ?? '').trim().toLowerCase();

const isNonUserIdentity = (value: string): boolean => {
  const normalized = normalizeInboundMessage(value);
  if (!normalized) return false;
  const tokens = normalized.split(/[^a-z0-9]+/).filter(Boolean);
  return tokens.some((token) =>
    new Set(['assistant', 'system', 'agent', 'model', 'bot']).has(token),
  );
};

const isLikelyUserMessageEvent = (event: any): boolean => {
  const direction = normalizeInboundMessage(readEventDirection(event));
  if (direction && new Set(['outbound', 'egress', 'assistant', 'agent', 'system']).has(direction)) {
    return false;
  }

  const role = normalizeInboundMessage(readEventRole(event));
  if (role && new Set(['assistant', 'system', 'tool', 'model']).has(role)) {
    return false;
  }

  const from = readInboundSender(event);
  if (from && isNonUserIdentity(from)) return false;
  return true;
};

const senderAuthorizedForCancelDecision = (
  event: any,
  from: string,
  status?: StatusPayload | null,
): boolean => {
  const normalizedFrom = from.trim().toLowerCase();
  if (!normalizedFrom) return false;

  if (cancelAllowedSenders.size > 0) {
    return cancelAllowedSenders.has(normalizedFrom);
  }

  const targetAgentId = resolveTargetAgentId(event, status);
  if (!targetAgentId) return false;
  const route = resolveSessionRoute(
    event,
    targetAgentId,
    status,
    { includeEventDelivery: false },
  );
  if (!route) return false;

  const allowedSenders = [route.to]
    .map((value) => normalizeRouteIdentity(value))
    .filter(Boolean);
  if (allowedSenders.length === 0 && route.from && !isNonUserIdentity(route.from)) {
    allowedSenders.push(normalizeRouteIdentity(route.from));
  }
  if (allowedSenders.length === 0) {
    return false;
  }
  if (!allowedSenders.includes(normalizedFrom)) {
    return false;
  }

  if (route.accountId) {
    const eventAccountId = readEventAccountId(event);
    if (!eventAccountId || route.accountId.trim() !== eventAccountId) {
      return false;
    }
  }

  return true;
};

const mergeEventDeliveryContext = (
  route: SessionRoute,
  event: any,
): SessionRoute => ({
  sessionKey: route.sessionKey,
  sessionId: route.sessionId,
  channel: route.channel || readEventChannel(event) || undefined,
  to: route.to || readInboundSender(event) || undefined,
  from: route.from || readInboundRecipient(event) || undefined,
  accountId: route.accountId || readEventAccountId(event) || undefined,
});

const routeAllowsInboundSender = (route: SessionRoute, from: string): boolean => {
  const normalizedFrom = normalizeRouteIdentity(from);
  if (!normalizedFrom) return false;

  const allowedSenders = [route.to]
    .map((value) => normalizeRouteIdentity(value))
    .filter(Boolean);
  if (allowedSenders.length === 0 && route.from && !isNonUserIdentity(route.from)) {
    allowedSenders.push(normalizeRouteIdentity(route.from));
  }
  if (allowedSenders.length === 0) return false;
  return allowedSenders.includes(normalizedFrom);
};

const eventEnvelopeMatchesRoute = (
  route: SessionRoute,
  event: any,
): boolean => {
  const from = readInboundSender(event);
  if (!routeAllowsInboundSender(route, from)) {
    return false;
  }

  if (route.accountId) {
    const eventAccountId = readEventAccountId(event);
    if (!eventAccountId || route.accountId.trim() !== eventAccountId) {
      return false;
    }
  }

  const routeChannel = normalizeRouteIdentity(route.channel);
  const eventChannel = normalizeRouteIdentity(readEventChannel(event));
  if (routeChannel && eventChannel && routeChannel !== eventChannel) {
    return false;
  }

  return true;
};

const startupWakeDedupeKey = (status: StatusPayload, route: SessionRoute): string => {
  const startedAt = typeof status.started_at === 'string' ? status.started_at.trim() : '';
  const sessionStartToken = startedAt || `missing-started-at:${shortFingerprint(status.request)}`;
  return ['startup', route.sessionKey, route.sessionId, sessionStartToken].join('|');
};

const inboundForwardDedupeKey = (
  route: SessionRoute,
  event: any,
  raw: string,
  from: string,
): string => {
  const messageId = readInboundMessageId(event);
  const messageToken = messageId || `text:${shortFingerprint(normalizeInboundMessage(raw))}`;
  return [
    'message',
    route.sessionKey,
    route.sessionId,
    messageToken,
    shortFingerprint(from),
  ].join('|');
};

const buildAgentArgs = (targetAgentId: string, route: SessionRoute, prompt: string): string[] => {
  const args = ['agent', '--agent', targetAgentId, '--session-id', route.sessionId, '--message', prompt];
  if (deliverStartupWake) {
    args.push('--deliver');
    if (route.channel) args.push('--reply-channel', route.channel);
    if (route.to) args.push('--reply-to', route.to);
    if (route.accountId) args.push('--reply-account', route.accountId);
  }
  return args;
};

const reportLaunchFailure = (
  reason: string,
  spawnFailedEvent: string,
  spawnFailedNote: string,
  rollbackStartupDedupeKey?: string,
): false => {
  if (rollbackStartupDedupeKey) {
    forgetStartupWake(rollbackStartupDedupeKey);
  }
  console.error(`[omni-recovery] failed to launch agent wake runner: ${reason}`);
  recordHookTelemetry(
    spawnFailedEvent,
    `${spawnFailedNote} reason=${normalizeTelemetryText(reason, 160)}`,
  );
  return false;
};

const sleepMs = (ms: number): void => {
  if (ms <= 0) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
};

const launchDetachedOpenclawAgent = (
  command: string,
  args: string[],
  options: { shell: boolean },
): { ok: boolean; reason: string } => {
  const shell = options.shell;
  if (process.platform !== 'win32') {
    const quotedCommand = shell ? buildShellCommand(command, args) : buildShellCommand(command, args);
    const verifySeconds = Math.max(1, Math.ceil(DETACHED_LAUNCH_VERIFY_MS / 1000));
    const script = `nohup ${quotedCommand} >/dev/null 2>&1 & child=$!; sleep ${verifySeconds}; kill -0 "$child" >/dev/null 2>&1`;
    const result = spawnSync('sh', ['-lc', script], {
      stdio: 'ignore',
      env: runtimeEnv,
    });

    if (result.status === 0) {
      return { ok: true, reason: '' };
    }

    const reason = typeof result.status === 'number'
      ? `exit=${result.status}`
      : result.error instanceof Error
        ? result.error.message
        : 'unknown';
    return { ok: false, reason };
  }

  try {
    const child = shell
      ? spawn(buildShellCommand(command, args), {
          stdio: 'ignore',
          env: runtimeEnv,
          detached: true,
          windowsHide: true,
          shell: true,
        })
      : spawn(command, args, {
          stdio: 'ignore',
          env: runtimeEnv,
          detached: true,
          windowsHide: true,
        });

    const pid = child.pid;
    if (typeof pid !== 'number' || pid <= 0) {
      return { ok: false, reason: 'missing child pid' };
    }
    child.unref();

    const deadline = Date.now() + DETACHED_LAUNCH_VERIFY_MS;
    while (Date.now() < deadline) {
      const remaining = deadline - Date.now();
      sleepMs(Math.min(DETACHED_LAUNCH_POLL_MS, remaining));
    }

    try {
      process.kill(pid, 0);
      return { ok: true, reason: '' };
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      return { ok: false, reason: reason || 'process exited before verification' };
    }
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return { ok: false, reason };
  }
};

const launchOpenclawAgent = (
  args: string[],
  spawnFailedEvent: string,
  spawnFailedNote: string,
  rollbackStartupDedupeKey?: string,
) => {
  if (!syncAgentLaunch) {
    const directAttempt = launchDetachedOpenclawAgent(openclawBin, args, {
      shell: false,
    });
    if (directAttempt.ok) {
      return true;
    }

    const shellAttempt = launchDetachedOpenclawAgent(openclawBin, args, {
      shell: true,
    });
    if (shellAttempt.ok) {
      return true;
    }

    return reportLaunchFailure(
      shellAttempt.reason || directAttempt.reason || 'unknown',
      spawnFailedEvent,
      spawnFailedNote,
      rollbackStartupDedupeKey,
    );
  }

  let failureReason = 'unknown';

  for (let attempt = 0; attempt < AGENT_WAKE_RETRY_ATTEMPTS; attempt += 1) {
    const result = spawnWithShimFallback(openclawBin, args, {
      stdio: 'ignore',
      env: runtimeEnv,
    });
    if (result.status === 0) {
      return true;
    }

    failureReason = typeof result.status === 'number'
      ? `exit=${result.status}`
      : result.error instanceof Error
        ? result.error.message
        : 'unknown';

    if (attempt + 1 < AGENT_WAKE_RETRY_ATTEMPTS) {
      sleepMs(AGENT_WAKE_RETRY_DELAY_MS);
    }
  }

  return reportLaunchFailure(
    failureReason,
    spawnFailedEvent,
    spawnFailedNote,
    rollbackStartupDedupeKey,
  );
};

const queueResumePing = (status: StatusPayload, event: any) => {
  const eventSessionKey = readEventSessionKey(event);
  const targetAgentId = resolveTargetAgentId(event, status);
  if (!targetAgentId) {
    console.warn('[omni-recovery] startup wake skipped: unresolved target agent id');
    recordHookTelemetry(
      'openclaw.startup.target_unresolved',
      `event_key=${shortFingerprint(eventSessionKey)}`,
    );
    return false;
  }

  const route = resolveSessionRoute(event, targetAgentId, status);
  if (!route) {
    console.warn('[omni-recovery] startup wake skipped: unresolved session route');
    recordHookTelemetry(
      'openclaw.startup.route_unresolved',
      `agent=${targetAgentId} event_key=${shortFingerprint(eventSessionKey)}`,
    );
    return false;
  }
  persistOpenclawRoute(targetAgentId, route);

  const latestStatus = readStatusPayload();
  if (!latestStatus?.active) {
    console.log('[omni-recovery] startup wake skipped: session no longer active');
    recordHookTelemetry(
      'openclaw.startup.session_ineligible',
      `reason=inactive route=${shortFingerprint(route.sessionKey)}`,
    );
    return false;
  }

  if (!requireActiveSession()) {
    console.log('[omni-recovery] startup wake skipped: --require-active failed');
    recordHookTelemetry(
      'openclaw.startup.require_active_failed',
      `route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
    );
    return false;
  }

  const effectiveStatus = latestStatus ?? status;

  const dedupeKey = startupWakeDedupeKey(effectiveStatus, route);
  const dedupe = rememberStartupWake(dedupeKey);
  if (dedupe.decision === 'duplicate') {
    console.log('[omni-recovery] startup wake skipped: duplicate restart event');
    recordHookTelemetry(
      'openclaw.startup.duplicate_skip',
      `key=${shortFingerprint(dedupeKey)} route=${shortFingerprint(route.sessionKey)}`,
    );
    return false;
  }

  if (dedupe.decision === 'lock-unavailable') {
    console.warn('[omni-recovery] startup wake skipped: dedupe lock unavailable');
    recordHookTelemetry(
      'openclaw.startup.dedupe_lock_unavailable',
      `key=${shortFingerprint(dedupeKey)} route=${shortFingerprint(route.sessionKey)}`,
    );
    return false;
  }

  if (dedupe.decision === 'error') {
    console.warn('[omni-recovery] startup wake dedupe file unavailable; proceeding with in-memory dedupe only');
    recordHookTelemetry(
      'openclaw.startup.dedupe_file_error',
      `key=${shortFingerprint(dedupeKey)} route=${shortFingerprint(route.sessionKey)}`,
    );
  }

  const request = effectiveStatus.request ?? '(unknown)';
  const deadline = effectiveStatus.dynamic ? 'dynamic' : (effectiveStatus.deadline ?? 'unknown');
  const reportStatus = effectiveStatus.report_status ?? 'UNKNOWN';
  const requestLine = includeSensitiveContext ? `Request: ${request}` : 'Request: [redacted]';
  const resumeLine = effectiveStatus.waiting_for_user
    ? 'A user-response window may still be active. If it is still open, remain paused for the user; if it has expired, continue autonomously with the best available information.'
    : 'Resume autonomous execution now.';
  const prompt = [
    '[omni] Gateway restarted and an autonomous session is still active.',
    resumeLine,
    requestLine,
    `Deadline: ${deadline}`,
    `Report status: ${reportStatus}`,
  ].join('\\n');

  const args = buildAgentArgs(targetAgentId, route, prompt);

  console.log(
    `[omni-recovery] startup wake queued for agent=${targetAgentId} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
  );
  recordHookTelemetry(
    'openclaw.startup.wake_queued',
    `agent=${targetAgentId} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)} deliver=${deliverStartupWake ? '1' : '0'}`,
  );

  const launched = launchOpenclawAgent(
    args,
    'openclaw.startup.spawn_failed',
    `route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)} rollback=1`,
    dedupe.decision === 'recorded' ? dedupeKey : undefined,
  );
  return launched;
};

const queueInboundUserMessage = (
  status: StatusPayload,
  event: any,
  raw: string,
  targetAgentId: string,
  route: SessionRoute,
) => {
  if (!route) {
    console.warn('[omni-recovery] inbound forward skipped: unresolved session route');
    recordHookTelemetry(
      'openclaw.message.forward_route_unresolved',
      `agent=${targetAgentId} event_key=${shortFingerprint(readEventSessionKey(event))}`,
    );
    return false;
  }

  const from = readInboundSender(event);
  const dedupeKey = inboundForwardDedupeKey(route, event, raw, from);
  const dedupe = rememberInboundForward(dedupeKey);
  if (dedupe.decision === 'duplicate') {
    console.log('[omni-recovery] inbound forward skipped: duplicate message event');
    recordHookTelemetry(
      'openclaw.message.forward_duplicate',
      `action=${event.action} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
    );
    return false;
  }
  if (dedupe.decision === 'lock-unavailable') {
    console.warn('[omni-recovery] inbound forward dedupe lock unavailable; proceeding');
    recordHookTelemetry(
      'openclaw.message.forward_dedupe_lock_unavailable',
      `action=${event.action} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
    );
  }
  if (dedupe.decision === 'error') {
    console.warn('[omni-recovery] inbound forward dedupe file unavailable; proceeding');
    recordHookTelemetry(
      'openclaw.message.forward_dedupe_error',
      `action=${event.action} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
    );
  }

  const deadline = status.dynamic ? 'dynamic' : (status.deadline ?? 'unknown');
  const reportStatus = status.report_status ?? 'UNKNOWN';
  const prompt = [
    '[omni] New user message arrived during an active autonomous session.',
    'Handle it immediately, then continue autonomous execution unless the user changed the task or told you to stop.',
    `User message: ${raw}`,
    `Deadline: ${deadline}`,
    `Report status: ${reportStatus}`,
  ].join('\\n');
  const args = buildAgentArgs(targetAgentId, route, prompt);

  console.log(
    `[omni-recovery] inbound message forwarded to agent=${targetAgentId} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
  );
  recordHookTelemetry(
    'openclaw.message.forward_queued',
    `action=${event.action} agent=${targetAgentId} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)} deliver=${deliverStartupWake ? '1' : '0'}`,
  );
  const launched = launchOpenclawAgent(
    args,
    'openclaw.message.forward_spawn_failed',
    `action=${event.action} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
  );
  return launched;
};

const handler = async (event: any) => {
  if (
    event.type === 'session' &&
    event.action === 'compact:before'
  ) {
    const status = readStatusPayload();
    if (!status?.active) return;

    const precompact = runOaa(['--hook-precompact']);
    if (precompact.ok) {
      recordHookTelemetry('openclaw.session.precompact_forwarded', 'status=ok');
      return;
    }

    const details = normalizeTelemetryText(precompact.output || 'unknown', 180);
    console.warn(`[omni-recovery] precompact forward failed: ${details}`);
    recordHookTelemetry('openclaw.session.precompact_failed', `reason=${details}`);
    return;
  }

  if (
    event.type === 'message' &&
    ['received', 'transcribed', 'preprocessed'].includes(event.action)
  ) {
    const status = readStatusPayload();
    const raw = readInboundEventText(event);
    const note = raw.replace(/\\s+/g, ' ').trim().slice(0, 200) || 'Inbound user message received.';
    const from = readInboundSender(event);

    if (!status?.active) return;
    if (!isLikelyUserMessageEvent(event)) {
      recordHookTelemetry(
        'openclaw.message.non_user_ignored',
        `action=${event.action} from=${shortFingerprint(from)}`,
      );
      return;
    }

    if (status.cancel_request_state === 'pending') {
      const decision = parseCancelDecision(raw);
      if (decision) {
        if (!from || from.toLowerCase() === 'system') return;
        if (!senderAuthorizedForCancelDecision(event, from, status)) {
          recordHookTelemetry(
            'openclaw.message.cancel_decision_unauthorized',
            `from=${shortFingerprint(from)} account=${shortFingerprint(readEventAccountId(event))}`,
          );
          return;
        }

        if (decision === 'accept') {
          runOaa(['--cancel-accept', '--decision-note', note]);
          recordHookTelemetry('openclaw.message.cancel_accept', `from=${shortFingerprint(from)}`);
          return;
        }

        runOaa(['--cancel-deny', '--decision-note', note]);
        recordHookTelemetry('openclaw.message.cancel_deny', `from=${shortFingerprint(from)}`);
        return;
      }
    }

    const targetAgentId = resolveTargetAgentId(event, status);
    if (!targetAgentId) return;

    const route = resolveSessionRoute(event, targetAgentId, status);
    if (!route) return;
    if (!eventMatchesActiveRoute(event, targetAgentId, status, route)) {
      recordHookTelemetry(
        'openclaw.message.route_mismatch_ignored',
        `action=${event.action} from=${shortFingerprint(from)} session=${shortFingerprint(readEventSessionKey(event))}`,
      );
      return;
    }
    persistOpenclawRoute(targetAgentId, route);

    if (raw) {
      runOaa(['--user-responded', '--response-note', note]);
      recordHookTelemetry(
        'openclaw.message.user_responded',
        `action=${event.action} from=${shortFingerprint(from)} active_wait=${status.waiting_for_user ? '1' : '0'}`,
      );
    }

    if (!raw) return;
    const latestStatus = readStatusPayload() ?? status;
    queueInboundUserMessage(latestStatus, event, raw, targetAgentId, route);
    return;
  }

  if (event.type !== 'gateway' || event.action !== 'startup') return;
  if (process.env.OMNI_AGENT_DISABLE_OPENCLAW_AUTOWAKE === '1') return;
  if (Date.now() - lastStartupWakeMs < STARTUP_WAKE_COOLDOWN_MS) return;

  const status = readStatusPayload();
  if (!status) {
    console.warn('[omni-recovery] startup wake skipped: unable to read OAA status');
    recordHookTelemetry(
      'openclaw.startup.status_unavailable',
      `event_key=${shortFingerprint(readEventSessionKey(event))}`,
    );
    return;
  }
  if (!status.active) return;
  if (queueResumePing(status, event)) {
    lastStartupWakeMs = Date.now();
  }
};

export default handler;
"""


def _configure_openclaw() -> tuple[bool, Path]:
    hook_dir = _path_override(
        "OMNI_AGENT_OPENCLAW_HOOK_DIR",
        Path.home() / ".openclaw" / "hooks" / "omni-recovery",
    )
    plugin_dir = _openclaw_plugin_dir()
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

    def _run_openclaw_command(command: list[str]) -> tuple[bool, str]:
        command_text = " ".join(command)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return False, f"{command_text}: timed out after 30 seconds"

        if result.returncode == 0:
            return True, (result.stdout or "").strip()

        details = (result.stderr or result.stdout or "command failed").strip()
        return False, f"{command_text}: {details}"

    plugin_manifest = plugin_dir / "openclaw.plugin.json"
    plugin_entry = plugin_dir / "index.ts"
    if not plugin_manifest.exists() or not plugin_entry.exists():
        raise RuntimeError(f"OpenClaw plugin source missing at {plugin_dir}")

    plugin_cli_ok, plugin_cli_error = _run_openclaw_command(["openclaw", "plugins", "--help"])
    if not plugin_cli_ok:
        raise RuntimeError(
            "openclaw plugins CLI is required for OAA stop-gate enforcement: "
            f"{plugin_cli_error}"
        )

    plugin_info_ok, plugin_info_output = _run_openclaw_command(
        ["openclaw", "plugins", "info", OPENCLAW_PLUGIN_ID, "--json"]
    )
    plugin_needs_install = True
    if plugin_info_ok:
        try:
            plugin_info = json.loads(plugin_info_output)
        except json.JSONDecodeError:
            plugin_info = None
        if isinstance(plugin_info, dict):
            source_value = str(plugin_info.get("source", "") or "").strip()
            plugin_needs_install = not (
                source_value
                and (
                    str(plugin_dir.resolve()) in source_value
                    or source_value == str(plugin_entry.resolve())
                )
            )

    if plugin_needs_install:
        _run_openclaw_command(["openclaw", "plugins", "uninstall", OPENCLAW_PLUGIN_ID])
        plugin_install_ok, plugin_install_error = _run_openclaw_command(
            ["openclaw", "plugins", "install", "--link", str(plugin_dir)]
        )
        if not plugin_install_ok:
            raise RuntimeError(
                f"openclaw plugin install failed for {plugin_dir}: {plugin_install_error}"
            )
        changed = True

    plugin_enable_ok, plugin_enable_error = _run_openclaw_command(
        ["openclaw", "plugins", "enable", OPENCLAW_PLUGIN_ID]
    )
    if not plugin_enable_ok:
        raise RuntimeError(f"openclaw plugin enable failed: {plugin_enable_error}")

    plugin_verify_ok, plugin_verify_error = _run_openclaw_command(
        ["openclaw", "plugins", "info", OPENCLAW_PLUGIN_ID, "--json"]
    )
    if not plugin_verify_ok:
        raise RuntimeError(
            f"openclaw plugin verification failed: {plugin_verify_error}"
        )

    plugin_doctor_ok, plugin_doctor_error = _run_openclaw_command(
        ["openclaw", "plugins", "doctor"]
    )
    if not plugin_doctor_ok:
        raise RuntimeError(f"openclaw plugin doctor failed: {plugin_doctor_error}")

    recovery_ok, recovery_error = _run_openclaw_command(
        ["openclaw", "hooks", "enable", "omni-recovery"]
    )
    if not recovery_ok:
        raise RuntimeError(recovery_error)

    session_memory_ok, session_memory_error = _run_openclaw_command(
        ["openclaw", "hooks", "enable", "session-memory"]
    )
    if not session_memory_ok:
        _row("Warning", c(YELLOW, f"OpenClaw optional hook: {session_memory_error}"))

    hook_check_ok, hook_check_error = _run_openclaw_command(
        ["openclaw", "hooks", "check"]
    )
    if not hook_check_ok:
        raise RuntimeError(f"openclaw hooks health check failed: {hook_check_error}")

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
        "claude": _has_cli("claude"),
        "gemini": _has_cli("gemini"),
        "opencode": _has_cli("opencode"),
        "openclaw": _has_cli("openclaw"),
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
            _has_cli(wrapper_cmd)
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
