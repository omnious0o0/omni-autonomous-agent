# Install Help & Hook Setup (AI Self-Check)

This file is the post-install self-check for `omni-autonomous-agent`.

If installation succeeded, the CLI exists and bootstrap already ran once. You still must verify hooks/wrappers before starting autonomous work.

Goal of this guide: let an AI agent recover and complete setup independently, even when the default scripted flow partially fails.

---

## 1) Verify the CLI is reachable

```bash
omni-autonomous-agent --status
```

Expected result: `No active session.` (or active session details).

If `omni-autonomous-agent` is already reachable, you can rerun install via CLI:

```bash
omni-autonomous-agent --install
```

If `omni-autonomous-agent` is not on PATH yet, run the installer directly (default install location):

```bash
bash ~/.omni-autonomous-agent/.omni-autonomous-agent/install.sh
```

On Windows PowerShell (default install location):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.omni-autonomous-agent\.omni-autonomous-agent\install.ps1"
```

The installer output prints the exact install path and PATH export line.

If the CLI is still not found after install, do a direct binary discovery before retrying scripts:

```bash
command -v omni-autonomous-agent || true
ls -la ~/.local/bin/omni-autonomous-agent 2>/dev/null || true
ls -la ~/.omni-autonomous-agent/bin/omni-autonomous-agent 2>/dev/null || true
python3 -c 'import os; print(os.environ.get("PATH", ""))'
```

Use that output to fix PATH in the current shell first, then rerun bootstrap.

---

## 2) Re-run bootstrap explicitly (required self-check)

```bash
omni-autonomous-agent --bootstrap
```

Important behavior:

- Bootstrap is idempotent.
- Bootstrap returns non-zero if a required integration step fails.
- Invalid JSON config files are quarantined to `*.invalid.<token>` and rebuilt safely.

If bootstrap fails, inspect and fix the first failing integration instead of repeatedly rerunning full install:

```bash
omni-autonomous-agent --bootstrap
openclaw hooks check
openclaw hooks info omni-recovery
```

Then verify each agent config path directly (examples are below).

---

## 3) What bootstrap configures automatically

### Native-hook agents

- Claude Code: `~/.claude/settings.json`
  - Stop hook: `omni-autonomous-agent --hook-stop`
  - PreCompact hook: `omni-autonomous-agent --hook-precompact`
- Gemini CLI: `~/.gemini/settings.json`
  - AfterAgent hook: `omni-autonomous-agent --hook-stop`
  - PreCompress hook: `omni-autonomous-agent --hook-precompact`
- OpenCode: `~/.config/opencode/plugins/omni-hook.ts`
  - `session.idle` -> `--hook-stop`
  - `experimental.session.compacting` -> `--hook-precompact`
- OpenClaw: `~/.openclaw/hooks/omni-recovery/`
  - `HOOK.md` + `handler.ts` managed by bootstrap
  - Bootstrap enables `omni-recovery` and attempts `session-memory` (warning-only on failure)
  - `omni-recovery` listens to `gateway:startup` and `message:received`
  - On startup with an active OAA session, it queues a resume ping turn
  - On inbound messages, it auto-registers user responses when OAA is waiting

Optional path overrides for non-default environments:

- `OMNI_AGENT_CLAUDE_SETTINGS`
- `OMNI_AGENT_GEMINI_SETTINGS`
- `OMNI_AGENT_OPENCODE_PLUGIN`
- `OMNI_AGENT_OPENCLAW_HOOK_DIR`

Direct config inspection commands (useful when setup was done by another process):

```bash
python3 - <<'PY'
from pathlib import Path

targets = {
    'claude': Path.home() / '.claude' / 'settings.json',
    'gemini': Path.home() / '.gemini' / 'settings.json',
    'openclaw_hook_md': Path.home() / '.openclaw' / 'hooks' / 'omni-recovery' / 'HOOK.md',
    'openclaw_handler': Path.home() / '.openclaw' / 'hooks' / 'omni-recovery' / 'handler.ts',
}
for name, path in targets.items():
    print(f"{name}: {'OK' if path.exists() else 'MISSING'} -> {path}")
PY
```

### Wrapper-based agents

Bootstrap creates wrappers in a platform-aware bin directory:

- Linux/macOS default: `~/.local/bin`
- Windows default: `%LOCALAPPDATA%\\omni-autonomous-agent\\bin`
- Override on any OS: `OMNI_AGENT_WRAPPER_BIN=/custom/path`

Wrapper names:

- Universal wrapper: `omni-agent-wrap` (Windows: `omni-agent-wrap.cmd`)
- Agent wrappers (when detected): `omni-wrap-<agent>` (Windows: `.cmd` suffix)
  - built-in candidates: `codex`, `aider`, `goose`, `plandex`, `amp`, `crush`, `kiro`, `roo`, `cline`

You can force wrappers for future agents:

```bash
OMNI_AGENT_EXTRA_WRAPPERS="myagent,anotheragent" omni-autonomous-agent --bootstrap
```

---

## 4) Wrapper semantics (strict)

Wrappers are not simple EXIT traps.

They enforce:

1. **Active-session preflight**
   - Calls `omni-autonomous-agent --require-active`
   - If no active session: exits with code `3`
2. **Stop-prevention loop**
   - Runs wrapped agent command
   - Calls `omni-autonomous-agent --hook-stop`
   - If hook exits `2`, wrapper continues looping (no premature stop)
   - If hook exits `4`, wrapper pauses and exits (used for user-response wait windows and corrupted-state recovery)
   - Wrapper exits with command status when hook exits `0`
   - Wrapper exits with hook error code for other non-`0`/`2`/`4` hook failures

Quick preflight check:

```bash
WRAP_BIN="${OMNI_AGENT_WRAPPER_BIN:-$HOME/.local/bin}"
"$WRAP_BIN/omni-agent-wrap" true
```

Expected (without active session): non-zero and message
`[omni] no active session. run omni-autonomous-agent --add first.`

On Windows PowerShell:

```powershell
$wrapBin = if ($env:OMNI_AGENT_WRAPPER_BIN) { $env:OMNI_AGENT_WRAPPER_BIN } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "omni-autonomous-agent\bin" } else { Join-Path $HOME "AppData\Local\omni-autonomous-agent\bin" }
& (Join-Path $wrapBin "omni-agent-wrap.cmd") --version
```

---

## 5) Functional hook verification

### Register a test task (dynamic)

```bash
omni-autonomous-agent --add -R "verification run"
```

Expected:

- Session registration output
- Immediate status output
- Duration defaults to `dynamic` when `-D` is omitted

### Stop must be blocked while still active

```bash
omni-autonomous-agent --hook-stop
```

Expected:

- JSON payload on stdout
- exit code `2`
- `"continue": true`

### Precompact must checkpoint report

```bash
omni-autonomous-agent --hook-precompact
```

Expected:

- JSON payload
- checkpoint appended to `REPORT.md`

### Mark report complete and allow stop

Set `### 🚦 Status` to `COMPLETE` (or `PARTIAL`) in sandbox `REPORT.md`, then run:

