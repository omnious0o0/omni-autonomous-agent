# OAA Helper: Hook Setup Playbook for Agents

Purpose: give agents a reliable, machine-agnostic way to validate OAA hook setup and recover from partial bootstrap failures.

This guide intentionally avoids host-specific paths.
Use command outputs and env overrides instead of assumptions.

---

## 1) Hard rules

- Do not assume install paths.
- Do not assume shell profile state.
- Do not trust stale config files without validation.
- Do not loop full reinstall as first response.
- Always verify with command output before moving to next step.

---

## 2) Fast readiness check

Start with the universal checks:

```bash
omni-autonomous-agent --status
omni-autonomous-agent --bootstrap
```

Then choose the branch that matches the current host:

- If OpenClaw is installed or this host is supposed to use the OpenClaw integration, run:

```bash
openclaw hooks check
openclaw hooks info omni-recovery
```

- If OpenClaw is not installed and you are validating a wrapper-based or generic agent path, run:

```bash
omni-agent-wrap <agent-command> --version
omni-wrap-<agent-command> --version
```

Pass criteria:
- OAA command is callable.
- Bootstrap exits zero.
- On the OpenClaw path, `omni-recovery` is enabled and healthy.
- On the wrapper path, wrapper preflight fails fast without an active session, then becomes enforceable once a session exists.

Do not fail a generic wrapper-based setup just because `openclaw` is absent on that host.

If any command fails, stop and fix that exact failure first.

---

## 3) Required behaviors to verify

### A) OAA stop-gate behavior

```bash
omni-autonomous-agent --add -R "hook verification run" -D 10
omni-autonomous-agent --hook-stop
```

Expected:
- stop hook returns blocked payload (`continue=true`, non-zero exit for blocked state)
- payload includes clear guidance to continue working

### B) Precompact checkpoint behavior

```bash
omni-autonomous-agent --hook-precompact
```

Expected:
- checkpoint is appended to report/log artifacts for handoff safety

### C) Report-gated closure behavior

Set report status to `COMPLETE` (or `PARTIAL`) then run:

```bash
omni-autonomous-agent --hook-stop
```

Expected:
- stop allowed
- state is closed cleanly
- sandbox archived

### D) Await-user behavior

```bash
omni-autonomous-agent --await-user -Q "Need missing constraints"
omni-autonomous-agent --user-responded --response-note "User clarified constraints"
```

Expected:
- wait window is opened and then cleared safely
- stop hook handles timeout path correctly when no response arrives

---

## 4) Wrapper contract (must hold)

Wrappers are enforcement points, not convenience scripts.

They must guarantee:
1. Active-session preflight before running wrapped agent.
2. Stop-hook enforcement loop after each wrapped execution.
3. Correct handling of stop-hook exit classes:
   - blocked -> continue loop
   - pause required -> pause then resume
   - allowed -> exit cleanly
   - unexpected error -> propagate failure

If wrapper behavior diverges from this contract, treat it as a setup failure.

---

## 4B) Command model (must stay sane)

OAA does **not** replace the shell and does **not** require every command to be prefixed with `omni-autonomous-agent`.

Normal work commands remain normal commands, for example:
- `git`
- `python3`
- `node`
- `docker`
- `curl`
- project-local scripts and build tools

Use OAA commands only for:
- session lifecycle (`--add`, `--status`, `--cancel`, `--await-user`, etc.)
- stop/precompact hook entry points
- bootstrap or repair

Use wrappers only for the **agent process** when you need automatic stop-gate enforcement around that agent binary.
Do not "solve" integration by forcing unrelated shell commands through OAA.

---

## 4C) Future-agent fallback (must stay generic)

If the provider is not one of the native hook integrations, use wrapper-based fallback instead of inventing provider-specific hacks.

Bootstrap options:

```bash
AGENT=<agent-command> omni-autonomous-agent --bootstrap
OMNI_AGENT_EXTRA_WRAPPERS="<agent-a> <agent-b>" omni-autonomous-agent --bootstrap
```

Verification:

```bash
omni-wrap-<agent-command> --version
omni-agent-wrap <agent-command> --version
```

Expected:
- with no active session, wrapper preflight fails fast
- with an active session, wrapped agent execution is followed by OAA stop-gate enforcement
- wrapper behavior matches the contract in section 4

If only wrapper generation was tested, report that as wrapper-level coverage, not full provider verification.

---

## 5) OpenClaw hook contract (must hold when OpenClaw is installed or targeted)

If OpenClaw is not installed on this host and you are validating a wrapper-based path, skip this section and continue with the wrapper contract instead.

`omni-recovery` must reliably handle:
- gateway startup events (resume active autonomous runs)
- inbound message events (`message:received`, `message:transcribed`, `message:preprocessed`) using the richest available user text
- `session:compact:before` forwarding so OAA can write a precompact handoff before OpenClaw compacts the session

