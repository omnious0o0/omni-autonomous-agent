# omni-autonomous-agent (OAA)

![OAA](https://i.imgur.com/eEAbxoy.png)

## What it does

Makes your AI agent autonomous and able to work for a long or fixed duration, without interruptions. (e.g., "Work overnight," "work on this for 2 hours," "keep working on this until it's done," etc.). Basically any task that could take longer than usual. (3+ minutes)

Your agent gets its own workspace where it can log its memory, reasoning, plans, timestamps of what it's doing, make its own tools and helpers, etc.

Your agent won't stop until one of the conditions is met based on the task you gave it:
- Time's up
- Task is done
- You manually stop it (e.g. "keep doing chores until I stop you")

Manual stop requests are approval-gated: `--cancel` creates a cancellation request, the AI pauses for 30 seconds, and cancellation executes only after explicit user accept (`...` / `--cancel-accept`) while user denial (`..` / `--cancel-deny`) keeps autonomous work running.

Otherwise it won't stop. Even if it goes offline, it will immediately resume when it comes back online.
And none of the "I will now do..." and then doing nothing. That's fixed too.

Don't worry about setup, your agent takes care of it. All you have to do is send it this:
```text
Please install `https://github.com/omnious0o0/omni-autonomous-agent`. Follow all instructions, do not ask questions or give progress updates, please only report back when everything's fully installed and verified. Make sure to follow `install-help.md`.
```
## Install

### Quick & easy
Send this to your AI agent:

```text
Please install `https://github.com/omnious0o0/omni-autonomous-agent`. Follow all instructions, do not ask questions or give progress updates, please only report back when everything's fully installed and verified. Make sure to follow `install-help.md`.
```

### Manual (not recommended)
Use this:

```bash
curl -fsSL https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.sh | bash
```

After install:

1. Run `omni-autonomous-agent --status`
2. Follow `install-help.md` self-check steps

## Support

If you find a bug or unexpected behavior, open an issue with reproduction steps:

https://github.com/omnious0o0/omni-autonomous-agent/issues

---

### Related projects

- [commands-wrapper](https://github.com/omnious0o0/commands-wrapper)
- [extract](https://github.com/omnious0o0/extract)

## License

[MIT](LICENSE)
