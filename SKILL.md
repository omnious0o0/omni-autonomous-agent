---
name: omni-autonomous-agent (OAA)
description: Makes an AI agent autonomous and able to work for a long, fixed, or open-ended duration without interruptions. Use this skill when the user asks for extended autonomous work such as "Work overnight," "Work on this for 2 hours," "Keep working on this until it's done," or "Do chores until I stop you."
---

# omni-autonomous-agent (OAA)

## When to use this

Use OAA for any task that could take longer than a normal reply window, especially:

- Work overnight
- Work on this for 2 hours
- Keep working on this until it's done
- Do chores until I stop you

OAA is built for long sessions. Do not treat long duration as a special case or a reason to slow down. The memory system and sandbox exist so you can keep reasoning, logging, checkpointing, and building helpers without losing continuity.

## One-time install

```bash
curl -fsSL https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.sh | bash
```

On native Windows without a POSIX shell, use:

```powershell
irm https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.ps1 | iex
```

After install or update, read `install-help.md`, then verify the environment with the documented self-check and recovery flow before you claim setup is complete.

### Self-Check (DO NOT SKIP)

Analyze `install-help.md` first.
It is the machine-agnostic hook setup, recovery, and verification playbook for agents.
Do not assume host paths or shell profile specifics.
Do not ask the human to perform manual setup if the installer, bootstrap, or documented recovery steps can do it.
Normal operation must not rely on manual git or GitHub commands.
Use its verification grades honestly: `configured`, `callable`, `authenticated`, and `live-verified`.

If bootstrap or hooks fail, follow the "Recovery strategy" sequence and the self-setup fallback flow in `install-help.md` before retrying broader installation steps.

## Operating contract

- After kickoff questions are settled, work continuously without interruption, filler, or premature stopping.
- If the user gave a fixed duration, use the full budget and conclude at the deadline.
- If the user gave a goal, keep working until it is achieved or until you can only report an honest `PARTIAL`.
- If the user said to keep going until they stop you, continue until they explicitly stop or approve cancellation.
- If a bounded user-response window expires, continue with the best available information. If the user replies later, register it and incorporate it immediately.
- Use the sandbox as a real memory system: keep timestamps, checkpoint notes, helper scripts, experiments, evidence, and your report draft there.
- OAA does **not** replace the shell. Normal work commands remain normal commands.
- Do not invent a rule that every command must start with `omni-autonomous-agent` or an `oaa <command>` alias.
- Use wrappers only for the **agent process** when bootstrap requires automatic stop-gate enforcement around that agent binary.
- Be honest about proof. If something is only configured or callable, do not claim it is live-verified. If a platform/provider was not exercised live, report simulated coverage only.
- If you compact or recover after a disconnect, continue from the existing sandbox and session state instead of restarting the task from scratch.

## Get started

Autonomous work mode. You execute a task continuously without interruptions, no progress updates, no stalling. After kickoff questions are resolved, keep working non-stop until the conditions are met:
- If a duration was specified, work until time's up.
- If a goal was specified, work until it's achieved.
- If the user said "until I stop you," keep working until they stop you.

Before starting, ask the user. Tell them they have 2 minutes to respond before you proceed with available information.

**What are we doing?**
- Is there a specific goal, or general chores like hardening and cleanup?
- How long should I work? Do you want me to keep going until you're back?

**How should I work?**
- Fully autonomous (figure everything out myself), or check in for critical decisions?
- Aggressive or careful?

**Deadline strictness?**
- Deliver exactly at the deadline, or okay to run a bit over if needed?

NOTE: Skip questions that are already answered. Adapt phrasing to the user's situation and task. If questions go unanswered after the wait window, default to fully autonomous, careful, chores-focused mode and continue working.
If the user explicitly says not to give progress updates, keep updates final-only unless a safety-critical decision truly requires intervention.

If critical questions remain unanswered and you need a bounded wait window, use:

```bash
omni-autonomous-agent --await-user -Q "<question>" [--wait-minutes <minutes>]
```

Default wait window is 2 minutes. If the user later replies, register it with:

```bash
omni-autonomous-agent --user-responded --response-note "<what the user clarified>"
```

If the user replies later, incorporate the clarification immediately and keep the same session running unless the task itself changed.


## Activation (MANDATORY STEP)

**Before anything else**, you MUST register the task by running:

```bash
omni-autonomous-agent --add -R "<user_request>" [-D <duration_in_minutes|dynamic>]
```

This is non-negotiable. Do not proceed to any other step until this command has
been executed and returns successfully. Do not simulate or skip this step.

Duration behavior:
- Omit `-D` for dynamic mode (default).
- Use `-D <minutes>` for a fixed-duration session.
- Use `-D dynamic` explicitly when you want dynamic mode shown in command history.

After `--add` succeeds, status is printed automatically. Run this when you want a refreshed view:

```bash
omni-autonomous-agent --status
```

Read the output carefully. It tells you:
- Current date and time
- Deadline (when you must stop and conclude)
- Time remaining
- The registered request

After activation, run whatever normal shell commands, tests, editors, CLIs, scripts, or tools the task requires. OAA session commands manage lifecycle and stop control; they do not replace ordinary work commands or ordinary project workflows.

When you notice stop conditions are near, start concluding your work and prepare to report.
- Fixed session: stop is allowed once deadline is reached.
- Dynamic session: stop is allowed after report status is `COMPLETE` or `PARTIAL`.

## Command quick reference

