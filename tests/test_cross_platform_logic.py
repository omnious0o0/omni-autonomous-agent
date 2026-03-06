from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = PROJECT_ROOT / ".omni-autonomous-agent"


def _load_internal_modules(*module_names: str) -> dict[str, object]:
    pkg_name = f"omni_agent_test_{uuid.uuid4().hex}"

    pkg_spec = importlib.util.spec_from_file_location(pkg_name, PKG_DIR / "__init__.py")
    if pkg_spec is None or pkg_spec.loader is None:
        raise RuntimeError("failed to load internal package")

    pkg_module = importlib.util.module_from_spec(pkg_spec)
    pkg_module.__path__ = [str(PKG_DIR)]
    sys.modules[pkg_name] = pkg_module
    pkg_spec.loader.exec_module(pkg_module)

    loaded: dict[str, object] = {}
    for module_name in module_names:
        module_spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{module_name}", PKG_DIR / f"{module_name}.py"
        )
        if module_spec is None or module_spec.loader is None:
            raise RuntimeError(f"failed to load internal module {module_name}")

        module = importlib.util.module_from_spec(module_spec)
        module.__package__ = pkg_name
        sys.modules[f"{pkg_name}.{module_name}"] = module
        module_spec.loader.exec_module(module)
        loaded[module_name] = module

    return loaded


