# omni-autonomous-agent (OAA)

![OAA](https://i.imgur.com/eEAbxoy.png)

## Install

Install with the official one-time command:

```bash
curl -fsSL https://raw.githubusercontent.com/omnious0o0/omni-autonomous-agent/main/.omni-autonomous-agent/install.sh | bash
```

After install:

1. Run `omni-autonomous-agent --status`
2. Follow `install-help.md` self-check steps

## What it does

- Registers and tracks autonomous sessions
- Enforces stop gating to prevent premature termination
- Maintains autonomous handoff/checkpoint hooks
- Configures supported agent integrations with `--bootstrap`

## Support

If you find a bug or unexpected behavior, open an issue with reproduction steps:

https://github.com/omnious0o0/omni-autonomous-agent/issues

---

### Related projects

- [commands-wrapper](https://github.com/omnious0o0/commands-wrapper)
- [extract](https://github.com/omnious0o0/extract)

## License

[MIT](LICENSE)