```bash
omni-autonomous-agent --hook-stop
```

Expected:

- exit code `0`
- state file removed
- sandbox moved to `omni-sandbox/archived/`
- finalized report contains real completion timestamp + actual worked duration

---

## 6) Await-user window verification (2-minute default)

Open the user-response window:

```bash
omni-autonomous-agent --await-user -Q "Need constraints confirmation"
```

Expected:

- JSON payload with `"hook": "await-user"`
- `"waiting_for_user": true`
- `"wait_minutes": 2` by default

If user returns, clear the wait window:

```bash
omni-autonomous-agent --user-responded --response-note "User replied with updated priorities"
```

Expected:

- JSON payload with `"hook": "user-responded"`
- `"user_response_registered": true`
- wait window cleared from state

If user does not respond before deadline, stop hook returns timeout guidance:

```bash
omni-autonomous-agent --hook-stop
```

Expected:

- exit code `2`
- `"user_response_timed_out": true`
- `"template_id": "user-timeout-continue"`

---

## 7) OpenClaw verification

```bash
openclaw hooks list
```

Expected:

- `omni-recovery` ready
- `session-memory` ready (recommended, optional)

Optional deep check:

```bash
openclaw hooks info omni-recovery
```

Expected events:

- `gateway:startup`
- `message:received`

Eligibility and registration check:

```bash
openclaw hooks check
```

If `session-memory` is unavailable, OAA still works with `omni-recovery` as long as check/info output is healthy.

---

## 8) Kill-switch behavior

```bash
omni-autonomous-agent --cancel
```

- Immediately clears active state
- Archives sandbox if present
- If state file is corrupted, it is quarantined as `state.invalid.<timestamp>.json`

---

## 9) Notes for autonomous runs

- Always register first: `omni-autonomous-agent --add -R "..." [-D <minutes|dynamic>]`
- Always rely on hook output, not assumptions.
- Use `--await-user` when you need missing constraints; default window is 2 minutes.
- If using a new agent binary, set `OMNI_AGENT_EXTRA_WRAPPERS` and rerun bootstrap.
- For CI/non-interactive environments, installer uses non-interactive sudo checks and fails fast when elevation is unavailable.
- If you override install destination with `OMNI_AGENT_SYSTEM_BIN`, ensure that path is writable (or creatable) by the installer user.
- OpenClaw hooks are event-driven; there is no true idle wake timer. Resume happens on startup or inbound events.
- Set `OMNI_AGENT_DISABLE_OPENCLAW_AUTOWAKE=1` to disable startup auto-resume ping behavior.
- If you find bugs or integration issues, report them at `https://github.com/omnious0o0/omni-autonomous-agent/issues` with repro steps.

---

## 10) AI self-setup playbook (non-scripted fallback)

Use this exact order when onboarding fails midway:

1. Confirm runtime tools are callable (`python3`, `omni-autonomous-agent`, agent CLI binaries).
2. Rebuild OAA integrations only: `omni-autonomous-agent --bootstrap`.
3. Validate generated config files exist and contain expected hook commands.
4. Run one dynamic session (`--add ... -D dynamic`), verify stop-block behavior (`--hook-stop` exit `2`), then mark report COMPLETE and verify clean stop (`--hook-stop` exit `0`).
5. Verify OpenClaw hook discovery (`openclaw hooks list`, `openclaw hooks info omni-recovery`, `openclaw hooks check`).
6. Verify wrapper preflight blocks without active session (exit `3`) and loop semantics under active session.

This sequence is faster and safer than re-running full install repeatedly.

---

## 11) Official references and troubleshooting resources

- OpenClaw hooks docs: `https://docs.openclaw.ai/automation/hooks`
- OpenClaw hooks troubleshooting: `https://docs.openclaw.ai/automation/hooks#troubleshooting`
- Gemini CLI authentication: `https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html`
- Gemini CLI repo/docs root: `https://github.com/google-gemini/gemini-cli`
- Claude Code hooks reference: `https://code.claude.com/docs/en/hooks`
- OAA issue tracker: `https://github.com/omnious0o0/omni-autonomous-agent/issues`

When reporting issues, include:

- exact command run
- exit code
- stderr/stdout snippet
- OS + shell
- whether you used default paths or env overrides
