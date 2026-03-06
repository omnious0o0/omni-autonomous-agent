import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { spawnSync } from "node:child_process";

type AgentContext = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  channelId?: string;
  messageProvider?: string;
};

type StopPayload = {
  continue?: boolean;
  block?: boolean;
  message?: string;
  template?: string;
  template_id?: string;
  retry_immediately?: boolean;
  pause_then_resume_seconds?: number;
};

const pendingResumeTimers = new Map<string, NodeJS.Timeout>();

const readText = (value: unknown): string => {
  if (typeof value !== "string") return "";
  return value.trim();
};

const parsePositiveInt = (value: unknown): number => {
  const parsed =
    typeof value === "number"
      ? value
      : Number.parseInt(readText(value), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0;
  return parsed;
};

const resolveHome = (): string => (process.env.HOME ?? process.env.USERPROFILE ?? "").trim();

const buildRuntimeEnv = (): NodeJS.ProcessEnv => {
  const env = { ...process.env };
  const entries: string[] = [];

  const addEntries = (value: string | undefined) => {
    if (!value) return;
    for (const raw of value.split(delimiter)) {
      const entry = raw.trim();
      if (entry) entries.push(entry);
    }
  };

  addEntries(process.env.PATH);

  const home = resolveHome();
  if (home) {
    entries.push(join(home, ".local", "bin"));
    entries.push(join(home, ".npm-global", "bin"));
    entries.push(join(home, ".pnpm-global", "bin"));
    if (process.platform === "win32") {
      entries.push(join(home, "AppData", "Roaming", "npm"));
      entries.push(join(home, "AppData", "Local", "omni-autonomous-agent", "bin"));
    }
  }

  if (process.platform === "win32") {
    entries.push("C:/Program Files/nodejs", "C:/Program Files (x86)/nodejs");
  } else {
    entries.push("/usr/local/bin", "/usr/bin", "/bin");
  }

  const seen = new Set<string>();
  env.PATH = entries.filter((entry) => {
    if (seen.has(entry)) return false;
    seen.add(entry);
    return true;
  }).join(delimiter);

  return env;
};

const runtimeEnv = buildRuntimeEnv();

const resolveOaaBinary = (): string => {
  const override = readText(process.env.OMNI_AGENT_OAA_BIN);
  if (override) return override;

  const home = resolveHome();
  const candidates: string[] = [];
  if (home) {
    if (process.platform === "win32") {
      candidates.push(
        join(home, "AppData", "Local", "omni-autonomous-agent", "bin", "omni-autonomous-agent.cmd"),
        join(home, ".local", "bin", "omni-autonomous-agent.cmd"),
        join(home, ".local", "bin", "omni-autonomous-agent.exe"),
      );
    } else {
      candidates.push(
        join(home, ".local", "bin", "omni-autonomous-agent"),
        join(home, ".npm-global", "bin", "omni-autonomous-agent"),
      );
    }
  }

  if (process.platform === "win32") {
    candidates.push("C:/Program Files/omni-autonomous-agent/omni-autonomous-agent.cmd");
  } else {
    candidates.push("/usr/local/bin/omni-autonomous-agent", "/usr/bin/omni-autonomous-agent");
  }

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }

  return "omni-autonomous-agent";
};

const oaaBin = resolveOaaBinary();

const quotePosixArg = (value: string): string =>
  `'${value.replace(/'/g, `'\\''`)}'`;

const quoteWindowsArg = (value: string): string =>
  `"${value.replace(/(["^%])/g, "^$1")}"`;

const buildShellCommand = (command: string, args: string[]): string => {
  const quote = process.platform === "win32" ? quoteWindowsArg : quotePosixArg;
  return [command, ...args].map(quote).join(" ");
};

const spawnWithShimFallback = (
  command: string,
  args: string[],
  options: Parameters<typeof spawnSync>[2],
) => {
  const direct = spawnSync(command, args, options);
  const errorCode =
    direct.error && typeof direct.error === "object" && "code" in direct.error
      ? String((direct.error as NodeJS.ErrnoException).code ?? "")
      : "";
  if (errorCode !== "EPERM") {
    return direct;
  }

  return spawnSync(buildShellCommand(command, args), {
    ...options,
    shell: true,
  });
};

const runOaa = (args: string[]) => {
  const result = spawnWithShimFallback(oaaBin, args, {
    stdio: "pipe",
    encoding: "utf-8",
    env: {
      ...runtimeEnv,
      OMNI_AGENT_HOOK_WRAPPER: "1",
    },
  });

  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim();
  if (typeof result.status === "number") {
    return {
      ok: result.status === 0,
      output,
      code: result.status,
    };
  }

  if (result.error) {
    const reason = result.error instanceof Error ? result.error.message : String(result.error);
    return {
      ok: false,
      output: [output, reason].filter(Boolean).join("\n").trim(),
      code: result.status ?? -1,
    };
  }

  return {
    ok: result.status === 0,
    output,
    code: result.status ?? 0,
  };
};