class CrossPlatformLogicTests(unittest.TestCase):
    def test_linux_config_dir_prefers_xdg(self) -> None:
        constants = _load_internal_modules("constants")["constants"]
        with tempfile.TemporaryDirectory() as config_home:
            with mock.patch.object(constants.os, "name", "posix"):
                with mock.patch.object(constants.sys, "platform", "linux"):
                    with mock.patch.dict(
                        constants.os.environ,
                        {"XDG_CONFIG_HOME": config_home},
                        clear=False,
                    ):
                        self.assertEqual(
                            constants._default_config_dir(),
                            Path(config_home) / "omni-autonomous-agent",
                        )

    def test_macos_config_dir_uses_application_support(self) -> None:
        constants = _load_internal_modules("constants")["constants"]
        fake_home = Path("/tmp/fake-macos-home")
        with mock.patch.object(constants.os, "name", "posix"):
            with mock.patch.object(constants.sys, "platform", "darwin"):
                with mock.patch.object(constants.Path, "home", return_value=fake_home):
                    self.assertEqual(
                        constants._default_config_dir(),
                        fake_home
                        / "Library"
                        / "Application Support"
                        / "omni-autonomous-agent",
                    )

    def test_windows_config_dir_prefers_localappdata(self) -> None:
        constants = _load_internal_modules("constants")["constants"]
        with tempfile.TemporaryDirectory() as local_app_data:
            with mock.patch.object(constants.os, "name", "nt"):
                with mock.patch.object(constants, "Path", pathlib.PosixPath):
                    with mock.patch.dict(
                        constants.os.environ,
                        {"LOCALAPPDATA": local_app_data, "APPDATA": ""},
                        clear=False,
                    ):
                        self.assertEqual(
                            str(constants._default_config_dir()),
                            str(pathlib.PosixPath(local_app_data) / "omni-autonomous-agent"),
                        )

    def test_windows_wrapper_bin_dir_prefers_localappdata(self) -> None:
        modules = _load_internal_modules("constants", "bootstrap")
        bootstrap = modules["bootstrap"]
        with tempfile.TemporaryDirectory() as local_app_data:
            with mock.patch.object(bootstrap.os, "name", "nt"):
                with mock.patch.object(bootstrap, "Path", pathlib.PosixPath):
                    with mock.patch.dict(
                        bootstrap.os.environ,
                        {"LOCALAPPDATA": local_app_data},
                        clear=False,
                    ):
                        self.assertEqual(
                            str(bootstrap._wrapper_bin_dir()),
                            str(
                                pathlib.PosixPath(local_app_data)
                                / "omni-autonomous-agent"
                                / "bin"
                            ),
                        )

    def test_windows_wrapper_filename_uses_cmd_suffix(self) -> None:
        bootstrap = _load_internal_modules("constants", "bootstrap")["bootstrap"]
        with mock.patch.object(bootstrap.os, "name", "nt"):
            self.assertEqual(bootstrap._wrapper_filename("omni-wrap-codex"), "omni-wrap-codex.cmd")

    def test_windows_wrapper_does_not_claim_child_powershell_pid(self) -> None:
        bootstrap = _load_internal_modules("constants", "bootstrap")["bootstrap"]
        with mock.patch.object(bootstrap.os, "name", "nt"):
            script = bootstrap._specific_wrapper_script("codex")
        self.assertNotIn("--execution-owner-pid", script)
        self.assertNotIn('-Command "$PID"', script)

    def test_opencode_plugin_path_respects_explicit_config_dir(self) -> None:
        bootstrap = _load_internal_modules("constants", "bootstrap")["bootstrap"]
        with tempfile.TemporaryDirectory() as config_dir:
            with mock.patch.dict(
                bootstrap.os.environ,
                {"OPENCODE_CONFIG_DIR": config_dir, "XDG_CONFIG_HOME": ""},
                clear=False,
            ):
                self.assertEqual(
                    bootstrap._default_opencode_plugin_path(),
                    Path(config_dir) / "plugins" / "omni-hook.ts",
                )

    def test_universal_wrapper_runs_arbitrary_command_without_oaa_prefix(self) -> None:
        bootstrap = _load_internal_modules("constants", "bootstrap")["bootstrap"]
        with mock.patch.object(bootstrap.os, "name", "posix"):
            script = bootstrap._universal_wrapper_script()
        self.assertIn('"$@"', script)
        self.assertNotIn('omni-autonomous-agent "$@"', script)

    def test_specific_wrapper_runs_target_agent_directly(self) -> None:
        bootstrap = _load_internal_modules("constants", "bootstrap")["bootstrap"]
        with mock.patch.object(bootstrap.os, "name", "posix"):
            script = bootstrap._specific_wrapper_script("gemini")
        self.assertIn('gemini "$@"', script)
        self.assertNotIn("omni-autonomous-agent gemini", script)
        self.assertIn(
            "trap 'omni_exit_status=$?; trap - EXIT; cleanup_owner; exit \"$omni_exit_status\"' EXIT",
            script,
        )

    def test_install_help_documents_command_model_and_proof_limits(self) -> None:
        text = (PROJECT_ROOT / "install-help.md").read_text(encoding="utf-8")
        required_snippets = [
            "does **not** require every command to be prefixed",
            "Normal work commands remain normal commands",
            "Use wrappers only for the **agent process**",
            "Verification grades",
            "configured",
            "callable",
            "authenticated",
            "live-verified",
            "must **not** claim live provider verification",
            "simulated coverage only",
            "defined-but-broken VM",
            "Do not fail a generic wrapper-based setup just because `openclaw` is absent",
            "openclaw sessions --json --all-agents",
            "handlerPath",
            "`python3`, `python`, or `py`",
            "`winget`",
            "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest",
            "tests/native_agent_check.sh",
            "tests/host_agent_check.sh",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_skill_matches_readme_operating_contract(self) -> None:
        readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        skill_text = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        for snippet in [
            "Work overnight",
            "Work on this for 2 hours",
            "Keep working on this until it's done",
            "Do chores until I stop you",
            "memory system",
            "install.ps1",
        ]:
            self.assertIn(snippet, readme_text)
            self.assertIn(snippet, skill_text)

        for snippet in [
            "does **not** replace the shell",
            "Do not invent a rule that every command must start with `omni-autonomous-agent`",
            "Use wrappers only for the **agent process**",
            "configured",
            "callable",
            "authenticated",
            "live-verified",
            "simulated coverage only",
        ]:
            self.assertIn(snippet, skill_text)

    def test_gitignore_keeps_task_and_archived_sandbox_rules(self) -> None:
        text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        for snippet in [
            "TASK.md",
            "omni-sandbox/*",
            "!omni-sandbox/archived/",
            "omni-sandbox/archived/*",
            "!omni-sandbox/archived/.gitkeep",
        ]:
            self.assertIn(snippet, text)

    def test_install_ps1_keeps_self_healing_bootstrap_logic(self) -> None:
        text = (PROJECT_ROOT / ".omni-autonomous-agent" / "install.ps1").read_text(
            encoding="utf-8"
        )
        required_snippets = [
            "Ensure-PythonCommand",
            "Ensure-GitCommand",
            "Get-PythonVersion",
            "Get-PowerShellHostCommand",
            "Refresh-Path",
            "Python.Python.3.12",
            "Git.Git",
            "OMNI_AGENT_REPO_URL",
            "requires >= 3.10",
            "Assert-CleanGitCheckout",
            "Invoke-GitPullNoPrompt",
            "$env:ComSpec",
            "$runnerPs1",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_launch_gate_clean_script_keeps_sanitized_copy_flow(self) -> None:
        text = (PROJECT_ROOT / "tests" / "launch_gate_clean.sh").read_text(
            encoding="utf-8"
        )
        required_snippets = [
            '"git",',
            '"ls-files",',
            '"--cached",',
            '"--modified",',
            '"--deduplicate",',
            "rev-parse --is-inside-work-tree",
            "touch \"${COPY_DIR}/TASK.md\"",
            "bash tests/launch_gate.sh",
            "launch-gate-clean passed",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_pwsh_install_smoke_script_exists(self) -> None:
        text = (PROJECT_ROOT / "tests" / "pwsh_install_smoke.sh").read_text(
            encoding="utf-8"
        )
        required_snippets = [
            "mcr.microsoft.com/powershell:latest",
            "install.ps1",
            "omni-autonomous-agent.ps1",
            "futureagent",
            "pwsh-install-smoke passed",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_docker_smoke_uses_tracked_fixture_and_safe_cleanup_order(self) -> None:
        text = (PROJECT_ROOT / "tests" / "docker_smoke.sh").read_text(
            encoding="utf-8"
        )
        for snippet in ['"git",', '"ls-files",', '"--cached",', '"--modified",', '"--deduplicate",']:
            self.assertIn(snippet, text)
        self.assertNotIn("rsync -a", text)
        self.assertNotIn("require_cmd rsync", text)
        self.assertLess(
            text.index("stop_installer_server() {"),
            text.index('trap \'stop_installer_server; rm -rf "${WORK_DIR}"\' EXIT'),
        )

    def test_windows_and_macos_smoke_scripts_exist(self) -> None:
        windows_text = (PROJECT_ROOT / "tests" / "windows_smoke.ps1").read_text(
            encoding="utf-8"
        )
        macos_text = (PROJECT_ROOT / "tests" / "macos_smoke.sh").read_text(
            encoding="utf-8"
        )
        for snippet in [
            "windows-smoke passed",
            "omni-autonomous-agent.ps1",
            "futureagent",
            "omni-wrap-codex.cmd",
        ]:
            self.assertIn(snippet, windows_text)
        for snippet in [
            "macos-smoke passed",
            "futureagent",
            "omni-wrap-codex",
            "omni-agent-wrap",
        ]:
            self.assertIn(snippet, macos_text)

    def test_workflow_matrix_covers_windows_macos_and_linux(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "verify.yml"
        ).read_text(encoding="utf-8")
        required_snippets = [
            "ubuntu-latest",
            "windows-latest",
            "macos-latest",
            "actions/setup-node@v4",
            'node-version: "22"',
            "tests.test_cross_platform_logic",
            "tests.test_autonomous_agent",
            "tests/launch_gate_clean.sh",
            "tests/docker_smoke.sh",
            "tests/launch_gate.sh",
            "tests/pwsh_install_smoke.sh",
            "tests/windows_smoke.ps1",
            "tests/macos_smoke.sh",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, workflow)