Verification commands:

```bash
openclaw hooks list
openclaw hooks info omni-recovery
openclaw hooks check
```

If events are missing or disabled, bootstrap is incomplete.

---

## 6) Final-only update policy check (critical)

For requests like "work until <time>", OAA should infer `update_policy=final-only`.

Quick check:

```bash
omni-autonomous-agent --add -R "Continue cleanup until 10:30" -D 30
omni-autonomous-agent --status --json
```

Expected:
- JSON includes `"update_policy": "final-only"`
- stop-hook blocked payloads include `user_update_allowed=false` while still active

This prevents premature progress updates during fixed autonomous windows.

---

## 7) Recovery strategy when setup is broken

Use this exact order:

1. Reproduce one failing command.
2. Fix the failing integration directly (do not broad-reinstall yet).
3. Re-run bootstrap once.
4. Re-run behavior checks (stop, precompact, await-user, closure).
5. If OpenClaw is installed, re-run OpenClaw hook checks.
6. Run test suite and confirm green.

If state is corrupted, use OAA cancel/reset flow, then re-register a fresh session and validate again.

---

## 8) Environment override guidance (generic)

Use env overrides only when defaults are unsuitable or not discoverable.
Keep overrides explicit and minimal.

Useful override classes:
- config file locations
- hook directory locations
- wrapper bin location
- timeout bounds for bootstrap/install
- telemetry/sensitive-context toggles
- startup wake behavior toggles

Tip: always print active env overrides in logs before bootstrap for reproducibility.

---

## 9) Evidence checklist for agents

Before claiming setup is fixed, capture:

- exact commands run
- exit codes
- key stdout/stderr lines
- files/hooks touched
- test results summary

No completion claim without this evidence.

---

## 9B) Verification grades (do not blur these)

Keep provider/OS verification honest and explicit:

- `configured`: files, hooks, wrappers, or plugins were generated
- `callable`: the CLI starts and basic commands succeed
- `authenticated`: the provider can actually reach its backend/API
- `live-verified`: autonomous behavior was exercised end-to-end on that provider/OS

Never collapse these into one claim.

Example:
- "Gemini configured + callable + live-verified" is strong evidence.
- "Claude configured only" is **not** Claude live verification.
- "Windows path logic tested" is **not** Windows live install verification.

If any grade is missing, say so directly.

---

## 10) AI self-setup playbook (non-scripted fallback)

Use this exact order when onboarding fails midway:

1. Confirm runtime tools are callable (`python3`, `omni-autonomous-agent`, and the relevant agent CLI).
2. Rebuild OAA integrations only: `omni-autonomous-agent --bootstrap`.
3. Validate generated config files, plugins, wrappers, and OpenClaw hook files exist and contain the expected OAA commands.
4. Run one dynamic session (`--add ... -D dynamic`), verify blocked stop behavior (`--hook-stop`), await-user timeout or response handling, and clean closure after report status becomes `COMPLETE` or `PARTIAL`.
5. If OpenClaw is installed or targeted, verify OpenClaw discovery and health with `openclaw hooks list`, `openclaw hooks info omni-recovery`, and `openclaw hooks check`.
6. For fresh-machine or Docker verification, prefer headless auth paths and env-backed secrets:
   - Gemini CLI: `GEMINI_API_KEY` or Vertex AI env vars
   - Claude Code: API-key or managed settings/env flow
   - OpenCode: `/connect` or `opencode auth login`, depending build, with env or file secret references
   - Codex/OpenAI: `OPENAI_API_KEY`; use wrappers and shell/MCP integration, not an assumed native hook API
7. If a provider config file was invalid, confirm it was quarantined or replaced safely before continuing.
8. Capture the evidence checklist before claiming setup is fixed.

This sequence is faster and safer than rerunning a full install without diagnosis.

---

## 10B) Provider coverage rule

If a provider CLI is missing, unauthenticated, or unavailable due subscription/access limits:

- you may verify config generation and wrapper/plugin creation
- you may verify CLI discovery if the binary exists
- you must **not** claim live provider verification

Allowed claim examples:
- "OpenCode configured and callable"
- "Gemini live-verified"
- "Claude config path prepared, live verification unavailable on this host"

Disallowed claim examples:
- "All providers verified" when one provider was not actually run
- "Future agents verified" when only wrapper generation was tested

---

## 10C) Cross-OS proof rule

If you do not have a real Windows or macOS machine or VM available:

- test path logic, wrapper generation, and installer logic with simulation/unit tests
- test Linux behavior live where possible
- report Windows/macOS as **simulated coverage only**
- a defined-but-broken VM (missing disk, missing boot media, no boot path, no guest access) does **not** count as real OS availability

