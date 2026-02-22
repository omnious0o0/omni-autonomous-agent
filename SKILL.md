---
name: omni-autonomous-agent
description: Makes an AI agent autonomous and able to work for a long time, or a fixed duration without interruptions. Use the skill when user asks you to work autonomously (EG. Work overnight, I'll be gone for few hours wrok on this, keep working on this until it's done, etc.)
---

# omni-autonomous-agent

## One-time install

```bash
curl -fsSL https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.sh | bash
```

### Self-Check (DO NOT SKIP)

Analyze `install-help.md` for instructions to configure and set up hook so you don't fill up your context window or stop prematurely.

## Get started

Autonomous work mode. You execute a task continuously without interruptions, no progress updates, no questions, no stalling. You keep working non-stop until the conditions are met:
- if user specified duration, you must keep working until time's up.
- if user specified certain goal, you must keep working until you achieve it.

Before starting, please ask your user to answer the following questions:
```
- Is there a goal, or just chores like hardening & cleaning up carefully?
- What is the duration? Do you want me to keep working until you're back?

- MODE: do you want me to be full autonomous, figure everything out myself (recomended if you're away), or do you want me to ask you questions for critical decisions?
- how strict is the deadline? Do you want me to deliver report exactly at the deadline, or is it ok if I finish a bit later?
- How would you like me to work? Do you want be to be agressive, or careful?
```

NOTE: Please adapt dynamically, so skip questions that already have an answer, slightly change phrasing based on your user's prefrences, current situation or task. If the user doesn't answer a question, that's alright, default to being completely autonomous and careful, doing chores.


## Activation (MANDATORY STEP)

**Before anything else**, you MUST register the task by running:

```bash
omni-autonomous-agent --add -R "<user_request>" -D <duration_in_minutes>
```

This is non-negotiable. Do not proceed to any other step until this command has
been executed and returns successfully. Do not simulate or skip this step.

After `--add` succeeds, run:

```bash
omni-autonomous-agent --status
```

Read the output carefully. It tells you:
- Current date and time
- Deadline (when you must stop and conclude)
- Time remaining
- The registered request

When you notice the deadline is near, start concluding your work and prepare to report.

## Execution Rules

### What you MUST do
- Work **continuously** from activation until the deadline
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

You have a dedicated sandbox at `omni-autonomous-agent/omni-sandbox/<task-title>`. This is your
personal workspace for the entire session.

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

**IMPORTANT:** Once the task is done, put the task sandbox in the `archived` folder.

## End-of-Session Report

When the deadline is reached, deliver a structured report:

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

Be completely honest and transparrent. 

## Notes

- When user uses this skill, 99% of the time they're away. Do not try to communicate with them. Work with what you have, be creative and resourceful.
- If there's no duration, use `-D dynamic` instead of `-D [duration]`
- Kill-switch: To cancel a session mid-way (DON'T DO IT WITHOUT A REASON), run `omni-autonomous-agent --cancel`. Stop all work immediately and skip the end-of-session report. Only do this if user explicitly asks/approves.
- Scope management is your responsibility. If the task is larger than the time budget, prioritize the highest-value work and note what you couldn't finish in your report.