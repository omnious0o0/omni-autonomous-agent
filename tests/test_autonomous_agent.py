from __future__ import annotations

import itertools
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
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
        self.assertEqual(active_payload.get("request"), "fixed lifecycle")

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

        openclaw_hook_text = openclaw_hook.read_text(encoding="utf-8")
        openclaw_handler_text = openclaw_handler.read_text(encoding="utf-8")
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
        self.assertIn(
            "eventSessionKey.startsWith('agent:') ? eventSessionKey : ''",
            openclaw_handler_text,
        )
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
        self.assertIn("failed to launch startup wake ping", openclaw_handler_text)
        self.assertIn("event.context?.from", openclaw_handler_text)
        self.assertIn("['--status', '--json']", openclaw_handler_text)
        self.assertIn("STARTUP_WAKE_COOLDOWN_MS", openclaw_handler_text)
        self.assertIn('runHook(["--hook-stop"])', opencode_plugin_text)
        self.assertIn('runHook(["--hook-precompact"])', opencode_plugin_text)
        self.assertIn('if [[ "$hook_status" -eq 5 ]]', universal_wrapper_text)
        self.assertIn("sleep 30", universal_wrapper_text)
        self.assertGreaterEqual(
            universal_wrapper_text.count(
                "if ! omni-autonomous-agent --require-active >/dev/null 2>&1; then"
            ),
            2,
        )
        self.assertIn('if [[ "$hook_status" -eq 5 ]]', codex_wrapper_text)
        self.assertIn("sleep 30", codex_wrapper_text)
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

    def test_bootstrap_warns_when_session_memory_enable_fails(self) -> None:
        self._write_openclaw_partial_binary()

        bootstrap = _run_cli(["--bootstrap"], self.env)
        self.assertEqual(bootstrap.returncode, 0)
        self.assertIn("OpenClaw optional hook", bootstrap.stdout)

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

    def test_installer_script_contains_bootstrap_timeout_guard(self) -> None:
        install_sh = PROJECT_ROOT / ".omni-autonomous-agent" / "install.sh"
        self.assertTrue(install_sh.exists())

        text = install_sh.read_text(encoding="utf-8")
        self.assertIn("OMNI_AGENT_BOOTSTRAP_TIMEOUT", text)
        self.assertIn("run_bootstrap_with_timeout", text)

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