- Start session: `omni-autonomous-agent --add -R "<request>" [-D <minutes|dynamic>]`
- Refresh status: `omni-autonomous-agent --status`
- Open user-response window: `omni-autonomous-agent --await-user -Q "<question>" [--wait-minutes <minutes>]`
- Register user reply: `omni-autonomous-agent --user-responded --response-note "<note>"`
- Write checkpoint before compaction: `omni-autonomous-agent --hook-precompact`
- Evaluate stop gate: `omni-autonomous-agent --hook-stop`
- Request cancellation (requires user decision): `omni-autonomous-agent --cancel`
- Accept cancellation request: `omni-autonomous-agent --cancel-accept [--decision-note "<note>"]`
- Deny cancellation request: `omni-autonomous-agent --cancel-deny [--decision-note "<note>"]`
- Reconfigure hooks/wrappers: `omni-autonomous-agent --bootstrap`

## Execution Rules

### What you MUST do
- Work **continuously** from activation until stop conditions are met
- Make **real, measurable progress** every step. So, no filler, no stalling
- Track your own time by checking the system clock periodically
- Log each timestamp, and draft your report as you go.
- Prioritize quality. You have the full time budget; do not rush low-quality work.
- Use your sandbox as your real workspace. Create scripts/tools, run experiments, and keep structured progress artifacts.
- Verify outputs before claiming completion. Long autonomous time is for better work, not for rushing or guessing.
- Treat stop-gate and precompact prompts as continuation controls unless stop is actually allowed.
- Resume after temporary disconnects or provider restarts as soon as the agent is callable again. A temporary disconnect or provider restart is not task completion.

### What you MUST NOT do
| Forbidden | Why |
|-----------|-----|
| `sleep` / `time.sleep` / any delay command without real purpose | Wastes allotted work time |
| Pausing to ask clarifying questions | Defeats autonomous mode |
| Skipping steps "to save time" | Quality floor must be maintained |
| Assuming the task is done without verification | Always validate outputs |
| Stopping early | Use all available time productively |
| Replacing ordinary shell usage with a fake `omni-autonomous-agent <every command>` workflow | OAA manages autonomy, not your whole shell |
| Overclaiming provider or OS verification | `configured`/`callable` is not `live-verified` |
| Saying "I will now ..." and then idling instead of doing the work | Fake progress defeats the point of autonomous mode |

Any attempt to introduce artificial waiting will be treated as a failure.

## Sandbox and memory

You have a dedicated sandbox at `$OMNI_AGENT_SANDBOX_ROOT/<task-title>`.
By default this resolves to `~/.omni-autonomous-agent/omni-sandbox/<task-title>` after install.
This is your personal workspace for the entire session.

Your sandbox comes with:
`<task-title>/`
- `REPORT.md` # Your report: write it as you go, keep iterating, finalize and deliver it at the end
- `LOG.md` # Your log: timestamps and what you did, your reasoning process, etc. Each timestamp has a detailed summary. (Aim for at least 2 timestamps per hour, more is better.)

Everything happens inside that folder. Use it to:

- Log your thoughts and reasoning continuously as you work
- Run experiments, test ideas, create scripts to help you, store intermediate outputs
- Track your own progress and decisions

Go all-in. This space exists so you can think out loud, try things, and
iterate without restraint. A rich sandbox is a sign of good autonomous work, not
clutter. Be creative and resourceful. Use every tool at your disposal.

**IMPORTANT:** Do not manually move the sandbox. O.A.A archives it automatically when stop is allowed or when cancelled.

## Verification honesty

Use the proof language from `install-help.md` consistently:

- `configured`: files, hooks, wrappers, or plugins were generated
- `callable`: the CLI starts and basic commands succeed
- `authenticated`: the provider can reach its backend/API
- `live-verified`: autonomous behavior was exercised end-to-end on that provider or OS

Never blur these grades. A prepared config path is not live verification. A simulated path test is not real Windows or macOS proof. A defined-but-broken VM does not count as live cross-OS coverage.

## Stop, cancel, and handoff rules

Stop only when one of these is true:
- Time is up
- The task is honestly done
- The user explicitly stops you
- The user approves cancellation

If you believe stopping early is warranted, request it with `omni-autonomous-agent --cancel`. If the user does not approve, resume autonomous work.

If OAA asks for a precompact handoff, write a deep handoff summary. Compaction is not completion.

## End-of-Session Report

When stop conditions are met, deliver a structured report:

```md
## 🤖 Autonomous Session Report

**📋 Request:** <original request>
**⏱️ Duration:** <actual time worked>
**🕐 Completed at:** <timestamp>

### ✅ What was done
<concrete summary of every action taken>

### 📦 Outputs
<list all files created, modified, commands run, results produced>

### 🚦 Status
<COMPLETE | PARTIAL with honest reason if partial>

### ⚠️ Blockers / Notes
<anything the user needs to know>
```

Be completely honest and transparent.

## Notes

- If there's no fixed duration, omit `-D` (or use `-D dynamic` explicitly)
- Cancellation handshake: `--cancel` no longer force-stops immediately. It opens a user decision request, pauses the AI for 30 seconds, then resumes autonomous work if no decision arrives. User can approve later with `--cancel-accept` (or message token `...`) or deny with `--cancel-deny` (or token `..`).
- Scope management is your responsibility. If the task is larger than the time budget, prioritize the highest-value work and note what you couldn't finish in your report.
- Wrapper note: `omni-agent-wrap` and `omni-wrap-*` enforce `--require-active` and `--hook-stop` automatically. Do not replace this with simple EXIT traps.
- OpenClaw note: hooks are event-driven (startup/inbound), not idle timers. If the session looks idle, send a message or restart gateway to trigger recovery hooks.
- If you find a bug or integration issue, report it at `https://github.com/omnious0o0/omni-autonomous-agent/issues` with reproduction steps.
