[OAA STOP BLOCK - FIXED SESSION]
Do not stop. Continue autonomous execution until the fixed deadline is reached.

Request: {request}
Now: {now}
Deadline: {deadline}
Time remaining: {time_remaining}
Report status: {report_status}
Sandbox: {sandbox_dir}

Policy:
- Keep working without interruption.
- Prioritize measurable progress before the fixed deadline.
- Keep `REPORT.md` status accurate while work is in progress.
- If user input is missing, continue with the safest assumptions and log them.
- Re-attempt stop only after the fixed deadline is reached.
- If `update_policy=final-only`, do not send user-facing progress updates until closure or explicit user request.
