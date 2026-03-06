# omni-autonomous-agent (OAA)

![OAA](https://i.imgur.com/eEAbxoy.png)

## What it does

Makes your AI agent autonomous and able to work for a long or fixed duration, without interruptions. For example:
- Work overnight
- Work on this for 2 hours
- Keep working on this until it's done
- Do chores until I stop you
...

Don't worry about the duration of the task. There's a memory system.

Basically any task that could take longer than usual.

If OAA needs missing constraints before a long run, it tells the agent to give you 2 minutes to respond, then continue with the safest available assumptions if you do not reply in time.

Your agent gets its own workspace where it can log its memory, reasoning, plans, timestamps of what it's doing, make its own tools and helpers, etc.

Your agent won't stop until one of the conditions is met based on the task you gave it:
- Time's up
- Task is done
- You manually stop it (e.g. "keep doing chores until I stop you")

> **NOTE:** Your AI agent can request to stop, once it sends the request it becomes idle for 30 seconds and waits for your approval/denial. If you don't respond within 30 seconds or deny, it will resume autonomous work.

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
Use the entry point that matches the host:

```bash
curl -fsSL https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.sh | bash
```

```powershell
irm https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.ps1 | iex
```

After install:

1. Run `omni-autonomous-agent --status`
2. Follow `install-help.md` self-check steps

### Agent setup tip

Treat `install-help.md` as the canonical hook setup playbook.
It is intentionally machine-agnostic: validate behavior with commands and outputs, not host-specific path assumptions.

## Support

If you found this project useful, please consider starring the repo and dropping me a follow for more stuff like this :)
It takes less than a minute and helps a lot ❤️

> If you find a bug or unexpected behavior, please report it!

---


**RECOMMENDED:** Check out [commands-wrapper](https://github.com/omnious0o0/commands-wrapper) you and your agent will love it!

---

If you want to show extra love, consider *[buying me a coffee](https://buymeacoffee.com/specter0o0)*! ☕


[![Buy Me a Coffee](https://imgs.search.brave.com/FolmlC7tneei1JY_QhD9teOLwsU3rivglA3z2wWgJL8/rs:fit:860:0:0:0/g:ce/aHR0cHM6Ly93aG9w/LmNvbS9ibG9nL2Nv/bnRlbnQvaW1hZ2Vz/L3NpemUvdzIwMDAv/MjAyNC8wNi9XaGF0/LWlzLUJ1eS1NZS1h/LUNvZmZlZS53ZWJw)](https://buymeacoffee.com/specter0o0)

### Related projects

- [commands-wrapper](https://github.com/omnious0o0/commands-wrapper)
- [extract](https://github.com/omnious0o0/extract)

**And more on [omnious](https://github.com/omnious0o0)!**

## License

[MIT](LICENSE)