const parseStopPayload = (output: string): StopPayload | null => {
  const raw = output.trim();
  if (!raw.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as StopPayload;
  } catch {
    return null;
  }
};

const clearPendingResume = (sessionKey: string): void => {
  const key = readText(sessionKey);
  if (!key) return;
  const timer = pendingResumeTimers.get(key);
  if (!timer) return;
  clearTimeout(timer);
  pendingResumeTimers.delete(key);
};

const enqueueContinuation = (
  api: OpenClawPluginApi,
  ctx: AgentContext,
  payload: StopPayload,
): void => {
  const sessionKey = readText(ctx.sessionKey);
  if (!sessionKey) {
    api.logger.warn("omni-autonomous-agent: cannot resume session without a sessionKey");
    return;
  }

  const template = readText(payload.template) || readText(payload.message);
  if (!template) {
    api.logger.warn("omni-autonomous-agent: stop payload missing continuation template");
    return;
  }

  const agentId = readText(ctx.agentId) || undefined;
  const sessionId = readText(ctx.sessionId) || sessionKey;
  const templateId = readText(payload.template_id) || "stop";
  const schedule = () => {
    pendingResumeTimers.delete(sessionKey);
    api.runtime.system.enqueueSystemEvent(template, {
      sessionKey,
      contextKey: `oaa:${templateId}:${sessionId}:${Date.now()}`,
    });
    api.runtime.system.requestHeartbeatNow({
      reason: `oaa:${templateId}`,
      sessionKey,
      agentId,
    });
  };

  const pauseSeconds = parsePositiveInt(payload.pause_then_resume_seconds);
  if (pauseSeconds > 0) {
    clearPendingResume(sessionKey);
    const timer = setTimeout(schedule, pauseSeconds * 1000);
    timer.unref?.();
    pendingResumeTimers.set(sessionKey, timer);
    api.logger.info?.(
      `omni-autonomous-agent: scheduled resume for ${sessionKey} in ${pauseSeconds}s`,
    );
    return;
  }

  clearPendingResume(sessionKey);
  schedule();
};

const recordSessionBinding = (api: OpenClawPluginApi, ctx: AgentContext): void => {
  const sessionId = readText(ctx.sessionId);
  if (!sessionId) return;

  const args = [
    "--record-openclaw-route",
    "--openclaw-agent-id",
    readText(ctx.agentId) || "main",
    "--openclaw-session-id",
    sessionId,
  ];

  const sessionKey = readText(ctx.sessionKey);
  if (sessionKey) args.push("--openclaw-session-key", sessionKey);

  const channelId = readText(ctx.channelId) || readText(ctx.messageProvider);
  if (channelId) args.push("--openclaw-reply-channel", channelId);

  const result = runOaa(args);
  if (!result.ok) {
    api.logger.warn(`omni-autonomous-agent: failed to record OpenClaw session binding: ${result.output}`);
  }
};

export default function register(api: OpenClawPluginApi) {
  api.on("before_agent_start", async (_event, ctx) => {
    recordSessionBinding(api, ctx);
    clearPendingResume(readText(ctx.sessionKey));
  });

  api.on("agent_end", async (_event, ctx) => {
    const sessionKey = readText(ctx.sessionKey);
    if (!sessionKey) {
      api.logger.warn("omni-autonomous-agent: skipping stop-gate enforcement without sessionKey");
      return;
    }

    recordSessionBinding(api, ctx);

    const stop = runOaa(["--hook-stop"]);
    const payload = parseStopPayload(stop.output);
    if (payload === null) {
      if (!stop.ok) {
        api.logger.warn(
          `omni-autonomous-agent: --hook-stop failed without JSON payload: ${stop.output || `exit=${stop.code}`}`,
        );
      }
      return;
    }

    if (payload.continue !== true || payload.block === false) {
      return;
    }

    if (parsePositiveInt(payload.pause_then_resume_seconds) > 0) {
      enqueueContinuation(api, ctx, payload);
      return;
    }

    if (payload.retry_immediately === true) {
      enqueueContinuation(api, ctx, payload);
      return;
    }

    api.logger.info?.(
      `omni-autonomous-agent: stop-gate blocked without immediate resume for ${sessionKey}`,
    );
  });

  api.on("session_end", async (_event, ctx) => {
    clearPendingResume(readText(ctx.sessionKey));
  });
}
