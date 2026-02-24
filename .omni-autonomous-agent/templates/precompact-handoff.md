[OAA PRECOMPACT HANDOFF]

Request: {request}
Now: {now} | Elapsed: {elapsed} | Deadline: {deadline}
Sandbox: {sandbox_dir} | Report: {report_path} | Log: {log_path}

---

Your task is to write a comprehensive and deep handoff for the next model instance that will continue this work. Another agent will read ONLY this - nothing else survives compaction.

Before writing, wrap your analysis in `<analysis>` tags. Chronologically review every message and action. Identify: what changed, what failed, what was learned, where reasoning drifted, what assumptions were made. Then write the handoff below.

<analysis>
[Trace the full session chronologically. Flag any ambiguities, assumptions, or silent failures. Verify your understanding of the current task before writing the handoff.]
</analysis>

---

## 1. Primary Goal & Intent
What is the user ultimately trying to accomplish? Include sub-goals if any. Be precise and do not generalize.

## 2. Session State
- **Status:** [In progress / Blocked / Needs decision / Ready for next step]
- **Risk level:** [Low / Medium / High] - reason:
- **Deadline pressure:** [from {deadline} vs {elapsed}]

## 3. Completed Work
List every significant action taken, in order. For each:
- What was done
- Why it was done
- What file or system was affected
- Whether it was verified or just written
- Which command or check verified it (if applicable)

## 4. Files & Code State
For each file touched:
- `path/to/file` - what changed, why it matters, current state
- Include critical code snippets if losing them would cause drift or rework

## 5. Key Technical Concepts & Decisions
Decisions made, patterns established, constraints discovered, and why they matter. Include things learned that weren't in the original plan.

## 6. Problems & Attempts
### Solved
- Problem → what fixed it

### Failed / Abandoned
- Attempt → why it didn't work → current status

### Open / Unresolved
- Problem → last known state → what's needed to unblock

## 7. Pending Tasks
Ordered by priority:
1. [Task] - context needed to execute
2. ...

## 8. Current Work (Exact State)
Describe precisely what was being worked on at the moment of compaction. Include the exact file, function, line of reasoning, or step in a sequence. If mid-implementation, describe the incomplete state.

> **Verbatim last task** (copy exact wording to prevent drift):
> "{quote the most recent explicit task or instruction from the conversation}"

## 9. Immediate Next Action
The single first thing the next model should do. Be specific enough to execute without re-reading anything.

## 10. Blockers & Handoff Notes
Any information that doesn't fit above but would cause the next model to make a wrong move if missing.
