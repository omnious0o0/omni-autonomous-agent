---
name: omni-autonomous-agent
description: Makes an AI agent autonomous and able to work for a long time, or a fixed duration without interruptions. Use this skill when the user asks you to work autonomously (e.g., "Work overnight," "I'll be gone for a few hours, work on this," "keep working on this until it's done," etc.). Basically any task that could take longer than usual. (3+ minutes)
---

# omni-autonomous-agent

## One-time install

```bash
curl -fsSL https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.sh | bash
```

### Self-Check (DO NOT SKIP)

Analyze `install-help.md` for instructions to configure and set up hooks so you don't fill up your context window or stop prematurely.

## Get started

Autonomous work mode. You execute a task continuously without interruptions, no progress updates, no questions, no stalling. You keep working non-stop until the conditions are met:
- If a duration was specified, work until time's up.
- If a goal was specified, work until it's achieved.

Before starting, ask the user:

**What are we doing?**
- Is there a specific goal, or general chores like hardening and cleanup?
- How long should I work? Do you want me to keep going until you're back?

**How should I work?**
- Fully autonomous (figure everything out myself), or check in for critical decisions?
- Aggressive or careful?

**Deadline strictness?**
- Deliver exactly at the deadline, or okay to run a bit over if needed?

NOTE: Skip questions that are already answered. Adapt phrasing to the user's situation and task. If questions go unanswered, default to fully autonomous, careful, chores-focused mode.


## Activation (MANDATORY STEP)

**Before anything else**, you MUST register the task by running:

```bash
omni-autonomous-agent --add -R "<user_request>" [-D <duration_in_minutes|dynamic>]
```

This is non-negotiable. Do not proceed to any other step until this command has
been executed and returns successfully. Do not simulate or skip this step.

After `--add` succeeds, status is printed automatically. Run this when you want a refreshed view:

```bash
omni-autonomous-agent --status
```

Read the output carefully. It tells you:
- Current date and time
- Deadline (when you must stop and conclude)
- Time remaining
- The registered request

When you notice stop conditions are near, start concluding your work and prepare to report.
- Fixed session: stop is allowed once deadline is reached.
- Dynamic session: stop is allowed after report status is `COMPLETE` or `PARTIAL`.

## Execution Rules

### What you MUST do
- Work **continuously** from activation until stop conditions are met
- Make **real, measurable progress** every step. So, no filler, no stalling
- Track your own time by checking the system clock periodically
- Log each timestamp, and draft your report as you go.
- Prioritize quality, you got all the time you need, no need to rush anything.
- Use your sandbox to its fullest extent. Do not treat it as just a dump folder for you to draft in, treat it as your workspace, create your own tools, your own environment, be creative!

### What you MUST NOT do
| Forbidden | Why |
|-----------|-----|
| `sleep` / `time.sleep` / any delay command without real purpose | Wastes allotted work time |
| Pausing to ask clarifying questions | Defeats autonomous mode |
| Skipping steps "to save time" | Quality floor must be maintained |
| Assuming the task is done without verification | Always validate outputs |
| Stopping early | Use all available time productively |

Any attempt to introduce artificial waiting will be treated as a failure.

## Sandbox

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
clutter. Be creative and resourceful. Use every tool at your disposal, be creative and have freedom.

**IMPORTANT:** Do not manually move the sandbox. O.A.A archives it automatically when stop is allowed or when cancelled.

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

- When a user uses this skill, 99% of the time they're away. Do not try to communicate with them. Work with what you have; be creative and resourceful.
- If there's no duration, use `-D dynamic` instead of `-D [duration]`
- Kill-switch: To cancel a session mid-way (DON'T DO IT WITHOUT A REASON), run `omni-autonomous-agent --cancel`. Stop all work immediately and skip the end-of-session report. Only do this if user explicitly asks/approves.
- Scope management is your responsibility. If the task is larger than the time budget, prioritize the highest-value work and note what you couldn't finish in your report.
