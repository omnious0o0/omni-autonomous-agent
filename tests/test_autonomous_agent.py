from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_SCRIPT = PROJECT_ROOT / "main.py"


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

    def test_fixed_session_lifecycle(self) -> None:
        status = _run_cli(["--status"], self.env)
        self.assertEqual(status.returncode, 0)
        self.assertIn("No active session", status.stdout)

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
        self.assertEqual(stop_payload.get("template_id"), "stop-blocked")
        self.assertIn("Do not stop", str(stop_payload.get("template", "")))

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

        cancel = _run_cli(["--cancel"], self.env)
        self.assertEqual(cancel.returncode, 0)
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

        state = self._read_state()
        started = state.get("await_user_started_at")
        deadline = state.get("await_user_deadline")
        self.assertIsInstance(started, str)
        self.assertIsInstance(deadline, str)

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

        state_after = self._read_state()
        self.assertNotIn("await_user_started_at", state_after)
        self.assertNotIn("await_user_deadline", state_after)
        self.assertNotIn("await_user_question", state_after)

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

        self.assertTrue(gemini_settings.exists())
        self.assertTrue(opencode_plugin.exists())
        self.assertTrue(universal_wrapper.exists())
        self.assertTrue(codex_wrapper.exists())
        self.assertTrue(plandex_wrapper.exists())
        self.assertTrue(openclaw_hook.exists())
        self.assertTrue(openclaw_handler.exists())

        wrapper_result = subprocess.run(
            [str(codex_wrapper), "--exit-code", "7"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(wrapper_result.returncode, 3)

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
        self.assertEqual(wrapper_result.returncode, 7)
        self.assertFalse(self._state_file().exists())

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
        self.env["OMNI_AGENT_EXTRA_WRAPPERS"] = "soonagent"

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)

        wrapper = self.home_dir / ".local" / "bin" / "omni-wrap-soonagent"
        self.assertTrue(wrapper.exists())

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

    def test_cli_help_reflects_dynamic_duration_default(self) -> None:
        help_out = _run_cli(["--help"], self.env)
        self.assertEqual(help_out.returncode, 0)
        self.assertIn("defaults to dynamic", help_out.stdout)
        self.assertIn("with --add", help_out.stdout)


if __name__ == "__main__":
    unittest.main()
