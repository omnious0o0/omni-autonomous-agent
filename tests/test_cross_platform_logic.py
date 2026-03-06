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
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, text)

    def test_install_ps1_keeps_self_healing_bootstrap_logic(self) -> None:
        text = (PROJECT_ROOT / ".omni-autonomous-agent" / "install.ps1").read_text(
            encoding="utf-8"
        )
        required_snippets = [
            "Ensure-PythonCommand",
            "Ensure-GitCommand",
            "Get-PowerShellHostCommand",
            "Refresh-Path",
            "Python.Python.3.12",
            "Git.Git",
            "OMNI_AGENT_REPO_URL",
            "$env:ComSpec",
            "$runnerPs1",
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
            "tests.test_cross_platform_logic",
            "tests.test_autonomous_agent",
            "tests/launch_gate.sh",
            "tests/pwsh_install_smoke.sh",
            "tests/windows_smoke.ps1",
            "tests/macos_smoke.sh",
        ]
        for snippet in required_snippets:
            self.assertIn(snippet, workflow)
