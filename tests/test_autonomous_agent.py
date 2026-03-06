from __future__ import annotations

import itertools
import json
import os
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import main as launcher_main

sys.dont_write_bytecode = True


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_SCRIPT = PROJECT_ROOT / "main.py"
INTERNAL_PKG_COUNTER = itertools.count()


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAIN_SCRIPT), *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    output = result.stdout.strip()
    if not output:
        return {}
    return json.loads(output.splitlines()[-1])


def _load_internal_modules(*module_names: str) -> dict[str, object]:
    pkg_dir = PROJECT_ROOT / ".omni-autonomous-agent"
    pkg_name = f"omni_agent_test_{next(INTERNAL_PKG_COUNTER)}"
    launcher_main._load_package(pkg_name, str(pkg_dir))

    loaded: dict[str, object] = {}
    for module_name in module_names:
        loaded[module_name] = launcher_main._load_module(
            pkg_name, module_name, str(pkg_dir)
        )
    return loaded


class AutonomousAgentHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self._temp_dir.name)

        self.home_dir = temp_root / "home"
        self.home_dir.mkdir(parents=True, exist_ok=True)

        self.config_dir = temp_root / "config"
        self.sandbox_root = temp_root / "sandbox"
        self.bin_dir = temp_root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home_dir)
        self.env["OMNI_AGENT_CONFIG_DIR"] = str(self.config_dir)
        self.env["OMNI_AGENT_SANDBOX_ROOT"] = str(self.sandbox_root)
        self.env["OMNI_AGENT_REPO_ROOT"] = str(PROJECT_ROOT)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env.pop("AGENT", None)

        cli_shim = self.bin_dir / "omni-autonomous-agent"
        cli_shim.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'exec "{sys.executable}" "{MAIN_SCRIPT}" "$@"\n',
            encoding="utf-8",
        )
        cli_shim.chmod(0o755)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _state_file(self) -> Path:
        return self.config_dir / "state.json"

    def _read_state(self) -> dict[str, object]:
        return json.loads(self._state_file().read_text(encoding="utf-8"))

    def _read_log(self) -> str:
        state = self._read_state()
        sandbox_dir = Path(str(state["sandbox_dir"]))
        return (sandbox_dir / "LOG.md").read_text(encoding="utf-8")

    def _write_fake_binary(self, name: str) -> None:
        binary = self.bin_dir / name
        binary.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "--exit-code" ]]; then\n'
            '  code="${2:-0}"\n'
            '  exit "$code"\n'
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)

    def _write_openclaw_failing_binary(self) -> None:
        binary = self.bin_dir / "openclaw"
        binary.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "hooks" && "${2:-}" == "enable" ]]; then\n'
            "  exit 42\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)

    def _write_openclaw_partial_binary(self) -> None:
        binary = self.bin_dir / "openclaw"
        binary.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "hooks" && "${2:-}" == "enable" && "${3:-}" == "omni-recovery" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "${1:-}" == "hooks" && "${2:-}" == "enable" && "${3:-}" == "session-memory" ]]; then\n'
            "  exit 42\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)

    def _run_openclaw_handler(
        self,
        *,
        status_payload: dict[str, object],
        event: dict[str, object],
        extra_events: list[dict[str, object]] | None = None,
        session_record: dict[str, object] | None = None,
        session_store: dict[str, object] | None = None,
        session_key: str = "agent:main:main",
        require_active_code: int = 0,
        write_session_file: bool = True,
        precreated_lock_dirs: list[str] | None = None,
        sync_launch: bool = True,
        cli_store_payloads: list[dict[str, object]] | None = None,
        cli_sessions_override: list[dict[str, object]] | None = None,
        fake_openclaw_script: str | None = None,
    ) -> tuple[str, str]:
        node_bin = shutil.which("node")
        if node_bin is None:
            self.skipTest("node is required for OpenClaw handler execution tests")

        node_help = subprocess.run(
            [node_bin, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        if "--experimental-transform-types" not in node_help.stdout:
            self.skipTest(
                "node --experimental-transform-types is required for OpenClaw handler execution tests"
            )

        bootstrap = _load_internal_modules("bootstrap")["bootstrap"]
        handler_source = bootstrap._openclaw_handler_ts()

        with tempfile.TemporaryDirectory() as work_dir:
            root = Path(work_dir)
            home_dir = root / "home"
            bin_dir = root / "bin"
            sessions_dir = home_dir / ".openclaw" / "agents" / "main" / "sessions"
            config_dir = home_dir / ".config" / "omni-autonomous-agent"
            bin_dir.mkdir(parents=True, exist_ok=True)

            if session_record is None:
                session_record = {
                    "sessionId": "session-123",
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                }
            sessions_payload = (
                session_store if session_store is not None else {session_key: session_record}
            )

            if write_session_file:
                sessions_dir.mkdir(parents=True, exist_ok=True)
                (sessions_dir / "sessions.json").write_text(
                    json.dumps(sessions_payload, indent=2),
                    encoding="utf-8",
                )

            cli_store_entries: list[dict[str, object]] = []
            for store_spec in cli_store_payloads or []:
                relative_path = str(store_spec.get("relative_path", "")).strip()
                store_sessions = store_spec.get("sessions")
                if not relative_path or not isinstance(store_sessions, dict):
                    continue
                store_path = root / relative_path
                store_path.parent.mkdir(parents=True, exist_ok=True)
                store_path.write_text(
                    json.dumps(store_sessions, indent=2), encoding="utf-8"
                )
                cli_store_entries.append(
                    {
                        "agentId": str(store_spec.get("agentId", "main")),
                        "path": str(store_path),
                    }
                )

            for lock_dir in precreated_lock_dirs or []:
                (config_dir / lock_dir).mkdir(parents=True, exist_ok=True)

            handler_path = root / "handler.ts"
            handler_path.write_text(handler_source, encoding="utf-8")

            oaa_log = root / "oaa.log"
            openclaw_log = root / "openclaw.log"

            fake_oaa = bin_dir / "omni-autonomous-agent"
            fake_oaa.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'cmd="${1:-}"\n'
                'if [[ "${cmd}" == "--status" ]]; then\n'
                '  printf "%s\\n" "${TEST_HOOK_STATUS_JSON}"\n'
                "  exit 0\n"
                "fi\n"
                'if [[ "${cmd}" == "--require-active" ]]; then\n'
                '  exit "${TEST_HOOK_REQUIRE_ACTIVE_CODE:-0}"\n'
                "fi\n"
                'printf "%s\\n" "$*" >> "${TEST_HOOK_OAA_LOG}"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_oaa.chmod(0o755)

            fake_openclaw = bin_dir / "openclaw"
            sessions_cli_payload = ""
            if cli_sessions_override is not None:
                cli_sessions = list(cli_sessions_override)
            elif session_store is not None or write_session_file:
                cli_sessions = []
                for key, entry in sessions_payload.items():
                    if not isinstance(entry, dict):
                        continue
                    session_id = str(entry.get("sessionId", "")).strip()
                    if not session_id:
                        continue
                    updated_at = entry.get("updatedAt")
                    cli_sessions.append(
                        {
                            "key": key,
                            "sessionId": session_id,
                            "updatedAt": (
                                updated_at
                                if isinstance(updated_at, (int, float))
                                else 0
                            ),
                            "agentId": "main",
                        }
                    )
            else:
                cli_sessions = []
            if cli_sessions or cli_store_entries:
                sessions_cli_payload = json.dumps(
                    {"stores": cli_store_entries, "sessions": cli_sessions}
                )
            fake_openclaw.write_text(
                fake_openclaw_script
                if fake_openclaw_script is not None
                else (
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    'if [[ "${1:-}" == "sessions" ]]; then\n'
                    '  if [[ -n "${TEST_HOOK_OPENCLAW_SESSIONS_JSON:-}" ]]; then\n'
                    '    printf "%s\\n" "${TEST_HOOK_OPENCLAW_SESSIONS_JSON}"\n'
                    "    exit 0\n"
                    "  fi\n"
                    "  exit 1\n"
                    "fi\n"
                    'printf "%s\\n" "$*" >> "${TEST_HOOK_OPENCLAW_LOG}"\n'
                    "exit 0\n"
                ),
                encoding="utf-8",
            )
            fake_openclaw.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home_dir),
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                    "OMNI_AGENT_OAA_BIN": str(fake_oaa),
                    "OMNI_AGENT_OPENCLAW_BIN": str(fake_openclaw),
                    "TEST_HOOK_STATUS_JSON": json.dumps(status_payload),
                    "TEST_HOOK_REQUIRE_ACTIVE_CODE": str(require_active_code),
                    "TEST_HOOK_OAA_LOG": str(oaa_log),
                    "TEST_HOOK_OPENCLAW_LOG": str(openclaw_log),
                    "TEST_HOOK_OPENCLAW_SESSIONS_JSON": sessions_cli_payload,
                }
            )
            if sync_launch:
                env["OMNI_AGENT_OPENCLAW_SYNC_LAUNCH"] = "1"

            for current_event in [event, *(extra_events or [])]:
                result = subprocess.run(
                    [
                        node_bin,
                        "--experimental-transform-types",
                        "--input-type=module",
                        "-e",
                        (
                            "import { pathToFileURL } from 'url';"
                            "const mod = await import(pathToFileURL(process.argv[1]).href);"
                            "await mod.default(JSON.parse(process.argv[2]));"
                        ),
                        str(handler_path),
                        json.dumps(current_event),
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    raise AssertionError(
                        "OpenClaw handler execution failed:\n"
                        f"stdout={result.stdout}\n"
                        f"stderr={result.stderr}"
                    )

            deadline = time.time() + 3.0
            while time.time() < deadline:
                if oaa_log.exists() or openclaw_log.exists():
                    break
                time.sleep(0.05)

            oaa_text = oaa_log.read_text(encoding="utf-8") if oaa_log.exists() else ""
            openclaw_text = (
                openclaw_log.read_text(encoding="utf-8")
                if openclaw_log.exists()
                else ""
            )
            return oaa_text, openclaw_text

    def _run_openclaw_plugin_agent_end(
        self,
        *,
        hook_stop_code: int,
        hook_stop_payload: dict[str, object],
        ctx: dict[str, object] | None = None,
        wait_ms: int = 0,
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]], str]:
        node_bin = shutil.which("node")
        if node_bin is None:
            self.skipTest("node is required for OpenClaw plugin execution tests")

        node_help = subprocess.run(
            [node_bin, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        if "--experimental-transform-types" not in node_help.stdout:
            self.skipTest(
                "node --experimental-transform-types is required for OpenClaw plugin execution tests"
            )

        plugin_path = (
            PROJECT_ROOT / ".omni-autonomous-agent" / "openclaw-plugin" / "index.ts"
        )
        self.assertTrue(plugin_path.exists())

        if ctx is None:
            ctx = {
                "agentId": "main",
                "sessionId": "plugin-session-123",
                "sessionKey": "agent:main:main",
            }

        with tempfile.TemporaryDirectory() as work_dir:
            root = Path(work_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)

            oaa_log = root / "oaa.log"
            system_log = root / "system.log"
            heartbeat_log = root / "heartbeat.log"
            plugin_log = root / "plugin.log"

            fake_oaa = bin_dir / "omni-autonomous-agent"
            fake_oaa.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'cmd="${1:-}"\n'
                'if [[ "${cmd}" == "--hook-stop" ]]; then\n'
                '  printf "HOOK_STOP\\n" >> "${TEST_PLUGIN_OAA_LOG}"\n'
                '  printf "%s\\n" "${TEST_PLUGIN_STOP_OUTPUT}"\n'
                '  exit "${TEST_PLUGIN_STOP_CODE:-0}"\n'
                "fi\n"
                'printf "%s\\n" "$*" >> "${TEST_PLUGIN_OAA_LOG}"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_oaa.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                    "OMNI_AGENT_OAA_BIN": str(fake_oaa),
                    "TEST_PLUGIN_OAA_LOG": str(oaa_log),
                    "TEST_PLUGIN_STOP_OUTPUT": json.dumps(hook_stop_payload),
                    "TEST_PLUGIN_STOP_CODE": str(hook_stop_code),
                    "TEST_PLUGIN_SYSTEM_LOG": str(system_log),
                    "TEST_PLUGIN_HEARTBEAT_LOG": str(heartbeat_log),
                    "TEST_PLUGIN_LOG": str(plugin_log),
                }
            )

            result = subprocess.run(
                [
                    node_bin,
                    "--experimental-transform-types",
                    "--input-type=module",
                    "-e",
                    (
                        "import { appendFileSync } from 'fs';"
                        "import { pathToFileURL } from 'url';"
                        "const pluginPath = process.argv[1];"
                        "const ctx = JSON.parse(process.argv[2]);"
                        "const waitMs = Number(process.argv[3]);"
                        "const handlers = new Map();"
                        "const log = (level, message) => appendFileSync(process.env.TEST_PLUGIN_LOG, `${level}:${message}\\n`);"
                        "const api = {"
                        "  logger: {"
                        "    info: (message) => log('info', message),"
                        "    warn: (message) => log('warn', message),"
                        "    error: (message) => log('error', message),"
                        "  },"
                        "  runtime: {"
                        "    system: {"
                        "      enqueueSystemEvent: (text, opts) => {"
                        "        appendFileSync(process.env.TEST_PLUGIN_SYSTEM_LOG, `${JSON.stringify({ text, opts })}\\n`);"
                        "        return true;"
                        "      },"
                        "      requestHeartbeatNow: (opts) => {"
                        "        appendFileSync(process.env.TEST_PLUGIN_HEARTBEAT_LOG, `${JSON.stringify(opts)}\\n`);"
                        "      },"
                        "    },"
                        "  },"
                        "  on: (name, handler) => handlers.set(name, handler),"
                        "};"
                        "const mod = await import(pathToFileURL(pluginPath).href);"
                        "mod.default(api);"
                        "const beforeAgentStart = handlers.get('before_agent_start');"
                        "if (beforeAgentStart) {"
                        "  await beforeAgentStart({ prompt: 'Continue autonomous work.' }, ctx);"
                        "}"
                        "const agentEnd = handlers.get('agent_end');"
                        "if (!agentEnd) throw new Error('agent_end hook not registered');"
                        "await agentEnd({ success: true, messages: [] }, ctx);"
                        "if (waitMs > 0) {"
                        "  await new Promise((resolve) => setTimeout(resolve, waitMs));"
                        "}"
                    ),
                    str(plugin_path),
                    json.dumps(ctx),
                    str(wait_ms),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise AssertionError(
                    "OpenClaw plugin execution failed:\n"
                    f"stdout={result.stdout}\n"
                    f"stderr={result.stderr}"
                )

            oaa_text = oaa_log.read_text(encoding="utf-8") if oaa_log.exists() else ""
            system_events = [
                json.loads(line)
                for line in system_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ] if system_log.exists() else []
            heartbeat_events = [
                json.loads(line)
                for line in heartbeat_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ] if heartbeat_log.exists() else []
            plugin_text = (
                plugin_log.read_text(encoding="utf-8") if plugin_log.exists() else ""
            )
            return oaa_text, system_events, heartbeat_events, plugin_text

    def test_fixed_session_lifecycle(self) -> None:
        status = _run_cli(["--status"], self.env)
        self.assertEqual(status.returncode, 0)
        self.assertIn("No active session", status.stdout)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        empty_payload = json.loads(status_json.stdout)
        self.assertTrue(bool(empty_payload.get("ok")))
        self.assertFalse(bool(empty_payload.get("active")))

        added = _run_cli(["--add", "-R", "fixed lifecycle", "-D", "1"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        sandbox_dir = Path(str(state["sandbox_dir"]))
        self.assertTrue((sandbox_dir / "REPORT.md").exists())
        self.assertTrue((sandbox_dir / "LOG.md").exists())

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 2)
        stop_payload = _json_output(stop)
        self.assertTrue(bool(stop_payload.get("continue")))
        self.assertTrue(bool(stop_payload.get("block")))
        self.assertEqual(stop_payload.get("template_id"), "stop-blocked-fixed")
        self.assertIn("Do not stop", str(stop_payload.get("template", "")))
        self.assertNotIn(
            "dynamic sessions", str(stop_payload.get("message", "")).lower()
        )
        self.assertNotIn("fixed lifecycle", str(stop_payload.get("template", "")))

        precompact = _run_cli(["--hook-precompact"], self.env)
        self.assertEqual(precompact.returncode, 0)
        precompact_payload = _json_output(precompact)
        self.assertFalse(bool(precompact_payload.get("continue")))
        self.assertEqual(precompact_payload.get("template_id"), "precompact-handoff")
        self.assertIn(
            "deep handoff", str(precompact_payload.get("template", "")).lower()
        )
        report_text = (sandbox_dir / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Checkpoint (precompact)", report_text)

        active_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(active_json.returncode, 0)
        active_payload = json.loads(active_json.stdout)
        self.assertTrue(bool(active_payload.get("ok")))
        self.assertTrue(bool(active_payload.get("active")))
        self.assertFalse(bool(active_payload.get("dynamic")))
        self.assertTrue(str(active_payload.get("request", "")).startswith("[request:"))

        cancel_request = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel_request.returncode, 0)
        self.assertTrue(self._state_file().exists())

        cancel_accept = _run_cli(["--cancel-accept"], self.env)
        self.assertEqual(cancel_accept.returncode, 0)
        self.assertFalse(self._state_file().exists())

        archived_dir = self.sandbox_root / "archived"
        self.assertTrue(archived_dir.exists())
        self.assertTrue(any(archived_dir.iterdir()))

    def test_dynamic_session_requires_completion(self) -> None:
        added = _run_cli(["--add", "-R", "dynamic lifecycle"], self.env)
        self.assertEqual(added.returncode, 0)
        self.assertIn("omni-autonomous-agent - active", added.stdout)

        stop_early = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop_early.returncode, 2)
        stop_payload = _json_output(stop_early)
        self.assertEqual(stop_payload.get("template_id"), "stop-blocked")
        self.assertIn("dynamic sessions", str(stop_payload.get("message", "")).lower())
        self.assertIn(
            "continue autonomous", str(stop_payload.get("template", "")).lower()
        )

        state = self._read_state()
        report_path = Path(str(state["sandbox_dir"])) / "REPORT.md"
        report_text = report_path.read_text(encoding="utf-8")
        report_path.write_text(
            report_text.replace("IN_PROGRESS", "COMPLETE", 1),
            encoding="utf-8",
        )

        stop_late = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop_late.returncode, 0)
        stop_payload = _json_output(stop_late)
        self.assertFalse(bool(stop_payload.get("continue")))
        self.assertFalse(self._state_file().exists())

    def test_cancel_request_requires_user_decision_and_pause_window(self) -> None:
        added = _run_cli(["--add", "-R", "cancel handshake"], self.env)
        self.assertEqual(added.returncode, 0)

        cancel_request = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel_request.returncode, 0)
        self.assertIn("Cancellation request sent", cancel_request.stdout)

        state = self._read_state()
        self.assertEqual(state.get("cancel_request_state"), "pending")
        self.assertIsInstance(state.get("cancel_pause_until"), str)

        wrapper_env = self.env.copy()
        wrapper_env["OMNI_AGENT_HOOK_WRAPPER"] = "1"
        stop_in_pause = _run_cli(["--hook-stop"], wrapper_env)
        self.assertEqual(stop_in_pause.returncode, 5)
        pause_payload = _json_output(stop_in_pause)
        self.assertTrue(bool(pause_payload.get("cancel_request_pending")))
        self.assertTrue(bool(pause_payload.get("waiting_for_cancel_decision")))
        self.assertEqual(pause_payload.get("pause_then_resume_seconds"), 30)

        state = self._read_state()
        state["cancel_requested_at"] = "1999-12-31T00:00:00+00:00"
        state["cancel_pause_until"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        stop_after_pause = _run_cli(["--hook-stop"], wrapper_env)
        self.assertEqual(stop_after_pause.returncode, 2)
        post_pause_payload = _json_output(stop_after_pause)
        self.assertTrue(bool(post_pause_payload.get("cancel_request_pending")))
        self.assertTrue(bool(post_pause_payload.get("cancel_pause_elapsed")))
        self.assertTrue(bool(post_pause_payload.get("waiting_for_cancel_decision")))

    def test_cancel_pause_window_shrinks_to_actual_remaining_time(self) -> None:
        added = _run_cli(["--add", "-R", "cancel remaining pause"], self.env)
        self.assertEqual(added.returncode, 0)

        cancel_request = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel_request.returncode, 0)

        state = self._read_state()
        now = datetime.now().astimezone()
        state["cancel_requested_at"] = (now - timedelta(seconds=25)).isoformat()
        state["cancel_pause_until"] = (now + timedelta(seconds=5)).isoformat()
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        wrapper_env = self.env.copy()
        wrapper_env["OMNI_AGENT_HOOK_WRAPPER"] = "1"
        stop_in_pause = _run_cli(["--hook-stop"], wrapper_env)
        self.assertEqual(stop_in_pause.returncode, 5)
        pause_payload = _json_output(stop_in_pause)
        pause_seconds = int(pause_payload.get("pause_then_resume_seconds") or 0)
        self.assertGreaterEqual(pause_seconds, 1)
        self.assertLess(pause_seconds, 30)

    def test_cancel_deny_blocks_until_normal_stop_conditions_met(self) -> None:
        added = _run_cli(["--add", "-R", "cancel denied"], self.env)
        self.assertEqual(added.returncode, 0)

        cancel_request = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel_request.returncode, 0)

        cancel_deny = _run_cli(
            ["--cancel-deny", "--decision-note", "user wants progress to continue"],
            self.env,
        )
        self.assertEqual(cancel_deny.returncode, 0)
        deny_payload = _json_output(cancel_deny)
        self.assertTrue(bool(deny_payload.get("cancellation_denied")))

        stop_blocked = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop_blocked.returncode, 2)
        blocked_payload = _json_output(stop_blocked)
        self.assertTrue(bool(blocked_payload.get("cancel_request_denied")))

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        status_payload = json.loads(status_json.stdout)
        denied_note = str(status_payload.get("cancel_denied_note", ""))
        self.assertTrue(denied_note.startswith("[decision:"))
        self.assertNotIn("user wants progress to continue", denied_note)

        state = self._read_state()
        report_path = Path(str(state["sandbox_dir"])) / "REPORT.md"
        report_text = report_path.read_text(encoding="utf-8")
        report_path.write_text(
            report_text.replace("IN_PROGRESS", "COMPLETE", 1),
            encoding="utf-8",
        )

        stop_allowed = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop_allowed.returncode, 0)
        self.assertFalse(self._state_file().exists())

    def test_cancel_accept_immediately_closes_session(self) -> None:
        added = _run_cli(["--add", "-R", "cancel accepted"], self.env)
        self.assertEqual(added.returncode, 0)

        cancel_request = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel_request.returncode, 0)

        cancel_accept = _run_cli(
            ["--cancel-accept", "--decision-note", "approved by user"],
            self.env,
        )
        self.assertEqual(cancel_accept.returncode, 0)
        self.assertFalse(self._state_file().exists())

        archived_dir = self.sandbox_root / "archived"
        self.assertTrue(archived_dir.exists())
        self.assertTrue(any(archived_dir.iterdir()))

    def test_await_user_window_times_out_to_autonomous_continue(self) -> None:
        added = _run_cli(["--add", "-R", "await-user"], self.env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            [
                "--await-user",
                "-Q",
                "Need goal and duration confirmation",
                "--wait-minutes",
                "1",
            ],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)
        waiting_payload = _json_output(waiting)
        self.assertTrue(bool(waiting_payload.get("waiting_for_user")))
        self.assertTrue(bool(waiting_payload.get("block")))
        self.assertEqual(waiting_payload.get("hook"), "await-user")

        state = self._read_state()
        state["await_user_started_at"] = "1999-12-31T00:00:00+00:00"
        state["await_user_deadline"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        timeout = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(timeout.returncode, 2)
        timeout_payload = _json_output(timeout)
        self.assertTrue(bool(timeout_payload.get("user_response_timed_out")))
        self.assertEqual(timeout_payload.get("template_id"), "user-timeout-continue")
        self.assertIn(
            "did not respond", str(timeout_payload.get("message", "")).lower()
        )

        state_after = self._read_state()
        self.assertNotIn("await_user_started_at", state_after)
        self.assertNotIn("await_user_deadline", state_after)
        self.assertNotIn("await_user_question", state_after)

    def test_await_user_timeout_uses_fallback_when_template_missing(self) -> None:
        template_dir = self.home_dir / "templates-missing"
        template_dir.mkdir(parents=True, exist_ok=True)

        env = self.env.copy()
        env["OMNI_AGENT_TEMPLATE_DIR"] = str(template_dir)

        added = _run_cli(["--add", "-R", "await-user-fallback"], env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Need input", "--wait-minutes", "1"],
            env,
        )
        self.assertEqual(waiting.returncode, 0)

        state = self._read_state()
        state["await_user_started_at"] = "1999-12-31T00:00:00+00:00"
        state["await_user_deadline"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        timeout = _run_cli(["--hook-stop"], env)
        self.assertEqual(timeout.returncode, 2)
        timeout_payload = _json_output(timeout)
        self.assertEqual(timeout_payload.get("template_id"), "user-timeout-continue")
        template_text = str(timeout_payload.get("template", ""))
        self.assertIn("OAA USER RESPONSE TIMEOUT", template_text)
        self.assertIn("Proceed with autonomous defaults", template_text)

    def test_stop_hook_uses_fallback_when_template_format_is_invalid(self) -> None:
        template_dir = self.home_dir / "templates-invalid"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "stop-blocked.md").write_text(
            "[OAA STOP BLOCK]\nBroken template {\n", encoding="utf-8"
        )

        env = self.env.copy()
        env["OMNI_AGENT_TEMPLATE_DIR"] = str(template_dir)

        added = _run_cli(["--add", "-R", "invalid-template-fallback"], env)
        self.assertEqual(added.returncode, 0)

        stop = _run_cli(["--hook-stop"], env)
        self.assertEqual(stop.returncode, 2)
        stop_payload = _json_output(stop)
        self.assertEqual(stop_payload.get("template_id"), "stop-blocked")

        template_text = str(stop_payload.get("template", ""))
        self.assertIn("Do not stop", template_text)
        self.assertIn("Continue autonomous execution now", template_text)

    def test_await_user_default_wait_is_two_minutes(self) -> None:
        added = _run_cli(["--add", "-R", "await-user-default"], self.env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Please confirm remaining priorities"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)
        waiting_payload = _json_output(waiting)
        self.assertEqual(waiting_payload.get("wait_minutes"), 2)
        self.assertTrue(bool(waiting_payload.get("waiting_for_user")))
        self.assertTrue(
            str(waiting_payload.get("question", "")).startswith("[question:")
        )

        state = self._read_state()
        started = state.get("await_user_started_at")
        deadline = state.get("await_user_deadline")
        self.assertIsInstance(started, str)
        self.assertIsInstance(deadline, str)

    def test_wait_window_stop_payload_schedules_resume_for_openclaw(self) -> None:
        added = _run_cli(["--add", "-R", "await-user-resume-schedule"], self.env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Need constraints", "--wait-minutes", "2"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 2)
        stop_payload = _json_output(stop)
        self.assertTrue(bool(stop_payload.get("waiting_for_user")))
        pause_seconds = int(stop_payload.get("pause_then_resume_seconds") or 0)
        self.assertGreaterEqual(pause_seconds, 1)
        self.assertLessEqual(pause_seconds, 120)

    def test_user_responded_clears_wait_window(self) -> None:
        added = _run_cli(["--add", "-R", "await-user-resume"], self.env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Need constraints", "--wait-minutes", "2"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)

        responded = _run_cli(
            [
                "--user-responded",
                "--response-note",
                "User returned with new constraints",
            ],
            self.env,
        )
        self.assertEqual(responded.returncode, 0)
        responded_payload = _json_output(responded)
        self.assertTrue(bool(responded_payload.get("user_response_registered")))
        self.assertFalse(bool(responded_payload.get("waiting_for_user")))
        self.assertTrue(
            str(responded_payload.get("response_note", "")).startswith("[response:")
        )

        state_after = self._read_state()
        self.assertNotIn("await_user_started_at", state_after)
        self.assertNotIn("await_user_deadline", state_after)
        self.assertNotIn("await_user_question", state_after)

    def test_user_responded_without_wait_window_is_still_recorded(self) -> None:
        added = _run_cli(["--add", "-R", "late response"], self.env)
        self.assertEqual(added.returncode, 0)

        responded = _run_cli(
            [
                "--user-responded",
                "--response-note",
                "User came back after the timer expired",
            ],
            self.env,
        )
        self.assertEqual(responded.returncode, 0)
        responded_payload = _json_output(responded)
        self.assertTrue(bool(responded_payload.get("user_response_registered")))
        self.assertTrue(bool(responded_payload.get("late_user_response")))
        self.assertFalse(bool(responded_payload.get("waiting_for_user")))

    def test_revise_session_updates_request_deadline_and_clears_wait_window(self) -> None:
        added = _run_cli(["--add", "-R", "initial run", "-D", "10"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        sandbox_dir = str(state["sandbox_dir"])
        started_at = str(state["started_at"])
        previous_deadline = datetime.fromisoformat(str(state["deadline"]))
        state["runtime_bindings"] = {
            "openclaw": {
                "agent_id": "main",
                "session_key": "agent:main:telegram:direct:7026799796",
                "session_id": "session-123",
                "channel": "telegram",
                "to": "telegram:7026799796",
                "from": "telegram:7026799796",
                "account_id": "default",
                "updated_at": "2026-03-06T08:00:00+01:00",
            }
        }
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        waiting = _run_cli(
            ["--await-user", "-Q", "Need constraints", "--wait-minutes", "2"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)

        revised = _run_cli(
            [
                "--revise-session",
                "-R",
                "Work until 10:00 with no updates unless safety-critical",
                "-D",
                "90",
                "--response-note",
                "User is away; continue and only send the final report.",
            ],
            self.env,
        )
        self.assertEqual(revised.returncode, 0)
        self.assertIn("Session revised", revised.stdout)

        state_after = self._read_state()
        self.assertEqual(
            state_after["request"],
            "Work until 10:00 with no updates unless safety-critical",
        )
        self.assertEqual(state_after["duration_input"], "90")
        self.assertEqual(state_after["duration_mode"], "fixed")
        self.assertEqual(state_after["duration_minutes"], 90)
        self.assertEqual(state_after["sandbox_dir"], sandbox_dir)
        self.assertEqual(state_after["started_at"], started_at)
        self.assertEqual(state_after["update_policy"], "final-only")
        self.assertNotIn("await_user_started_at", state_after)
        self.assertNotIn("await_user_deadline", state_after)
        self.assertNotIn("await_user_question", state_after)

        revised_deadline = datetime.fromisoformat(str(state_after["deadline"]))
        self.assertGreater(revised_deadline, previous_deadline)

        binding = state_after["runtime_bindings"]["openclaw"]
        self.assertEqual(binding["session_id"], "session-123")
        self.assertEqual(binding["to"], "telegram:7026799796")

        env = self.env.copy()
        env["OMNI_AGENT_INCLUDE_SENSITIVE_CONTEXT"] = "1"
        status_json = _run_cli(["--status", "--json"], env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertFalse(bool(payload.get("waiting_for_user")))
        self.assertEqual(payload.get("update_policy"), "final-only")
        self.assertEqual(
            payload.get("request"),
            "Work until 10:00 with no updates unless safety-critical",
        )

    def test_revise_session_can_reactivate_expired_fixed_session(self) -> None:
        added = _run_cli(["--add", "-R", "expired-fixed", "-D", "1"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state["deadline"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        revised = _run_cli(
            [
                "--revise-session",
                "-D",
                "60",
                "--response-note",
                "User extended the session.",
            ],
            self.env,
        )
        self.assertEqual(revised.returncode, 0)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertTrue(bool(payload.get("active")))
        self.assertEqual(payload.get("duration_input"), "60")

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 2)
        stop_payload = _json_output(stop)
        self.assertTrue(bool(stop_payload.get("continue")))
        self.assertTrue(bool(stop_payload.get("block")))

    def test_revise_session_requires_actual_input(self) -> None:
        added = _run_cli(["--add", "-R", "revise-empty"], self.env)
        self.assertEqual(added.returncode, 0)

        revised = _run_cli(["--revise-session"], self.env)
        self.assertNotEqual(revised.returncode, 0)
        self.assertIn("--revise-session requires", revised.stderr)

    def test_status_shows_wait_window_and_clears_expired_window(self) -> None:
        added = _run_cli(["--add", "-R", "await-user-status"], self.env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Need constraints", "--wait-minutes", "2"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)

        status_waiting = _run_cli(["--status"], self.env)
        self.assertEqual(status_waiting.returncode, 0)
        self.assertIn("User response", status_waiting.stdout)
        self.assertIn("waiting", status_waiting.stdout)

        state = self._read_state()
        state["await_user_started_at"] = "1999-12-31T00:00:00+00:00"
        state["await_user_deadline"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        status_expired = _run_cli(["--status"], self.env)
        self.assertEqual(status_expired.returncode, 0)
        self.assertIn("window expired", status_expired.stdout)
        self.assertIn("proceeding with defaults", status_expired.stdout)

        state_after = self._read_state()
        self.assertNotIn("await_user_started_at", state_after)
        self.assertNotIn("await_user_deadline", state_after)
        self.assertNotIn("await_user_question", state_after)

    def test_await_user_rejects_expired_fixed_session(self) -> None:
        added = _run_cli(
            ["--add", "-R", "await-user-expired-fixed", "-D", "1"], self.env
        )
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state["deadline"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        waiting = _run_cli(["--await-user", "-Q", "Need more details"], self.env)
        self.assertNotEqual(waiting.returncode, 0)
        self.assertIn("deadline already passed", waiting.stderr)

    def test_fixed_stop_allows_closure_even_with_active_wait_window(self) -> None:
        added = _run_cli(["--add", "-R", "fixed-await-precedence", "-D", "1"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state["deadline"] = "2000-01-01T00:00:00+00:00"
        state["await_user_started_at"] = "1999-12-31T00:00:00+00:00"
        state["await_user_deadline"] = "2999-01-01T00:00:00+00:00"
        state["await_user_question"] = "Still waiting for response"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 0)
        stop_payload = _json_output(stop)
        self.assertFalse(bool(stop_payload.get("continue")))
        self.assertFalse(self._state_file().exists())

    def test_status_json_marks_fixed_deadline_as_closure_pending(self) -> None:
        added = _run_cli(["--add", "-R", "fixed-expired-status", "-D", "1"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state["deadline"] = "2000-01-01T00:00:00+00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertTrue(bool(payload.get("ok")))
        self.assertFalse(bool(payload.get("active")))
        self.assertTrue(bool(payload.get("session_registered")))
        self.assertTrue(bool(payload.get("closure_pending")))
        self.assertEqual(
            payload.get("lifecycle_state"), "deadline_reached_waiting_closure"
        )
        self.assertEqual(payload.get("report_status"), "IN_PROGRESS")
        self.assertEqual(payload.get("report_status_effective"), "PARTIAL")

    def test_timezone_naive_state_is_rejected_without_crash(self) -> None:
        added = _run_cli(["--add", "-R", "timezone-naive", "-D", "5"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state["started_at"] = "2026-01-01T00:00:00"
        state["deadline"] = "2026-01-01T01:00:00"
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        status = _run_cli(["--status"], self.env)
        self.assertEqual(status.returncode, 0)
        self.assertIn("State error", status.stdout)
        self.assertIn("state file is invalid", status.stdout)

    def test_require_active_guard(self) -> None:
        before = _run_cli(["--require-active"], self.env)
        self.assertNotEqual(before.returncode, 0)

        added = _run_cli(["--add", "-R", "guard", "-D", "1"], self.env)
        self.assertEqual(added.returncode, 0)

        after = _run_cli(["--require-active"], self.env)
        self.assertEqual(after.returncode, 0)

    def test_require_active_rejects_dynamic_session_waiting_for_closure(self) -> None:
        added = _run_cli(["--add", "-R", "dynamic complete", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        report_path = Path(str(state["sandbox_dir"])) / "REPORT.md"
        report_path.write_text(
            report_path.read_text(encoding="utf-8").replace(
                "IN_PROGRESS", "COMPLETE", 1
            ),
            encoding="utf-8",
        )

        require_active = _run_cli(["--require-active"], self.env)
        self.assertNotEqual(require_active.returncode, 0)
        self.assertIn("no longer requires autonomous execution", require_active.stderr)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertFalse(bool(payload.get("active")))
        self.assertFalse(bool(payload.get("should_continue")))
        self.assertTrue(bool(payload.get("stop_allowed")))
        self.assertTrue(bool(payload.get("closure_pending")))
        self.assertEqual(
            payload.get("lifecycle_state"), "completion_marked_waiting_closure"
        )

    def test_claim_execution_owner_rejects_live_conflict(self) -> None:
        added = _run_cli(["--add", "-R", "ownership conflict", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        owner_env = dict(self.env)
        owner_env["OMNI_AGENT_OWNER_TOKEN"] = "owner-a"
        claim_a = _run_cli(
            [
                "--claim-execution-owner",
                "--execution-owner-kind",
                "wrapper",
                "--execution-owner-label",
                "codex",
                "--execution-owner-pid",
                str(os.getpid()),
            ],
            owner_env,
        )
        self.assertEqual(claim_a.returncode, 0)

        competing_env = dict(self.env)
        competing_env["OMNI_AGENT_OWNER_TOKEN"] = "owner-b"
        claim_b = _run_cli(
            [
                "--claim-execution-owner",
                "--execution-owner-kind",
                "wrapper",
                "--execution-owner-label",
                "gemini",
                "--execution-owner-pid",
                str(os.getpid()),
            ],
            competing_env,
        )
        self.assertEqual(claim_b.returncode, 6)
        self.assertIn("already being executed", claim_b.stderr)
        self.assertIn("codex", claim_b.stderr)

    def test_claim_execution_owner_steals_stale_owner(self) -> None:
        added = _run_cli(["--add", "-R", "ownership steal", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        now = datetime.now().astimezone()
        state["execution_owner"] = {
            "token": "stale-owner",
            "kind": "wrapper",
            "label": "codex",
            "host_id": "foreign-host",
            "pid": 999999,
            "acquired_at": (now - timedelta(minutes=10)).isoformat(),
            "heartbeat_at": (now - timedelta(minutes=10)).isoformat(),
        }
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        owner_env = dict(self.env)
        owner_env["OMNI_AGENT_OWNER_TOKEN"] = "owner-b"
        claim = _run_cli(
            [
                "--claim-execution-owner",
                "--execution-owner-kind",
                "wrapper",
                "--execution-owner-label",
                "gemini",
                "--execution-owner-pid",
                str(os.getpid()),
            ],
            owner_env,
        )
        self.assertEqual(claim.returncode, 0)

        state_after = self._read_state()
        owner = state_after["execution_owner"]
        self.assertEqual(owner["token"], "owner-b")
        self.assertEqual(owner["label"], "gemini")
        self.assertIn("Execution ownership stolen from stale runner", self._read_log())

    def test_status_json_reports_execution_owner(self) -> None:
        added = _run_cli(["--add", "-R", "ownership status", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        owner_env = dict(self.env)
        owner_env["OMNI_AGENT_OWNER_TOKEN"] = "owner-a"
        claim = _run_cli(
            [
                "--claim-execution-owner",
                "--execution-owner-kind",
                "wrapper",
                "--execution-owner-label",
                "codex",
                "--execution-owner-pid",
                str(os.getpid()),
            ],
            owner_env,
        )
        self.assertEqual(claim.returncode, 0)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        owner = payload.get("execution_owner") or {}
        self.assertEqual(owner.get("kind"), "wrapper")
        self.assertEqual(owner.get("label"), "codex")
        self.assertEqual(owner.get("state"), "active")

    def test_corrupted_state_recovery(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._state_file().write_text("{invalid-json", encoding="utf-8")

        status = _run_cli(["--status"], self.env)
        self.assertEqual(status.returncode, 0)
        self.assertIn("State error", status.stdout)

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 2)
        stop_payload = _json_output(stop)
        self.assertTrue(bool(stop_payload.get("continue")))
        self.assertTrue(bool(stop_payload.get("block")))

        cancel = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel.returncode, 0)
        self.assertFalse(self._state_file().exists())

        quarantined = list(self.config_dir.glob("state.invalid.*.json"))
        self.assertGreaterEqual(len(quarantined), 1)

    def test_invalid_sandbox_path_is_quarantined(self) -> None:
        added = _run_cli(["--add", "-R", "tamper test", "-D", "1"], self.env)
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state["sandbox_dir"] = str(self.home_dir)
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        cancel = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel.returncode, 0)
        self.assertIn("corrupted and quarantined", cancel.stdout)
        self.assertFalse(self._state_file().exists())
        quarantined = list(self.config_dir.glob("state.invalid.*.json"))
        self.assertGreaterEqual(len(quarantined), 1)

    def test_bootstrap_creates_noninteractive_assets(self) -> None:
        for binary_name in [
            "gemini",
            "opencode",
            "openclaw",
            "codex",
            "aider",
            "goose",
            "plandex",
        ]:
            self._write_fake_binary(binary_name)

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        gemini_settings = self.home_dir / ".gemini" / "settings.json"
        opencode_plugin = (
            self.home_dir / ".config" / "opencode" / "plugins" / "omni-hook.ts"
        )
        universal_wrapper = self.home_dir / ".local" / "bin" / "omni-agent-wrap"
        codex_wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-codex"
        plandex_wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-plandex"
        openclaw_hook = (
            self.home_dir / ".openclaw" / "hooks" / "omni-recovery" / "HOOK.md"
        )
        openclaw_handler = (
            self.home_dir / ".openclaw" / "hooks" / "omni-recovery" / "handler.ts"
        )
        openclaw_plugin_manifest = (
            PROJECT_ROOT
            / ".omni-autonomous-agent"
            / "openclaw-plugin"
            / "openclaw.plugin.json"
        )
        openclaw_plugin_entry = (
            PROJECT_ROOT / ".omni-autonomous-agent" / "openclaw-plugin" / "index.ts"
        )

        self.assertTrue(gemini_settings.exists())
        self.assertTrue(opencode_plugin.exists())
        self.assertTrue(universal_wrapper.exists())
        self.assertTrue(codex_wrapper.exists())
        self.assertTrue(plandex_wrapper.exists())
        self.assertTrue(openclaw_hook.exists())
        self.assertTrue(openclaw_handler.exists())
        self.assertTrue(openclaw_plugin_manifest.exists())
        self.assertTrue(openclaw_plugin_entry.exists())

        openclaw_hook_text = openclaw_hook.read_text(encoding="utf-8")
        openclaw_handler_text = openclaw_handler.read_text(encoding="utf-8")
        openclaw_plugin_manifest_text = openclaw_plugin_manifest.read_text(
            encoding="utf-8"
        )
        openclaw_plugin_entry_text = openclaw_plugin_entry.read_text(encoding="utf-8")
        opencode_plugin_text = opencode_plugin.read_text(encoding="utf-8")
        universal_wrapper_text = universal_wrapper.read_text(encoding="utf-8")
        codex_wrapper_text = codex_wrapper.read_text(encoding="utf-8")
        self.assertIn(
            'events: ["gateway:startup", "message:received", "message:transcribed", "message:preprocessed", "session:compact:before"]',
            openclaw_hook_text,
        )
        self.assertIn("message:transcribed", openclaw_hook_text)
        self.assertIn("message:preprocessed", openclaw_hook_text)
        self.assertIn("session:compact:before", openclaw_hook_text)
        self.assertIn("OMNI_AGENT_DISABLE_OPENCLAW_AUTOWAKE", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OPENCLAW_BIN", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OAA_BIN", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OPENCLAW_AGENT_ID", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_INCLUDE_SENSITIVE_CONTEXT", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OPENCLAW_WAKE_DEDUPE_MS", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OPENCLAW_WAKE_DELIVER", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_HOOK_TELEMETRY", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OPENCLAW_SESSION_KEY", openclaw_handler_text)
        self.assertIn("OMNI_AGENT_OPENCLAW_SESSION_ID", openclaw_handler_text)
        self.assertIn(
            "OMNI_AGENT_OPENCLAW_CANCEL_ALLOWED_SENDERS", openclaw_handler_text
        )
        self.assertIn("--log-event", openclaw_handler_text)
        self.assertIn("--event", openclaw_handler_text)
        self.assertIn("--note", openclaw_handler_text)
        self.assertIn("--hook-precompact", openclaw_handler_text)
        self.assertIn("agentIdFromSessionKey", openclaw_handler_text)
        self.assertIn("readPersistedOpenclawBinding", openclaw_handler_text)
        self.assertIn("readInboundEventText", openclaw_handler_text)
        self.assertIn(
            "['received', 'transcribed', 'preprocessed'].includes(event.action)",
            openclaw_handler_text,
        )
        self.assertIn("event.type === 'session'", openclaw_handler_text)
        self.assertIn("event.action === 'compact:before'", openclaw_handler_text)
        self.assertIn("--user-responded", openclaw_handler_text)
        self.assertIn("--cancel-accept", openclaw_handler_text)
        self.assertIn("--cancel-deny", openclaw_handler_text)
        self.assertIn("CANCEL_ACCEPT_TOKENS", openclaw_handler_text)
        self.assertIn("CANCEL_DENY_TOKENS", openclaw_handler_text)
        self.assertIn("senderAuthorizedForCancelDecision", openclaw_handler_text)
        self.assertIn("cancel_decision_unauthorized", openclaw_handler_text)
        self.assertIn("readEventAccountId", openclaw_handler_text)
        self.assertIn(
            "status.cancel_request_state === 'pending'", openclaw_handler_text
        )
        self.assertIn(".npm-global", openclaw_handler_text)
        self.assertIn(
            "['agent', '--agent', targetAgentId, '--session-id', route.sessionId, '--message', prompt]",
            openclaw_handler_text,
        )
        self.assertIn("--deliver", openclaw_handler_text)
        self.assertIn("--reply-channel", openclaw_handler_text)
        self.assertIn("--reply-to", openclaw_handler_text)
        self.assertIn("--reply-account", openclaw_handler_text)
        self.assertIn("openclaw-startup-wake.json", openclaw_handler_text)
        self.assertIn("acquireDedupeLock", openclaw_handler_text)
        self.assertIn("${dedupeFile}.lock", openclaw_handler_text)
        self.assertIn(
            "startup wake skipped: unresolved session route", openclaw_handler_text
        )
        self.assertIn(
            "startup wake skipped: unable to read OAA status", openclaw_handler_text
        )
        self.assertIn(
            "startup wake skipped: duplicate restart event", openclaw_handler_text
        )
        self.assertIn("Request: [redacted]", openclaw_handler_text)
        self.assertIn("startup wake queued for agent=", openclaw_handler_text)
        self.assertIn("failed to launch agent wake runner", openclaw_handler_text)
        self.assertIn("readInboundSender", openclaw_handler_text)
        self.assertIn("eventMatchesActiveRoute", openclaw_handler_text)
        self.assertIn("route_mismatch_ignored", openclaw_handler_text)
        self.assertIn("['--status', '--json']", openclaw_handler_text)
        self.assertIn("STARTUP_WAKE_COOLDOWN_MS", openclaw_handler_text)
        self.assertIn("syncAgentLaunch", openclaw_handler_text)
        self.assertIn("sameSessionRoutes", openclaw_handler_text)
        self.assertIn("routeDeliveryScore", openclaw_handler_text)
        self.assertIn("routeSubagentPenalty", openclaw_handler_text)
        self.assertIn("sessions', '--json', '--all-agents", openclaw_handler_text)
        self.assertIn("detached: true", openclaw_handler_text)
        self.assertIn("launchDetachedOpenclawAgent", openclaw_handler_text)
        self.assertIn("process.kill(pid, 0)", openclaw_handler_text)
        self.assertIn("--clear-stale-execution-owner", openclaw_handler_text)
        self.assertIn("owner_busy_skip", openclaw_handler_text)
        self.assertIn('"id": "omni-autonomous-agent"', openclaw_plugin_manifest_text)
        self.assertIn("before_agent_start", openclaw_plugin_entry_text)
        self.assertIn("agent_end", openclaw_plugin_entry_text)
        self.assertIn("enqueueSystemEvent", openclaw_plugin_entry_text)
        self.assertIn("requestHeartbeatNow", openclaw_plugin_entry_text)
        self.assertIn("--record-openclaw-route", openclaw_plugin_entry_text)
        self.assertIn("--hook-stop", openclaw_plugin_entry_text)
        self.assertIn('runHook(["--hook-stop"])', opencode_plugin_text)
        self.assertIn('runHook(["--hook-precompact"])', opencode_plugin_text)
        self.assertIn('if [[ "$hook_status" -eq 5 ]]', universal_wrapper_text)
        self.assertIn("omni_pause_seconds()", universal_wrapper_text)
        self.assertIn(
            'pause_seconds="$(printf "%s\\n" "$hook_output" | omni_pause_seconds)"',
            universal_wrapper_text,
        )
        self.assertIn("--claim-execution-owner", universal_wrapper_text)
        self.assertIn("--heartbeat-execution-owner", universal_wrapper_text)
        self.assertIn("--release-execution-owner", universal_wrapper_text)
        self.assertNotIn("sleep 30", universal_wrapper_text)
        self.assertGreaterEqual(
            universal_wrapper_text.count(
                "if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then"
            ),
            2,
        )
        self.assertIn('if [[ "$hook_status" -eq 5 ]]', codex_wrapper_text)
        self.assertIn("omni_pause_seconds()", codex_wrapper_text)
        self.assertIn(
            'pause_seconds="$(printf "%s\\n" "$hook_output" | omni_pause_seconds)"',
            codex_wrapper_text,
        )
        self.assertIn("--claim-execution-owner", codex_wrapper_text)
        self.assertIn("--heartbeat-execution-owner", codex_wrapper_text)
        self.assertIn("--release-execution-owner", codex_wrapper_text)
        self.assertNotIn("sleep 30", codex_wrapper_text)
        self.assertGreaterEqual(
            codex_wrapper_text.count(
                "if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then"
            ),
            2,
        )
        self.assertNotIn(
            'console.error("[omni] hook-stop blocked idle:"', opencode_plugin_text
        )

        wrapper_result = subprocess.run(
            [str(codex_wrapper), "--exit-code", "7"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(wrapper_result.returncode, 0)
        self.assertIn("no active session", wrapper_result.stderr)

    def test_wrapper_blocks_until_session_can_stop(self) -> None:
        self._write_fake_binary("codex")
        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-codex"

        added = _run_cli(["--add", "-R", "wrapper block", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        with self.assertRaises(subprocess.TimeoutExpired) as timeout_ctx:
            subprocess.run(
                [str(wrapper), "--exit-code", "0"],
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )

        timed_out = timeout_ctx.exception
        partial_output = f"{timed_out.stdout or ''}\n{timed_out.stderr or ''}"
        self.assertIn("stop-blocked", partial_output)

        active_check = _run_cli(["--require-active"], self.env)
        if active_check.returncode != 0:
            readded = _run_cli(
                ["--add", "-R", "wrapper block retry", "-D", "dynamic"], self.env
            )
            self.assertEqual(readded.returncode, 0)

        state = self._read_state()
        report_path = Path(str(state["sandbox_dir"])) / "REPORT.md"
        report_text = report_path.read_text(encoding="utf-8")
        report_path.write_text(
            report_text.replace("IN_PROGRESS", "COMPLETE", 1),
            encoding="utf-8",
        )

        wrapper_result = subprocess.run(
            [str(wrapper), "--exit-code", "7"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(wrapper_result.returncode, 0)
        self.assertFalse(self._state_file().exists())

    def test_wrapper_uses_dynamic_pause_then_resume_seconds(self) -> None:
        self._write_fake_binary("codex")
        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-codex"
        shim_bin = Path(self._temp_dir.name) / "wrapper-shims"
        shim_bin.mkdir(parents=True, exist_ok=True)
        pause_state = shim_bin / "pause-state.txt"

        fake_oaa = shim_bin / "omni-autonomous-agent"
        fake_oaa.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'cmd="${1:-}"\n'
            'if [[ "${cmd}" == "--require-active" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "${cmd}" == "--hook-stop" ]]; then\n'
            '  count="0"\n'
            '  if [[ -f "${TEST_HOOK_STATE_FILE}" ]]; then\n'
            '    count="$(cat "${TEST_HOOK_STATE_FILE}")"\n'
            "  fi\n"
            '  if [[ "${count}" == "0" ]]; then\n'
            '    printf "1" > "${TEST_HOOK_STATE_FILE}"\n'
            '    printf \'{"continue": true, "block": true, "pause_then_resume_seconds": 1}\\n\'\n'
            "    exit 5\n"
            "  fi\n"
            '  printf \'{"continue": false, "block": false}\\n\'\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_oaa.chmod(0o755)

        env = self.env.copy()
        env["PATH"] = f"{shim_bin}:{self.env['PATH']}"
        env["TEST_HOOK_STATE_FILE"] = str(pause_state)

        started = time.monotonic()
        wrapper_result = subprocess.run(
            [str(wrapper), "--exit-code", "7"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(wrapper_result.returncode, 7)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertLess(elapsed, 3.5)

    def test_wrapper_pauses_when_waiting_for_user_response(self) -> None:
        self._write_fake_binary("codex")
        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-codex"

        added = _run_cli(["--add", "-R", "await pause", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Need constraints", "--wait-minutes", "2"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)

        wrapper_result = subprocess.run(
            [str(wrapper), "--exit-code", "0"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(wrapper_result.returncode, 4)
        self.assertIn(
            "waiting_for_user", f"{wrapper_result.stdout}\n{wrapper_result.stderr}"
        )
        self.assertTrue(self._state_file().exists())

    def test_wrapper_pauses_instead_of_looping_when_state_corrupts_mid_run(
        self,
    ) -> None:
        codex = self.bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "--corrupt-state" ]]; then\n'
            '  printf "{bad-json" > "${OMNI_AGENT_CONFIG_DIR}/state.json"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-codex"

        added = _run_cli(
            ["--add", "-R", "corrupt during wrapper", "-D", "dynamic"], self.env
        )
        self.assertEqual(added.returncode, 0)

        wrapper_result = subprocess.run(
            [str(wrapper), "--corrupt-state"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(wrapper_result.returncode, 4)
        self.assertIn(
            "state_corrupted", f"{wrapper_result.stdout}\n{wrapper_result.stderr}"
        )

    def test_universal_wrapper_pauses_when_waiting_for_user_response(self) -> None:
        self._write_fake_binary("codex")
        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-agent-wrap"

        added = _run_cli(
            ["--add", "-R", "await pause universal", "-D", "dynamic"], self.env
        )
        self.assertEqual(added.returncode, 0)

        waiting = _run_cli(
            ["--await-user", "-Q", "Need constraints", "--wait-minutes", "2"],
            self.env,
        )
        self.assertEqual(waiting.returncode, 0)

        wrapper_result = subprocess.run(
            [str(wrapper), "codex", "--exit-code", "0"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(wrapper_result.returncode, 4)
        self.assertIn(
            "waiting_for_user", f"{wrapper_result.stdout}\n{wrapper_result.stderr}"
        )

    def test_universal_wrapper_pauses_when_state_corrupts_mid_run(self) -> None:
        codex = self.bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "--corrupt-state" ]]; then\n'
            '  printf "{bad-json" > "${OMNI_AGENT_CONFIG_DIR}/state.json"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-agent-wrap"
        added = _run_cli(
            ["--add", "-R", "corrupt universal", "-D", "dynamic"], self.env
        )
        self.assertEqual(added.returncode, 0)

        wrapper_result = subprocess.run(
            [str(wrapper), "codex", "--corrupt-state"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(wrapper_result.returncode, 4)
        self.assertIn(
            "state_corrupted", f"{wrapper_result.stdout}\n{wrapper_result.stderr}"
        )

    def test_bootstrap_supports_future_agent_from_env(self) -> None:
        self.env["AGENT"] = "futureagent"

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-futureagent"
        self.assertTrue(wrapper.exists())

    def test_bootstrap_supports_forced_wrapper_targets(self) -> None:
        self._write_fake_binary("gemini")
        self.env["OMNI_AGENT_EXTRA_WRAPPERS"] = "soonagent"

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-soonagent"
        gemini_settings = self.home_dir / ".gemini" / "settings.json"
        self.assertTrue(wrapper.exists())
        self.assertTrue(gemini_settings.exists())

    def test_bootstrap_resolves_openclaw_from_home_local_bin(self) -> None:
        local_bin = self.home_dir / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        openclaw_bin = local_bin / "openclaw"
        openclaw_bin.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "exit 0\n",
            encoding="utf-8",
        )
        openclaw_bin.chmod(0o755)

        env = self.env.copy()
        env["PATH"] = os.pathsep.join(
            [
                str(self.bin_dir),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )

        bootstrap = _run_cli(["--bootstrap"], env)
        self.assertEqual(bootstrap.returncode, 0)
        self.assertTrue(
            (
                self.home_dir / ".openclaw" / "hooks" / "omni-recovery" / "HOOK.md"
            ).exists()
        )

    def test_bootstrap_respects_wrapper_bin_override(self) -> None:
        self._write_fake_binary("codex")
        custom_bin = Path(self._temp_dir.name) / "custom-wrapper-bin"

        env = self.env.copy()
        env["OMNI_AGENT_WRAPPER_BIN"] = str(custom_bin)

        bootstrap = _run_cli(["--bootstrap"], env)
        self.assertEqual(bootstrap.returncode, 0)

        suffix = ".cmd" if os.name == "nt" else ""
        self.assertTrue((custom_bin / f"omni-agent-wrap{suffix}").exists())
        self.assertTrue((custom_bin / f"omni-wrap-codex{suffix}").exists())

    def test_bootstrap_skips_unsafe_wrapper_token(self) -> None:
        self.env["OMNI_AGENT_EXTRA_WRAPPERS"] = "bad;token"

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)
        self.assertIn("Skipped unsafe wrapper command token", bootstrap.stdout)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-bad-token"
        self.assertFalse(wrapper.exists())

    def test_bootstrap_fails_when_openclaw_enable_fails(self) -> None:
        self._write_openclaw_failing_binary()

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 2)
        self.assertIn("OpenClaw hooks failed", bootstrap.stdout)

    def test_bootstrap_warns_when_session_memory_enable_fails(self) -> None:
        self._write_openclaw_partial_binary()

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)
        self.assertIn("OpenClaw optional hook", bootstrap.stdout)

    def test_bootstrap_warns_when_invalid_provider_config_is_quarantined(self) -> None:
        self._write_fake_binary("gemini")
        gemini_dir = self.home_dir / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        (gemini_dir / "settings.json").write_text("{bad-json", encoding="utf-8")

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)
        self.assertIn("Gemini hooks: settings.json was invalid JSON", bootstrap.stdout)
        self.assertFalse((gemini_dir / "settings.json.bak").exists())
        self.assertTrue(any(gemini_dir.glob("settings.json.invalid.*")))

    def test_bootstrap_rewrites_provider_config_without_leaking_bak_copy(self) -> None:
        self._write_fake_binary("gemini")
        gemini_dir = self.home_dir / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        (gemini_dir / "settings.json").write_text(
            json.dumps({"hooks": {"AfterAgent": []}}, indent=2),
            encoding="utf-8",
        )

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)
        self.assertFalse((gemini_dir / "settings.json.bak").exists())

    def test_installer_fails_when_bootstrap_fails(self) -> None:
        self._write_openclaw_failing_binary()

        install_env = self.env.copy()
        install_env["OMNI_AGENT_LOCAL_BIN"] = str(
            Path(self._temp_dir.name) / "install-bin"
        )
        install_env["OMNI_AGENT_INSTALL_DIR"] = str(
            Path(self._temp_dir.name) / "install-root"
        )

        install_script = PROJECT_ROOT / ".omni-autonomous-agent" / "install.sh"
        result = subprocess.run(
            ["bash", str(install_script)],
            cwd=PROJECT_ROOT,
            env=install_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap did not complete successfully", result.stderr)

    def test_installer_uses_creatable_system_bin_override(self) -> None:
        local_bin = Path(self._temp_dir.name) / "readonly-local-bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        local_bin.chmod(0o555)
        system_bin = Path(self._temp_dir.name) / "custom-system-bin"

        install_env = self.env.copy()
        install_env["OMNI_AGENT_LOCAL_BIN"] = str(local_bin)
        install_env["OMNI_AGENT_SYSTEM_BIN"] = str(system_bin)
        install_env["OMNI_AGENT_INSTALL_DIR"] = str(
            Path(self._temp_dir.name) / "install-root"
        )

        install_script = PROJECT_ROOT / ".omni-autonomous-agent" / "install.sh"
        result = subprocess.run(
            ["bash", str(install_script)],
            cwd=PROJECT_ROOT,
            env=install_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue((system_bin / "omni-autonomous-agent").exists())

    def test_windows_installer_script_exists_and_bootstraps(self) -> None:
        install_ps1 = PROJECT_ROOT / ".omni-autonomous-agent" / "install.ps1"
        self.assertTrue(install_ps1.exists())

        text = install_ps1.read_text(encoding="utf-8")
        self.assertIn("$destName.cmd", text)
        self.assertIn("--bootstrap", text)
        self.assertIn('set "PS_EXE=pwsh"', text)
        self.assertIn("OMNI_AGENT_INSTALL_DIR", text)
        self.assertIn("clone $repoUrl $installDir", text)
        self.assertIn("UTF8Encoding($false)", text)
        self.assertIn("WriteAllText", text)
        self.assertIn("OMNI_AGENT_BOOTSTRAP_TIMEOUT", text)
        self.assertIn("Wait-Process", text)
        self.assertIn("Get-PowerShellHostCommand", text)
        self.assertIn("$env:ComSpec", text)
        self.assertIn("$runnerPs1", text)
        self.assertIn("Get-PythonVersion", text)
        self.assertIn("requires >= 3.10", text)
        self.assertIn("Assert-CleanGitCheckout", text)
        self.assertIn("Invoke-GitPullNoPrompt", text)

    def test_installer_script_contains_bootstrap_timeout_guard(self) -> None:
        install_sh = PROJECT_ROOT / ".omni-autonomous-agent" / "install.sh"
        self.assertTrue(install_sh.exists())

        text = install_sh.read_text(encoding="utf-8")
        self.assertIn("OMNI_AGENT_BOOTSTRAP_TIMEOUT", text)
        self.assertIn("run_bootstrap_with_timeout", text)
        self.assertIn("python3 >= 3.10", text)
        self.assertIn("ensure_clean_git_checkout", text)
        self.assertIn("GIT_TERMINAL_PROMPT=0", text)

    def test_shell_installer_refuses_dirty_existing_clone_before_pull(self) -> None:
        bundle_root = Path(self._temp_dir.name) / "install-bundle"
        script_dir = bundle_root / ".omni-autonomous-agent"
        script_dir.mkdir(parents=True, exist_ok=True)
        install_copy = script_dir / "install.sh"
        install_copy.write_text(
            (PROJECT_ROOT / ".omni-autonomous-agent" / "install.sh").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        fake_bin = Path(self._temp_dir.name) / "install-fake-bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "-C" ]]; then\n'
            '  shift 2\n'
            "fi\n"
            'if [[ "${1:-}" == "status" && "${2:-}" == "--porcelain" ]]; then\n'
            '  printf " M dirty-file\\n"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "${1:-}" == "rev-parse" && "${2:-}" == "--abbrev-ref" ]]; then\n'
            '  printf "main\\n"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        install_dir = Path(self._temp_dir.name) / "existing-install"
        (install_dir / ".git").mkdir(parents=True, exist_ok=True)

        env = self.env.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["OMNI_AGENT_INSTALL_DIR"] = str(install_dir)

        result = subprocess.run(
            ["bash", str(install_copy)],
            cwd=bundle_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has local changes", result.stderr)

    def test_json_flag_requires_status(self) -> None:
        invalid = _run_cli(["--cancel", "--json"], self.env)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("--json is only supported with --status", invalid.stderr)

    def test_update_fails_cleanly_without_git(self) -> None:
        env = self.env.copy()
        env["PATH"] = str(self.bin_dir)

        result = _run_cli(["--update"], env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git is required for --update", result.stderr)

    def test_update_reports_non_git_repo_cleanly(self) -> None:
        fake_git = self.bin_dir / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "rev-parse" ]]; then\n'
            "  exit 1\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        env = self.env.copy()
        env["PATH"] = str(self.bin_dir)

        result = _run_cli(["--update"], env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not a git repository", result.stderr)

    def test_auto_update_skips_non_default_branch(self) -> None:
        updater = _load_internal_modules("updater")["updater"]
        saved_state: dict[str, str] = {}

        def fake_git(_repo_root: Path, *args: str) -> str:
            if args == ("status", "--porcelain"):
                return ""
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "feature/oaa-hardening"
            raise AssertionError(f"unexpected git args: {args}")

        with mock.patch.object(updater, "_should_skip_auto_update", return_value=False):
            with mock.patch.object(updater.shutil, "which", return_value="/usr/bin/git"):
                with mock.patch.object(updater, "_is_git_worktree", return_value=True):
                    with mock.patch.object(
                        updater, "_load_auto_update_state", return_value={}
                    ):
                        with mock.patch.object(
                            updater, "_save_auto_update_state", side_effect=saved_state.update
                        ):
                            with mock.patch.object(updater, "_repo_root", return_value=PROJECT_ROOT):
                                with mock.patch.object(updater, "_git", side_effect=fake_git):
                                    with mock.patch.object(updater.subprocess, "run") as run_mock:
                                        updater.maybe_auto_update()

        self.assertEqual(saved_state.get("last_result"), "skipped")
        self.assertIn("feature/oaa-hardening", str(saved_state.get("last_output", "")))
        run_mock.assert_not_called()

    def test_update_times_out_cleanly(self) -> None:
        fake_git = self.bin_dir / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "rev-parse" && "${2:-}" == "--is-inside-work-tree" ]]; then\n'
            '  printf "true\\n"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "${1:-}" == "status" ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "${1:-}" == "rev-parse" && "${2:-}" == "--abbrev-ref" ]]; then\n'
            '  printf "main\\n"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "${1:-}" == "pull" ]]; then\n'
            "  sleep 2\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        env = self.env.copy()
        env["OMNI_AGENT_COMMAND_TIMEOUT"] = "1"

        result = _run_cli(["--update"], env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git pull timed out", result.stderr)

    def test_status_does_not_trigger_auto_update(self) -> None:
        modules = _load_internal_modules("session_manager", "updater", "cli")
        cli = modules["cli"]

        with mock.patch.object(cli, "maybe_auto_update") as auto_update_mock:
            with mock.patch.object(cli, "cmd_status") as status_mock:
                with mock.patch.object(sys, "argv", ["omni-autonomous-agent", "--status"]):
                    cli.main()

        auto_update_mock.assert_not_called()
        status_mock.assert_called_once_with(json_output=False)

    def test_add_triggers_auto_update_before_registering_session(self) -> None:
        modules = _load_internal_modules("session_manager", "updater", "cli")
        cli = modules["cli"]
        call_order: list[str] = []

        with mock.patch.object(
            cli, "maybe_auto_update", side_effect=lambda: call_order.append("update")
        ):
            with mock.patch.object(
                cli,
                "cmd_add",
                side_effect=lambda request, duration: call_order.append(
                    f"add:{request}:{duration}"
                ),
            ):
                with mock.patch.object(
                    cli,
                    "cmd_status",
                    side_effect=lambda json_output=False: call_order.append(
                        f"status:{json_output}"
                    ),
                ):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "omni-autonomous-agent",
                            "--add",
                            "-R",
                            "auto update trigger",
                            "-D",
                            "dynamic",
                        ],
                    ):
                        cli.main()

        self.assertEqual(
            call_order,
            ["update", "add:auto update trigger:dynamic", "status:False"],
        )

    def test_cli_help_reflects_dynamic_duration_default(self) -> None:
        help_out = _run_cli(["--help"], self.env)
        self.assertEqual(help_out.returncode, 0)
        self.assertIn("defaults to dynamic", help_out.stdout)
        self.assertIn("with --add", help_out.stdout)

    def test_log_event_appends_log_and_report_checkpoint(self) -> None:
        added = _run_cli(["--add", "-R", "telemetry check", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        logged = _run_cli(
            [
                "--log-event",
                "--event",
                "OpenClaw Startup Wake Queued",
                "--note",
                "route=abc session=def",
            ],
            self.env,
        )
        self.assertEqual(logged.returncode, 0)

        state = self._read_state()
        sandbox_dir = Path(str(state["sandbox_dir"]))
        log_text = (sandbox_dir / "LOG.md").read_text(encoding="utf-8")
        report_text = (sandbox_dir / "REPORT.md").read_text(encoding="utf-8")

        self.assertIn("Hook telemetry", log_text)
        self.assertIn("Event: openclaw-startup-wake-queued", log_text)
        self.assertIn("Note: route=abc session=def", log_text)
        self.assertIn("Checkpoint (hook:openclaw-startup-wake-queued)", report_text)

    def test_openclaw_startup_wake_uses_origin_route_fallback(self) -> None:
        _, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
        )

        self.assertIn("agent --agent main --session-id session-123", openclaw_log)
        self.assertIn("--deliver", openclaw_log)
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)
        self.assertIn("--reply-account default", openclaw_log)
        self.assertIn("Gateway restarted and an autonomous session is still active.", openclaw_log)

    def test_openclaw_startup_wake_uses_persisted_binding_without_session_store(self) -> None:
        _, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake from binding",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "telegram:slash:7026799796",
                        "session_id": "persisted-session-456",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "from": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={"type": "gateway", "action": "startup"},
            write_session_file=False,
        )

        self.assertIn(
            "agent --agent main --session-id persisted-session-456", openclaw_log
        )
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)
        self.assertIn("--reply-account default", openclaw_log)

    def test_openclaw_startup_wake_prefers_freshest_route_for_same_session_id(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake route rebinding",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:telegram:direct:7026799796",
                        "session_id": "session-123",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "from": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={"type": "gateway", "action": "startup"},
            session_store={
                "agent:main:telegram:direct:7026799796": {
                    "sessionId": "session-123",
                    "updatedAt": 100,
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                },
                "agent:main:main": {
                    "sessionId": "session-123",
                    "updatedAt": 200,
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                },
            },
        )

        self.assertIn("agent --agent main --session-id session-123", openclaw_log)
        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id session-123 --openclaw-session-key agent:main:main",
            oaa_log,
        )

    def test_openclaw_startup_wake_dedupes_duplicate_restart_events(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake duplicate",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            extra_events=[{"type": "gateway", "action": "startup"}],
        )

        self.assertEqual(
            openclaw_log.count("agent --agent main --session-id session-123"),
            1,
        )
        self.assertIn("openclaw.startup.duplicate_skip", oaa_log)

    def test_openclaw_startup_wake_skips_when_require_active_rejects(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake require-active",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            require_active_code=1,
        )

        self.assertEqual(openclaw_log.strip(), "")
        self.assertIn("openclaw.startup.require_active_failed", oaa_log)

    def test_openclaw_startup_wake_skips_when_route_is_unresolved(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake missing route",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            write_session_file=False,
        )

        self.assertEqual(openclaw_log.strip(), "")
        self.assertIn("openclaw.startup.route_unresolved", oaa_log)

    def test_openclaw_startup_wake_recovers_route_from_sessions_cli_when_file_missing(
        self,
    ) -> None:
        cli_sessions_payload = json.dumps(
            {
                "sessions": [
                    {
                        "key": "agent:main:main",
                        "sessionId": "cli-session-789",
                        "updatedAt": 250,
                        "agentId": "main",
                    }
                ]
            }
        )
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake sessions cli",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            write_session_file=False,
            fake_openclaw_script=(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "${1:-}" == "sessions" ]]; then\n'
                f"  printf '%s\\n' '{cli_sessions_payload}'\n"
                "  exit 0\n"
                "fi\n"
                'printf "%s\\n" "$*" >> "${TEST_HOOK_OPENCLAW_LOG}"\n'
                "exit 0\n"
            ),
        )

        self.assertIn("agent --agent main --session-id cli-session-789", openclaw_log)
        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id cli-session-789 --openclaw-session-key agent:main:main",
            oaa_log,
        )

    def test_openclaw_startup_wake_ignores_sessions_cli_stderr_noise(self) -> None:
        cli_sessions_payload = json.dumps(
            {
                "sessions": [
                    {
                        "key": "agent:main:main",
                        "sessionId": "cli-session-789",
                        "updatedAt": 250,
                        "agentId": "main",
                    }
                ]
            }
        )
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake stderr warning",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            write_session_file=False,
            fake_openclaw_script=(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "${1:-}" == "sessions" ]]; then\n'
                f"  printf '%s\\n' '{cli_sessions_payload}'\n"
                '  printf "warning: benign stderr noise\\n" >&2\n'
                "  exit 0\n"
                "fi\n"
                'printf "%s\\n" "$*" >> "${TEST_HOOK_OPENCLAW_LOG}"\n'
                "exit 0\n"
            ),
        )

        self.assertIn("agent --agent main --session-id cli-session-789", openclaw_log)
        self.assertIn("openclaw.startup.wake_queued", oaa_log)

    def test_openclaw_startup_wake_recovers_delivery_route_from_cli_store_when_default_file_missing(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake cli store recovery",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            write_session_file=False,
            cli_sessions_override=[
                {
                    "key": "agent:main:main",
                    "sessionId": "cli-session-789",
                    "updatedAt": 300,
                    "agentId": "main",
                },
                {
                    "key": "agent:main:telegram:direct:7026799796",
                    "sessionId": "cli-session-789",
                    "updatedAt": 200,
                    "agentId": "main",
                },
            ],
            cli_store_payloads=[
                {
                    "relative_path": "discovered/openclaw-sessions.json",
                    "agentId": "main",
                    "sessions": {
                        "agent:main:main": {
                            "sessionId": "cli-session-789",
                            "updatedAt": 300,
                            "origin": {
                                "surface": "telegram",
                                "from": "telegram:7026799796",
                                "to": "telegram:7026799796",
                                "accountId": "default",
                            },
                        },
                        "agent:main:telegram:direct:7026799796": {
                            "sessionId": "cli-session-789",
                            "updatedAt": 200,
                            "deliveryContext": {
                                "channel": "telegram",
                                "to": "telegram:7026799796",
                                "accountId": "default",
                            },
                            "origin": {
                                "surface": "telegram",
                                "from": "telegram:7026799796",
                                "to": "telegram:7026799796",
                                "accountId": "default",
                            },
                        },
                    },
                }
            ],
        )

        self.assertIn("agent --agent main --session-id cli-session-789", openclaw_log)
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)
        self.assertIn("--reply-account default", openclaw_log)
        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id cli-session-789",
            oaa_log,
        )

    def test_openclaw_startup_wake_skips_when_dedupe_lock_is_unavailable(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake lock contention",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            precreated_lock_dirs=["openclaw-startup-wake.json.lock"],
        )

        self.assertEqual(openclaw_log.strip(), "")
        self.assertIn("openclaw.startup.dedupe_lock_unavailable", oaa_log)

    def test_openclaw_preprocessed_message_records_route_and_forwards_message(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "record inbound route",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "preprocessed",
                "sessionKey": "agent:main:main",
                "context": {
                    "bodyForAgent": "Stop now and report the current status.",
                },
            },
        )

        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id session-123 --openclaw-session-key agent:main:main",
            oaa_log,
        )
        self.assertIn("agent --agent main --session-id session-123", openclaw_log)
        self.assertIn("Handle it immediately, then continue autonomous execution", openclaw_log)
        self.assertIn(
            "User message: Stop now and report the current status.",
            openclaw_log,
        )
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)

    def test_openclaw_message_with_unverified_metadata_less_binding_fails_closed(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "fail closed route auth",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "session-123",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "content": "foreign message",
                    "from": "telegram:foreign-user",
                },
            },
            write_session_file=False,
            cli_sessions_override=[],
        )

        self.assertIn("openclaw.message.route_mismatch_ignored", oaa_log)
        self.assertNotIn("--user-responded", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_startup_skips_when_live_wrapper_owner_exists(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "owner busy",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "execution_owner": {
                    "kind": "wrapper",
                    "label": "codex",
                    "state": "active",
                    "pid": os.getpid(),
                    "acquired_at": "2026-03-06T00:00:00+00:00",
                    "heartbeat_at": "2026-03-06T00:00:00+00:00",
                },
            },
            event={"type": "gateway", "action": "startup"},
        )

        self.assertEqual(openclaw_log.strip(), "")
        self.assertIn("openclaw.startup.owner_busy_skip", oaa_log)

    def test_openclaw_multiphase_inbound_events_dedupe_to_one_forward(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "dedupe inbound",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "messageId": "message-123",
                    "content": "Continue, but prioritize restart recovery.",
                    "from": "telegram:7026799796",
                },
            },
            extra_events=[
                {
                    "type": "message",
                    "action": "transcribed",
                    "sessionKey": "agent:main:main",
                    "context": {
                        "messageId": "message-123",
                        "transcript": "Continue, but prioritize restart recovery.",
                        "from": "telegram:7026799796",
                    },
                },
                {
                    "type": "message",
                    "action": "preprocessed",
                    "sessionKey": "agent:main:main",
                    "context": {
                        "messageId": "message-123",
                        "bodyForAgent": "Continue, but prioritize restart recovery.",
                        "from": "telegram:7026799796",
                    },
                },
            ],
        )

        self.assertEqual(
            openclaw_log.count("agent --agent main --session-id session-123"),
            1,
        )
        self.assertIn("openclaw.message.forward_duplicate", oaa_log)

    def test_openclaw_received_message_registers_wait_response_and_forwards_message(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": True,
                "request": "await reply",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "content": "Stop now and report the current status.",
                },
            },
        )

        self.assertIn(
            "--user-responded --response-note Stop now and report the current status.",
            oaa_log,
        )
        self.assertIn("agent --agent main --session-id session-123", openclaw_log)
        self.assertIn(
            "User message: Stop now and report the current status.",
            openclaw_log,
        )
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)

    def test_openclaw_received_message_registers_late_user_reply_and_forwards_message(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "late reply",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "content": "I am back, use the stricter constraints now.",
                    "from": "telegram:7026799796",
                },
            },
        )

        self.assertIn(
            "--user-responded --response-note I am back, use the stricter constraints now.",
            oaa_log,
        )
        self.assertIn("openclaw.message.user_responded", oaa_log)
        self.assertIn("agent --agent main --session-id session-123", openclaw_log)

    def test_openclaw_startup_wake_merges_delivery_metadata_from_same_session_routes(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake merged route",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            session_store={
                "agent:main:telegram:direct:7026799796": {
                    "sessionId": "session-123",
                    "updatedAt": 100,
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                },
                "agent:main:main": {
                    "sessionId": "session-123",
                    "updatedAt": 200,
                },
            },
        )

        self.assertIn("agent --agent main --session-id session-123", openclaw_log)
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)
        self.assertIn("--reply-account default", openclaw_log)
        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id session-123 --openclaw-session-key agent:main:main --openclaw-reply-channel telegram --openclaw-reply-to telegram:7026799796 --openclaw-reply-from telegram:7026799796 --openclaw-reply-account default",
            oaa_log,
        )

    def test_openclaw_startup_wake_avoids_promoting_same_session_subagent_alias(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake subagent alias",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:telegram:direct:7026799796",
                        "session_id": "session-123",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "from": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={"type": "gateway", "action": "startup"},
            session_store={
                "agent:main:telegram:direct:7026799796": {
                    "sessionId": "session-123",
                    "updatedAt": 100,
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                },
                "agent:main:main": {
                    "sessionId": "session-123",
                    "updatedAt": 200,
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                },
                "agent:main:subagent:worker-123": {
                    "sessionId": "session-123",
                    "updatedAt": 300,
                    "origin": {
                        "surface": "telegram",
                        "from": "telegram:7026799796",
                        "to": "telegram:7026799796",
                        "accountId": "default",
                    },
                },
            },
        )

        self.assertIn("agent --agent main --session-id session-123", openclaw_log)
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id session-123 --openclaw-session-key agent:main:main",
            oaa_log,
        )
        self.assertNotIn("agent:main:subagent:worker-123", oaa_log)

    def test_openclaw_cancel_accept_uses_persisted_binding_authorization(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "cancel approval",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "cancel_request_state": "pending",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:telegram:direct:7026799796",
                        "session_id": "persisted-session-456",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "from": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "...",
                    "from": "telegram:7026799796",
                    "accountId": "default",
                },
            },
            write_session_file=False,
        )

        self.assertIn(
            "--cancel-accept --decision-note ...",
            oaa_log,
        )
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_cancel_accept_ignores_service_sender(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "cancel approval bot sender",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "cancel_request_state": "pending",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "from": "telegram-bot",
                        "account_id": "default",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "...",
                    "from": "telegram-bot",
                    "accountId": "default",
                },
            },
            write_session_file=False,
        )

        self.assertNotIn("--cancel-accept", oaa_log)
        self.assertIn("openclaw.message.non_user_ignored", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_cancel_accept_fails_closed_without_route_authorization_metadata(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "cancel approval fail closed",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "cancel_request_state": "pending",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "...",
                    "from": "random-user",
                },
            },
            write_session_file=False,
        )

        self.assertNotIn("--cancel-accept", oaa_log)
        self.assertIn("openclaw.message.cancel_decision_unauthorized", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_cancel_accept_rejects_account_only_route_metadata(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "cancel approval account only",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "cancel_request_state": "pending",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                        "account_id": "default",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "...",
                    "from": "telegram:7026799796",
                    "accountId": "default",
                },
            },
            write_session_file=False,
        )

        self.assertNotIn("--cancel-accept", oaa_log)
        self.assertIn("openclaw.message.cancel_decision_unauthorized", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_message_recovers_delivery_metadata_from_event_context(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "recover delivery metadata",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "content": "Resume and prioritize message recovery.",
                    "from": "telegram:7026799796",
                    "to": "telegram-bot",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            write_session_file=False,
        )

        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id persisted-session-456 --openclaw-session-key agent:main:main --openclaw-reply-channel telegram --openclaw-reply-to telegram:7026799796 --openclaw-reply-from telegram-bot --openclaw-reply-account default",
            oaa_log,
        )
        self.assertIn("agent --agent main --session-id persisted-session-456", openclaw_log)
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)
        self.assertIn("--reply-account default", openclaw_log)

    def test_openclaw_message_forward_failure_does_not_emit_forward_queued(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "forward failure telemetry",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "content": "Forward this message.",
                    "from": "telegram:7026799796",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            sync_launch=False,
            fake_openclaw_script=(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "exit 1\n"
            ),
        )

        self.assertEqual(openclaw_log.strip(), "")
        self.assertIn("openclaw.message.forward_spawn_failed", oaa_log)
        self.assertNotIn("openclaw.message.forward_queued", oaa_log)

    def test_openclaw_message_without_session_key_requires_bound_sender_match(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "ignore unrelated chat",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "Unrelated chat should not hijack the active route.",
                    "from": "telegram:someone-else",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            write_session_file=False,
        )

        self.assertNotIn("--record-openclaw-route", oaa_log)
        self.assertNotIn("--user-responded", oaa_log)
        self.assertIn("openclaw.message.route_mismatch_ignored", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_message_without_session_key_uses_matching_bound_sender(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "match bound sender",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "I am back, continue with the new constraint set.",
                    "from": "telegram:7026799796",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            write_session_file=False,
        )

        self.assertIn(
            "--record-openclaw-route --openclaw-agent-id main --openclaw-session-id persisted-session-456 --openclaw-session-key agent:main:main --openclaw-reply-channel telegram --openclaw-reply-to telegram:7026799796 --openclaw-reply-account default",
            oaa_log,
        )
        self.assertIn("--user-responded --response-note I am back, continue with the new constraint set.", oaa_log)
        self.assertIn("agent --agent main --session-id persisted-session-456", openclaw_log)
        self.assertIn("--reply-channel telegram", openclaw_log)
        self.assertIn("--reply-to telegram:7026799796", openclaw_log)

    def test_openclaw_message_without_session_key_matches_asymmetric_origin_route(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "match asymmetric route",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "Continue with the updated plan.",
                    "from": "telegram:test-user",
                    "to": "telegram:test-bot",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            session_record={
                "sessionId": "session-123",
                "origin": {
                    "surface": "telegram",
                    "from": "telegram:test-user",
                    "to": "telegram:test-bot",
                    "accountId": "default",
                },
            },
        )

        self.assertIn("--user-responded --response-note Continue with the updated plan.", oaa_log)
        self.assertIn("openclaw.message.forward_queued", oaa_log)
        self.assertIn("--reply-to telegram:test-user", openclaw_log)
        self.assertNotIn("route_mismatch_ignored", oaa_log)

    def test_openclaw_cancel_accept_allows_asymmetric_origin_sender(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "cancel approval asymmetric route",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "cancel_request_state": "pending",
            },
            event={
                "type": "message",
                "action": "received",
                "context": {
                    "content": "...",
                    "from": "telegram:test-user",
                    "to": "telegram:test-bot",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            session_record={
                "sessionId": "session-123",
                "origin": {
                    "surface": "telegram",
                    "from": "telegram:test-user",
                    "to": "telegram:test-bot",
                    "accountId": "default",
                },
            },
        )

        self.assertIn("--cancel-accept --decision-note ...", oaa_log)
        self.assertNotIn("cancel_decision_unauthorized", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_message_foreign_session_key_is_ignored_without_store_match(
        self,
    ) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "ignore foreign session key",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "runtime_bindings": {
                    "openclaw": {
                        "agent_id": "main",
                        "session_key": "agent:main:main",
                        "session_id": "persisted-session-456",
                        "channel": "telegram",
                        "to": "telegram:7026799796",
                        "account_id": "default",
                    }
                },
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:telegram:direct:someone-else",
                "context": {
                    "content": "Do not forward this unrelated session.",
                    "from": "telegram:someone-else",
                    "accountId": "default",
                    "channel": "telegram",
                },
            },
            write_session_file=False,
        )

        self.assertNotIn("--record-openclaw-route", oaa_log)
        self.assertNotIn("--user-responded", oaa_log)
        self.assertIn("openclaw.message.route_mismatch_ignored", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_non_user_message_event_is_ignored(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "ignore assistant traffic",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
            },
            event={
                "type": "message",
                "action": "received",
                "sessionKey": "agent:main:main",
                "context": {
                    "content": "Assistant emitted a summary.",
                    "from": "assistant",
                },
            },
        )

        self.assertIn("openclaw.message.non_user_ignored", oaa_log)
        self.assertEqual(openclaw_log.strip(), "")

    def test_openclaw_plugin_agent_end_requeues_retry_immediately(self) -> None:
        oaa_log, system_events, heartbeat_events, plugin_log = (
            self._run_openclaw_plugin_agent_end(
                hook_stop_code=2,
                hook_stop_payload={
                    "continue": True,
                    "block": True,
                    "retry_immediately": True,
                    "template_id": "stop-blocked",
                    "template": "Keep working until stop is actually allowed.",
                },
            )
        )

        self.assertIn("HOOK_STOP", oaa_log)
        self.assertIn("--record-openclaw-route", oaa_log)
        self.assertEqual(len(system_events), 1)
        self.assertEqual(
            system_events[0]["text"],
            "Keep working until stop is actually allowed.",
        )
        self.assertIn("oaa:stop-blocked", str(system_events[0]["opts"]["contextKey"]))
        self.assertEqual(len(heartbeat_events), 1)
        self.assertEqual(heartbeat_events[0]["sessionKey"], "agent:main:main")
        self.assertEqual(heartbeat_events[0]["agentId"], "main")
        self.assertEqual(heartbeat_events[0]["reason"], "oaa:stop-blocked")
        self.assertNotIn("warn:", plugin_log)

    def test_openclaw_startup_wake_reports_detached_launch_failure(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake detached failure",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            sync_launch=False,
            fake_openclaw_script=(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "exit 1\n"
            ),
        )

        self.assertEqual(openclaw_log.strip(), "")
        self.assertIn("openclaw.startup.spawn_failed", oaa_log)
        self.assertNotIn("openclaw.startup.wake_queued", oaa_log)

    def test_openclaw_startup_wake_accepts_short_lived_detached_success(self) -> None:
        oaa_log, openclaw_log = self._run_openclaw_handler(
            status_payload={
                "active": True,
                "waiting_for_user": False,
                "request": "startup wake detached success",
                "dynamic": True,
                "report_status": "IN_PROGRESS",
                "started_at": "2026-03-06T00:00:00+00:00",
            },
            event={"type": "gateway", "action": "startup"},
            sync_launch=False,
            fake_openclaw_script=(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'printf "%s\\n" "$*" >> "${TEST_HOOK_OPENCLAW_LOG}"\n'
                "exit 0\n"
            ),
        )

        self.assertIn("openclaw.startup.wake_queued", oaa_log)
        self.assertNotIn("openclaw.startup.spawn_failed", oaa_log)
        self.assertIn("sessions --json --all-agents", openclaw_log)

    def test_openclaw_plugin_agent_end_respects_wait_window_block(self) -> None:
        oaa_log, system_events, heartbeat_events, plugin_log = (
            self._run_openclaw_plugin_agent_end(
                hook_stop_code=4,
                hook_stop_payload={
                    "continue": True,
                    "block": True,
                    "retry_immediately": False,
                    "waiting_for_user": True,
                    "template_id": "stop-blocked",
                    "template": "Wait for the user window to close.",
                },
            )
        )

        self.assertIn("HOOK_STOP", oaa_log)
        self.assertIn("--record-openclaw-route", oaa_log)
        self.assertEqual(system_events, [])
        self.assertEqual(heartbeat_events, [])
        self.assertIn(
            "stop-gate blocked without immediate resume",
            plugin_log,
        )

    def test_openclaw_plugin_agent_end_schedules_pause_then_resume(self) -> None:
        _, system_events, heartbeat_events, plugin_log = (
            self._run_openclaw_plugin_agent_end(
                hook_stop_code=5,
                hook_stop_payload={
                    "continue": True,
                    "block": True,
                    "pause_then_resume_seconds": 1,
                    "template_id": "stop-blocked",
                    "template": "Pause, then resume autonomous execution.",
                },
                wait_ms=1100,
            )
        )

        self.assertEqual(len(system_events), 1)
        self.assertEqual(
            system_events[0]["text"],
            "Pause, then resume autonomous execution.",
        )
        self.assertEqual(len(heartbeat_events), 1)
        self.assertIn("scheduled resume", plugin_log)

    def test_record_openclaw_route_updates_state_and_status_json(self) -> None:
        added = _run_cli(["--add", "-R", "record route", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        recorded = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-123",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(recorded.returncode, 0)

        state = self._read_state()
        binding = state["runtime_bindings"]["openclaw"]
        self.assertEqual(binding["agent_id"], "main")
        self.assertEqual(
            binding["session_key"], "agent:main:telegram:direct:7026799796"
        )
        self.assertEqual(binding["session_id"], "session-123")
        self.assertEqual(binding["channel"], "telegram")
        self.assertEqual(binding["to"], "telegram:7026799796")
        self.assertEqual(binding["from"], "telegram:7026799796")
        self.assertEqual(binding["account_id"], "default")

        env = self.env.copy()
        env["OMNI_AGENT_INCLUDE_SENSITIVE_CONTEXT"] = "1"
        status_json = _run_cli(["--status", "--json"], env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        runtime_bindings = payload.get("runtime_bindings") or {}
        status_binding = runtime_bindings.get("openclaw") or {}
        self.assertEqual(status_binding.get("session_id"), "session-123")
        self.assertEqual(
            status_binding.get("session_key"),
            "agent:main:telegram:direct:7026799796",
        )

        log_text = self._read_log()
        self.assertIn("OpenClaw recovery route updated", log_text)
        self.assertIn("[openclaw-session-id:", log_text)

    def test_status_json_redacts_sensitive_fields_by_default(self) -> None:
        added = _run_cli(
            ["--add", "-R", "apiKey=secret-token", "-D", "dynamic"], self.env
        )
        self.assertEqual(added.returncode, 0)

        recorded = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-123",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(recorded.returncode, 0)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertNotEqual(payload.get("request"), "apiKey=secret-token")
        self.assertTrue(str(payload.get("request", "")).startswith("[request:"))
        self.assertEqual(payload.get("sandbox_dir"), Path(self._read_state()["sandbox_dir"]).name)

        runtime_bindings = payload.get("runtime_bindings") or {}
        status_binding = runtime_bindings.get("openclaw") or {}
        self.assertEqual(status_binding.get("channel"), "telegram")
        self.assertNotEqual(status_binding.get("session_id"), "session-123")
        self.assertTrue(
            str(status_binding.get("session_id", "")).startswith("[openclaw-session-id:")
        )

    def test_record_openclaw_route_merges_partial_updates_without_losing_delivery_metadata(
        self,
    ) -> None:
        added = _run_cli(["--add", "-R", "merge route", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        initial = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-123",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(initial.returncode, 0)

        partial = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:main",
                "--openclaw-session-id",
                "session-123",
            ],
            self.env,
        )
        self.assertEqual(partial.returncode, 0)

        state = self._read_state()
        binding = state["runtime_bindings"]["openclaw"]
        self.assertEqual(binding["session_key"], "agent:main:main")
        self.assertEqual(binding["session_id"], "session-123")
        self.assertEqual(binding["channel"], "telegram")
        self.assertEqual(binding["to"], "telegram:7026799796")
        self.assertEqual(binding["from"], "telegram:7026799796")
        self.assertEqual(binding["account_id"], "default")

        self.assertFalse((self.config_dir / "openclaw-route-cache.json").exists())

    def test_record_openclaw_route_replaces_metadata_when_session_id_changes(self) -> None:
        added = _run_cli(["--add", "-R", "replace route", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        initial = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-old",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(initial.returncode, 0)

        replacement = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:main",
                "--openclaw-session-id",
                "session-new",
            ],
            self.env,
        )
        self.assertEqual(replacement.returncode, 0)

        state = self._read_state()
        binding = state["runtime_bindings"]["openclaw"]
        self.assertEqual(binding["session_key"], "agent:main:main")
        self.assertEqual(binding["session_id"], "session-new")
        self.assertNotIn("channel", binding)
        self.assertNotIn("to", binding)
        self.assertNotIn("from", binding)
        self.assertNotIn("account_id", binding)

    def test_add_ignores_preseeded_openclaw_route_cache_without_active_session(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.config_dir / "openclaw-route-cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "agent_id": "main",
                    "session_key": "agent:main:telegram:direct:7026799796",
                    "session_id": "cached-session-321",
                    "channel": "telegram",
                    "to": "telegram:7026799796",
                    "from": "telegram:7026799796",
                    "account_id": "default",
                    "updated_at": datetime.now().astimezone().isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        added = _run_cli(
            ["--add", "-R", "ignore foreign route cache", "-D", "dynamic"], self.env
        )
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        runtime_bindings = state.get("runtime_bindings") or {}
        self.assertEqual(runtime_bindings, {})
        self.assertTrue(cache_file.exists())

    def test_record_openclaw_route_without_active_session_does_not_create_cache(self) -> None:
        recorded = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-outside-oaa",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(recorded.returncode, 0)
        self.assertFalse((self.config_dir / "openclaw-route-cache.json").exists())

    def test_cancel_accept_clears_openclaw_route_cache(self) -> None:
        added = _run_cli(["--add", "-R", "cancel route cache", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        recorded = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-123",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(recorded.returncode, 0)

        cancel_request = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel_request.returncode, 0)

        cancel_accept = _run_cli(["--cancel-accept"], self.env)
        self.assertEqual(cancel_accept.returncode, 0)
        self.assertFalse((self.config_dir / "openclaw-route-cache.json").exists())

    def test_normal_stop_clears_openclaw_route_cache(self) -> None:
        added = _run_cli(["--add", "-R", "stop route cache", "-D", "dynamic"], self.env)
        self.assertEqual(added.returncode, 0)

        recorded = _run_cli(
            [
                "--record-openclaw-route",
                "--openclaw-agent-id",
                "main",
                "--openclaw-session-key",
                "agent:main:telegram:direct:7026799796",
                "--openclaw-session-id",
                "session-123",
                "--openclaw-reply-channel",
                "telegram",
                "--openclaw-reply-to",
                "telegram:7026799796",
                "--openclaw-reply-from",
                "telegram:7026799796",
                "--openclaw-reply-account",
                "default",
            ],
            self.env,
        )
        self.assertEqual(recorded.returncode, 0)

        state = self._read_state()
        report_path = Path(str(state["sandbox_dir"])) / "REPORT.md"
        report_text = report_path.read_text(encoding="utf-8")
        report_path.write_text(
            report_text.replace("IN_PROGRESS", "COMPLETE", 1),
            encoding="utf-8",
        )

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 0)
        self.assertFalse((self.config_dir / "openclaw-route-cache.json").exists())

    def test_final_only_update_policy_detected_for_until_requests(self) -> None:
        added = _run_cli(
            ["--add", "-R", "Clean workspace until 09:00 local time", "-D", "15"],
            self.env,
        )
        self.assertEqual(added.returncode, 0)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        status_payload = json.loads(status_json.stdout)
        self.assertEqual(status_payload.get("update_policy"), "final-only")

        stop = _run_cli(["--hook-stop"], self.env)
        self.assertEqual(stop.returncode, 2)
        stop_payload = _json_output(stop)
        self.assertFalse(bool(stop_payload.get("user_update_allowed", True)))
        self.assertIn(
            "final-only",
            str(stop_payload.get("template", "")).lower(),
        )

    def test_legacy_state_without_update_policy_is_migrated(self) -> None:
        added = _run_cli(
            ["--add", "-R", "Keep cleaning until 09:00", "-D", "15"],
            self.env,
        )
        self.assertEqual(added.returncode, 0)

        state = self._read_state()
        state.pop("update_policy", None)
        self._state_file().write_text(json.dumps(state), encoding="utf-8")

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertEqual(payload.get("update_policy"), "final-only")

        migrated = self._read_state()
        self.assertEqual(migrated.get("update_policy"), "final-only")

    def test_install_help_is_machine_agnostic(self) -> None:
        text = (PROJECT_ROOT / "install-help.md").read_text(encoding="utf-8")
        banned = ["/home/", "~/", "%LOCALAPPDATA%", "%APPDATA%", "C:/"]
        for token in banned:
            self.assertNotIn(token, text)

    def test_install_help_keeps_core_hook_setup_checks(self) -> None:
        text = (PROJECT_ROOT / "install-help.md").read_text(encoding="utf-8")
        required_snippets = [
            "omni-autonomous-agent --bootstrap",
            "openclaw hooks check",
            "openclaw hooks info omni-recovery",
            "Wrapper contract",
            "Final-only update policy check",
            "Evidence checklist",
            "AI self-setup playbook (non-scripted fallback)",
            "Preferred repo-native verification ladder",
            "Official references and troubleshooting resources",
            "Do not fail a generic wrapper-based setup just because `openclaw` is absent",
            "message:transcribed",
            "message:preprocessed",
            "session:compact:before",
            "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest",
            "tests/launch_gate_clean.sh",
            "tests/pwsh_install_smoke.sh",
            "tests/native_agent_check.sh",
            "tests/host_agent_check.sh",
            "tests/macos_smoke.sh",
            "tests/windows_smoke.ps1",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_install_help_keeps_current_official_reference_links(self) -> None:
        text = (PROJECT_ROOT / "install-help.md").read_text(encoding="utf-8")
        required_links = [
            "https://docs.openclaw.ai/automation/hooks",
            "https://docs.openclaw.ai/automation/hooks#troubleshooting",
            "https://geminicli.com/docs/get-started/authentication/",
            "https://geminicli.com/docs/hooks/",
            "https://code.claude.com/docs/en/hooks",
            "https://opencode.ai/docs/plugins/",
            "https://developers.openai.com/api/docs/guides/tools-shell",
            "https://developers.openai.com/api/docs/mcp",
        ]
        for link in required_links:
            self.assertIn(link, text)

    def test_install_help_clarifies_delivery_disabled_openclaw_proof_scope(
        self,
    ) -> None:
        text = (PROJECT_ROOT / "install-help.md").read_text(encoding="utf-8")
        required_snippets = [
            "internal OpenClaw session evidence, not external chat delivery",
            "With `OMNI_AGENT_OPENCLAW_WAKE_DELIVER=0`, do not expect a human-chat echo.",
            "rerun the same handler invocation without `OMNI_AGENT_OPENCLAW_WAKE_DELIVER=0`",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_install_help_keeps_future_agent_and_config_recovery_guidance(self) -> None:
        text = (PROJECT_ROOT / "install-help.md").read_text(encoding="utf-8")
        required_snippets = [
            "Future-agent fallback",
            "AGENT=<agent-command> omni-autonomous-agent --bootstrap",
            "OMNI_AGENT_EXTRA_WRAPPERS",
            "does **not** replace the shell",
            "provider config file was invalid",
            "quarantined or replaced safely",
            "simulated coverage only",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_skill_keeps_autonomy_contract_and_readme_alignment(self) -> None:
        text = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
        required_snippets = [
            "Do chores until I stop you",
            "Tell them they have 2 minutes to respond",
            "Do not ask the human to perform manual setup",
            "Do not invent a rule that every command must start with `omni-autonomous-agent`",
            "Use wrappers only for the **agent process**",
            "configured",
            "callable",
            "authenticated",
            "live-verified",
            "temporary disconnect or provider restart is not task completion",
            "I will now ...",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_skill_keeps_readme_install_and_resume_contract(self) -> None:
        text = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
        required_snippets = [
            "Don't worry about setup, your agent takes care of it.",
            "Treat `install-help.md` as the canonical hook setup playbook.",
            "Please install `https://github.com/omnious0o0/omni-autonomous-agent`.",
            "please only report back when everything's fully installed and verified.",
            "Otherwise it won't stop. Even if it goes offline, it will immediately resume when it comes back online.",
            "waits for approval or denial",
            'And none of the "I will now do..." and then doing nothing. That\'s fixed too.',
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_readme_and_skill_share_core_autonomous_examples(self) -> None:
        readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        skill_text = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
        shared_snippets = [
            "Work overnight",
            "Work on this for 2 hours",
            "Keep working on this until it's done",
            "Do chores until I stop you",
            "2 minutes",
            "install-help.md",
            "install.ps1",
        ]
        for snippet in shared_snippets:
            self.assertIn(snippet, readme_text)
            self.assertIn(snippet, skill_text)

    def test_gitignore_keeps_task_and_archived_sandbox_rules(self) -> None:
        text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        required_snippets = [
            "TASK.md",
            "omni-sandbox/*",
            "!omni-sandbox/archived/",
            "omni-sandbox/archived/*",
            "!omni-sandbox/archived/.gitkeep",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_skill_keeps_command_model_and_honest_proof_rules(self) -> None:
        text = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
        required_snippets = [
            "memory system",
            "Normal operation must not rely on manual git or GitHub commands.",
            "Normal work commands remain normal commands.",
            "`oaa <command>` alias",
            "Use wrappers only for the **agent process**",
            "2 minutes",
            "--await-user",
            "--user-responded",
            "configured",
            "callable",
            "authenticated",
            "live-verified",
            "Compaction is not completion.",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_default_config_dir_windows_uses_localappdata(self) -> None:
        constants = _load_internal_modules("constants")["constants"]
        class FakeWindowsPath(PureWindowsPath):
            def expanduser(self) -> "FakeWindowsPath":
                return self

        with (
            mock.patch.dict(
                constants.os.environ,
                {"LOCALAPPDATA": "DriveRoot/AppData/Local"},
                clear=True,
            ),
            mock.patch.object(constants, "Path", FakeWindowsPath),
            mock.patch.object(constants.os, "name", "nt"),
            mock.patch.object(constants.sys, "platform", "win32"),
        ):
            self.assertEqual(
                str(constants._default_config_dir()),
                "DriveRoot\\AppData\\Local\\omni-autonomous-agent",
            )

    def test_default_config_dir_macos_uses_application_support(self) -> None:
        constants = _load_internal_modules("constants")["constants"]
        with (
            mock.patch.dict(constants.os.environ, {}, clear=True),
            mock.patch.object(constants.os, "name", "posix"),
            mock.patch.object(constants.sys, "platform", "darwin"),
            mock.patch.object(
                constants.Path, "home", return_value=Path("/tmp/macos-home")
            ),
        ):
            self.assertEqual(
                constants._default_config_dir(),
                Path("/tmp/macos-home")
                / "Library"
                / "Application Support"
                / "omni-autonomous-agent",
            )

    def test_default_config_dir_linux_uses_xdg_config_home(self) -> None:
        constants = _load_internal_modules("constants")["constants"]
        with (
            mock.patch.dict(
                constants.os.environ,
                {"XDG_CONFIG_HOME": "/tmp/xdg-config"},
                clear=True,
            ),
            mock.patch.object(constants.os, "name", "posix"),
            mock.patch.object(constants.sys, "platform", "linux"),
        ):
            self.assertEqual(
                constants._default_config_dir(),
                Path("/tmp/xdg-config") / "omni-autonomous-agent",
            )

    def test_bootstrap_wrapper_bin_dir_windows_uses_localappdata(self) -> None:
        modules = _load_internal_modules("constants", "bootstrap")
        bootstrap = modules["bootstrap"]
        class FakeWindowsPath(PureWindowsPath):
            def expanduser(self) -> "FakeWindowsPath":
                return self

        with (
            mock.patch.dict(
                bootstrap.os.environ,
                {"LOCALAPPDATA": "DriveRoot/AppData/Local"},
                clear=True,
            ),
            mock.patch.object(bootstrap, "Path", FakeWindowsPath),
            mock.patch.object(bootstrap.os, "name", "nt"),
        ):
            self.assertEqual(
                str(bootstrap._wrapper_bin_dir()),
                "DriveRoot\\AppData\\Local\\omni-autonomous-agent\\bin",
            )

    def test_bootstrap_opencode_plugin_path_prefers_opencode_config_dir(self) -> None:
        modules = _load_internal_modules("constants", "bootstrap")
        bootstrap = modules["bootstrap"]
        with mock.patch.dict(
            bootstrap.os.environ,
            {"OPENCODE_CONFIG_DIR": "/tmp/opencode-config"},
            clear=True,
        ):
            self.assertEqual(
                bootstrap._default_opencode_plugin_path(),
                Path("/tmp/opencode-config") / "plugins" / "omni-hook.ts",
            )

    def test_docker_smoke_uses_real_docker_matrix(self) -> None:
        text = (PROJECT_ROOT / "tests" / "docker_smoke.sh").read_text(
            encoding="utf-8"
        )
        required_snippets = [
            "docker run",
            "curl -fsSL",
            "OMNI_DOCKER_SMOKE_INSTALLER_HOST",
            "--network host",
            "OMNI_DOCKER_SMOKE_IMAGES",
            "ubuntu:24.04",
            "debian:12-slim",
            "alpine:3.20",
            "session:compact:before",
            "message:transcribed",
            "message:preprocessed",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_pwsh_install_smoke_script_exists(self) -> None:
        text = (PROJECT_ROOT / "tests" / "pwsh_install_smoke.sh").read_text(
            encoding="utf-8"
        )
        required_snippets = [
            "mcr.microsoft.com/powershell:latest",
            "pwsh -NoLogo -NoProfile -File /repo/.omni-autonomous-agent/install.ps1",
            "omni-autonomous-agent.ps1",
            "omni-autonomous-agent.cmd",
            "omni-wrap-futureagent",
            "--hook-stop",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_final_only_update_policy_detected_for_by_and_no_update_phrases(self) -> None:
        added = _run_cli(
            ["--add", "-R", "Finish hardening by 10:30 and no progress updates", "-D", "20"],
            self.env,
        )
        self.assertEqual(added.returncode, 0)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertEqual(payload.get("update_policy"), "final-only")

    def test_no_update_phrase_for_dynamic_session_sets_final_only_policy(self) -> None:
        added = _run_cli(
            ["--add", "-R", "Do maintenance, no updates unless done", "-D", "dynamic"],
            self.env,
        )
        self.assertEqual(added.returncode, 0)

        status_json = _run_cli(["--status", "--json"], self.env)
        self.assertEqual(status_json.returncode, 0)
        payload = json.loads(status_json.stdout)
        self.assertEqual(payload.get("update_policy"), "final-only")

    def test_main_preflight_includes_bootstrap_module(self) -> None:
        main_text = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"bootstrap.py"', main_text)


if __name__ == "__main__":
    unittest.main()
