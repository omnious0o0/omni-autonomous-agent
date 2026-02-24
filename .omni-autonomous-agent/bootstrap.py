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
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_home:
        return Path(config_home).expanduser() / "opencode" / "plugins" / "omni-hook.ts"
    return Path.home() / ".config" / "opencode" / "plugins" / "omni-hook.ts"


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


def _openclaw_hook_md() -> str:
    return """---
name: omni-recovery
description: \"Auto-resume active autonomous sessions on startup and clear await-user windows on inbound messages\"
metadata:
  openclaw:
    emoji: \"🔁\"
    events: [\"gateway:startup\", \"message:received\"]
---
# omni-recovery
Runs Omni Autonomous Agent recovery flows for OpenClaw events:

- On `gateway:startup`: if an OAA session is active, queue a resume ping turn.
- On `message:received`: process cancellation decisions (`...` accept, `..` deny) and auto-register await-user responses.

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
};

type SessionRoute = {
  sessionKey: string;
  sessionId: string;
  channel?: string;
  to?: string;
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

const normalizeTelemetryText = (raw: string, maxLen: number): string =>
  raw.replace(/\\s+/g, ' ').trim().slice(0, maxLen);

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

const resolveWakeDedupeFile = (): string | null => {
  const configOverride = process.env.OMNI_AGENT_CONFIG_DIR?.trim();
  if (configOverride) return join(configOverride, 'openclaw-startup-wake.json');
  const home = resolveHome();
  if (!home) return null;
  return join(home, '.config', 'omni-autonomous-agent', 'openclaw-startup-wake.json');
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

const acquireDedupeLock = (lockDir: string): boolean => {
  try {
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

const runOaa = (args: string[]) => {
  const result = spawnSync(oaaBin, args, {
    stdio: 'pipe',
    encoding: 'utf-8',
    env: runtimeEnv,
  });

  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
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

const readEventSessionKey = (event: any): string =>
  typeof event?.sessionKey === 'string' ? event.sessionKey.trim() : '';

const resolveTargetAgentId = (event: any): string | null => {
  const override = process.env.OMNI_AGENT_OPENCLAW_AGENT_ID?.trim();
  if (override) return override;

  const sessionKey = readEventSessionKey(event);
  if (sessionKey.startsWith('agent:')) {
    const parts = sessionKey.split(':');
    const candidate = parts[1]?.trim();
    if (candidate) return candidate;
  }

  return 'main';
};

const resolveSessionRoute = (event: any, targetAgentId: string): SessionRoute | null => {
  const explicitSessionKey = process.env.OMNI_AGENT_OPENCLAW_SESSION_KEY?.trim();
  const eventSessionKey = readEventSessionKey(event);
  const eventAgentSessionKey = eventSessionKey.startsWith('agent:') ? eventSessionKey : '';
  const fallbackSessionKey = `agent:${targetAgentId}:main`;
  const routeSessionKey = eventAgentSessionKey || explicitSessionKey || fallbackSessionKey;

  const overrideSessionId = process.env.OMNI_AGENT_OPENCLAW_SESSION_ID?.trim();
  if (overrideSessionId) return { sessionKey: routeSessionKey, sessionId: overrideSessionId };

  const home = resolveHome();
  if (!home) return null;

  const sessionsPath = join(home, '.openclaw', 'agents', targetAgentId, 'sessions', 'sessions.json');
  const sessions = readJsonObject(sessionsPath);
  if (!sessions) return null;

  const entry = sessions[routeSessionKey];
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;

  const record = entry as Record<string, unknown>;
  const sessionId = typeof record.sessionId === 'string' ? record.sessionId.trim() : '';
  if (!sessionId) return null;

  const deliveryContext =
    record.deliveryContext && typeof record.deliveryContext === 'object' && !Array.isArray(record.deliveryContext)
      ? (record.deliveryContext as Record<string, unknown>)
      : null;

  const channel =
    deliveryContext && typeof deliveryContext.channel === 'string'
      ? deliveryContext.channel.trim()
      : '';
  const to =
    deliveryContext && typeof deliveryContext.to === 'string' ? deliveryContext.to.trim() : '';
  const accountId =
    deliveryContext && typeof deliveryContext.accountId === 'string'
      ? deliveryContext.accountId.trim()
      : '';

  return {
    sessionKey: routeSessionKey,
    sessionId,
    channel: channel || undefined,
    to: to || undefined,
    accountId: accountId || undefined,
  };
};

const readEventAccountId = (event: any): string =>
  typeof event?.context?.accountId === 'string' ? event.context.accountId.trim() : '';

const senderAuthorizedForCancelDecision = (event: any, from: string): boolean => {
  const normalizedFrom = from.trim().toLowerCase();
  if (!normalizedFrom) return false;

  if (cancelAllowedSenders.size > 0) {
    return cancelAllowedSenders.has(normalizedFrom);
  }

  const targetAgentId = resolveTargetAgentId(event);
  if (!targetAgentId) return false;
  const route = resolveSessionRoute(event, targetAgentId);
  if (!route) return false;

  if (route.to && route.to.trim().toLowerCase() !== normalizedFrom) {
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

const startupWakeDedupeKey = (status: StatusPayload, route: SessionRoute): string => {
  const startedAt = typeof status.started_at === 'string' ? status.started_at.trim() : '';
  const sessionStartToken = startedAt || `missing-started-at:${shortFingerprint(status.request)}`;
  return ['startup', route.sessionKey, route.sessionId, sessionStartToken].join('|');
};

const queueResumePing = (status: StatusPayload, event: any) => {
  const eventSessionKey = readEventSessionKey(event);
  const targetAgentId = resolveTargetAgentId(event);
  if (!targetAgentId) {
    console.warn('[omni-recovery] startup wake skipped: unresolved target agent id');
    recordHookTelemetry(
      'openclaw.startup.target_unresolved',
      `event_key=${shortFingerprint(eventSessionKey)}`,
    );
    return;
  }

  const route = resolveSessionRoute(event, targetAgentId);
  if (!route) {
    console.warn('[omni-recovery] startup wake skipped: unresolved session route');
    recordHookTelemetry(
      'openclaw.startup.route_unresolved',
      `agent=${targetAgentId} event_key=${shortFingerprint(eventSessionKey)}`,
    );
    return;
  }

  const latestStatus = readStatusPayload();
  if (!latestStatus?.active || latestStatus.waiting_for_user) {
    console.log('[omni-recovery] startup wake skipped: session no longer active');
    const reason = !latestStatus?.active ? 'inactive' : 'waiting_for_user';
    recordHookTelemetry(
      'openclaw.startup.session_ineligible',
      `reason=${reason} route=${shortFingerprint(route.sessionKey)}`,
    );
    return;
  }

  if (!requireActiveSession()) {
    console.log('[omni-recovery] startup wake skipped: --require-active failed');
    recordHookTelemetry(
      'openclaw.startup.require_active_failed',
      `route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
    );
    return;
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
    return;
  }

  if (dedupe.decision === 'lock-unavailable') {
    console.warn('[omni-recovery] startup wake skipped: dedupe lock unavailable');
    recordHookTelemetry(
      'openclaw.startup.dedupe_lock_unavailable',
      `key=${shortFingerprint(dedupeKey)} route=${shortFingerprint(route.sessionKey)}`,
    );
    return;
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
  const prompt = [
    '[omni] Gateway restarted and an autonomous session is still active.',
    'Resume autonomous execution now.',
    requestLine,
    `Deadline: ${deadline}`,
    `Report status: ${reportStatus}`,
  ].join('\\n');

  const args = ['agent', '--agent', targetAgentId, '--session-id', route.sessionId, '--message', prompt];
  if (deliverStartupWake) {
    args.push('--deliver');
    if (route.channel) args.push('--reply-channel', route.channel);
    if (route.to) args.push('--reply-to', route.to);
    if (route.accountId) args.push('--reply-account', route.accountId);
  }

  console.log(
    `[omni-recovery] startup wake queued for agent=${targetAgentId} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)}`,
  );
  recordHookTelemetry(
    'openclaw.startup.wake_queued',
    `agent=${targetAgentId} route=${shortFingerprint(route.sessionKey)} session=${shortFingerprint(route.sessionId)} deliver=${deliverStartupWake ? '1' : '0'}`,
  );

  let child: ReturnType<typeof spawn>;
  try {
    child = spawn(openclawBin, args, {
      detached: true,
      stdio: 'ignore',
      env: runtimeEnv,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (dedupe.decision === 'recorded') {
      forgetStartupWake(dedupeKey);
    }
    console.error(`[omni-recovery] failed to launch startup wake ping: ${message}`);
    recordHookTelemetry(
      'openclaw.startup.spawn_failed',
      `reason=${normalizeTelemetryText(message, 180)} rollback=1`,
    );
    return;
  }

  child.on('error', (error) => {
    const message = error instanceof Error ? error.message : String(error);
    if (dedupe.decision === 'recorded') {
      forgetStartupWake(dedupeKey);
    }
    console.error(`[omni-recovery] failed to launch startup wake ping: ${message}`);
    recordHookTelemetry(
      'openclaw.startup.spawn_failed',
      `reason=${normalizeTelemetryText(message, 180)} rollback=1`,
    );
  });

  child.unref();
};

const handler = async (event: any) => {
  if (event.type === 'message' && event.action === 'received') {
    const from = typeof event.context?.from === 'string' ? event.context.from.trim() : '';
    if (!from || from.toLowerCase() === 'system') return;

    const status = readStatusPayload();
    const raw = typeof event.context?.content === 'string' ? event.context.content : '';
    const note = raw.replace(/\\s+/g, ' ').trim().slice(0, 200) || 'Inbound user message received.';

    if (!status?.active) return;

    if (status.cancel_request_state === 'pending') {
      const decision = parseCancelDecision(raw);
      if (decision) {
        if (!senderAuthorizedForCancelDecision(event, from)) {
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

    if (!status.waiting_for_user) return;

    runOaa(['--user-responded', '--response-note', note]);
    recordHookTelemetry('openclaw.message.user_responded', `from=${shortFingerprint(from)}`);
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
  if (!status.active || status.waiting_for_user) return;
  lastStartupWakeMs = Date.now();
  queueResumePing(status, event);
};

export default handler;
"""


def _configure_openclaw() -> tuple[bool, Path]:
    hook_dir = _path_override(
        "OMNI_AGENT_OPENCLAW_HOOK_DIR",
        Path.home() / ".openclaw" / "hooks" / "omni-recovery",
    )
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

    def _run_openclaw_enable(command: list[str]) -> tuple[bool, str]:
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
            return True, ""

        details = (result.stderr or result.stdout or "command failed").strip()
        return False, f"{command_text}: {details}"

    recovery_ok, recovery_error = _run_openclaw_enable(
        ["openclaw", "hooks", "enable", "omni-recovery"]
    )
    if not recovery_ok:
        raise RuntimeError(recovery_error)

    session_memory_ok, session_memory_error = _run_openclaw_enable(
        ["openclaw", "hooks", "enable", "session-memory"]
    )
    if not session_memory_ok:
        _row("Warning", c(YELLOW, f"OpenClaw optional hook: {session_memory_error}"))

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
