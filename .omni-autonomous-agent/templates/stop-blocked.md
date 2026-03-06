[OAA STOP BLOCK]
Do not stop. Continue autonomous execution.

Request: {request}
Now: {now}
Deadline: {deadline}
Time remaining: {time_remaining}
Report status: {report_status}
Sandbox: {sandbox_dir}

Policy:
- Keep working without interruption.
- Make measurable progress now.
- Do not ask the user for clarification unless there is no safe path.
- Keep `REPORT.md` status accurate before each stop retry.
- If user input is missing, continue with the safest assumptions and log them.
- Re-attempt stop only when requirements are met.
- If `update_policy=final-only`, do not send user-facing progress updates until closure or explicit user request.

If you have lost context of `SKILL.md`, reread it now. It is very important (`omni-autonomous-agent` skill).