Do not present simulated cross-platform checks as real OS verification.

---

## 10D) Preferred repo-native verification ladder

If you are working inside the OAA repository, prefer existing verification entry points over ad-hoc checks.

Run the strongest checks you can for the current environment:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_autonomous_agent tests.test_cross_platform_logic
bash tests/launch_gate.sh
bash tests/launch_gate_clean.sh
bash tests/docker_smoke.sh
bash tests/native_agent_check.sh
bash tests/host_agent_check.sh
bash tests/pwsh_install_smoke.sh
bash tests/macos_smoke.sh
pwsh -File tests/windows_smoke.ps1
```

Interpret them honestly:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest ...`: structural logic proof without dirtying the repo with `__pycache__`
- `tests/launch_gate.sh`: strict release-tree gate; use it on a clean checkout or staged release bundle
- `tests/launch_gate_clean.sh`: convenience wrapper that runs the same gate against a sanitized copy when your working tree contains archived sandboxes, bytecode, or other local runtime residue
- `tests/docker_smoke.sh`: real fresh-machine Linux install + lifecycle proof in Docker
- `tests/native_agent_check.sh`: isolated install/bootstrap/session proof against real agent binaries already available on the current host
- `tests/host_agent_check.sh`: non-destructive verification of generated hooks, wrappers, and host-side agent config on the current machine
- `tests/pwsh_install_smoke.sh`: strong PowerShell installer proof using a PowerShell runtime, but still simulated coverage rather than real Windows
- `tests/macos_smoke.sh`: repo-native smoke flow intended for real macOS runners
- `tests/windows_smoke.ps1`: repo-native smoke flow intended for real Windows runners
- unit tests + launch gate are still not substitutes for live provider/OS proof by themselves

---

## 11) Official references and troubleshooting resources

- OpenClaw hooks docs: `https://docs.openclaw.ai/automation/hooks`
- OpenClaw hooks troubleshooting: `https://docs.openclaw.ai/automation/hooks#troubleshooting`
- Gemini CLI authentication: `https://geminicli.com/docs/get-started/authentication/`
- Gemini CLI configuration: `https://geminicli.com/docs/reference/configuration/`
- Gemini CLI hooks: `https://geminicli.com/docs/hooks/`
- Claude Code hooks: `https://code.claude.com/docs/en/hooks`
- Claude Code hooks guide: `https://code.claude.com/docs/en/hooks-guide`
- Claude Code settings: `https://code.claude.com/docs/en/settings`
- OpenCode plugins: `https://opencode.ai/docs/plugins/`
- OpenCode config: `https://opencode.ai/docs/config/`
- OpenCode providers/auth: `https://opencode.ai/docs/providers/`
- OpenCode CLI: `https://opencode.ai/docs/cli/`
- OpenAI shell tool guide: `https://developers.openai.com/api/docs/guides/tools-shell`
- OpenAI Docs MCP: `https://developers.openai.com/api/docs/mcp`
- OAA issue tracker: `https://github.com/omnious0o0/omni-autonomous-agent/issues`

When reporting issues, include:

- exact command run
- exit code
- stderr/stdout snippet
- OS + shell
- whether you used default paths or env overrides

---

## 12) Agent execution tips for flawless setup

- Prefer targeted fixes over broad reinstall.
- Validate after each fix, not only at the end.
- Keep reports honest: configured vs callable vs successful.
- Never mark COMPLETE without stop-gate, hook, and wrapper verification.
- If anything is ambiguous, fail safe and log assumptions explicitly.


---

## 13) Common failure patterns and deterministic fixes

1. **Bootstrap rerun loop without diagnosis**
   - Symptom: repeated bootstrap attempts with same failure.
   - Fix: capture first failing command + stderr, patch that integration, rerun bootstrap once.

2. **Hooks exist but events are incomplete**
   - Symptom: hook appears installed but autonomous resume does not trigger.
   - Fix: verify hook metadata includes required events and re-bootstrap if missing.

3. **Wrapper exists but stop-gate is bypassed**
   - Symptom: wrapped agent exits while session is still active.
   - Fix: re-validate wrapper contract with active-session preflight + stop-hook loop checks.

4. **Session active but unwanted progress updates**
   - Symptom: user receives updates during fixed deadline autonomous window.
   - Fix: confirm `update_policy=final-only` inference and `user_update_allowed=false` in blocked stop payloads.

5. **Claiming done without closure proof**
   - Symptom: report says complete but session state still active.
   - Fix: require stop-allow evidence (successful final `--hook-stop`) before completion claim.

6. **Provider config file is invalid or stale**
   - Symptom: bootstrap fails, hooks are ignored, or config JSON cannot be parsed.
   - Fix: verify invalid config was quarantined or replaced safely, rerun bootstrap once, then rerun provider-specific checks.
