#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType


def _load_package(pkg_name: str, pkg_path: str) -> ModuleType:
    init_path = os.path.join(pkg_path, "__init__.py")
    spec = importlib.util.spec_from_file_location(pkg_name, init_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load package spec from {init_path}")

    module = importlib.util.module_from_spec(spec)
    module.__path__ = [pkg_path]
    sys.modules[pkg_name] = module
    spec.loader.exec_module(module)
    return module


def _load_module(pkg_name: str, module_name: str, pkg_path: str) -> ModuleType:
    module_path = os.path.join(pkg_path, f"{module_name}.py")
    full_name = f"{pkg_name}.{module_name}"
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec from {module_path}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    base_dir = os.path.dirname(os.path.realpath(__file__))
    pkg_dir = os.path.join(base_dir, ".omni-autonomous-agent")
    pkg_name = "omni_agent_internal"

    if not os.path.isdir(pkg_dir):
        print(
            f"error: internal package directory not found at {pkg_dir}. "
            "Re-run installation to repair this setup.",
            file=sys.stderr,
        )
        sys.exit(1)

    required = [
        "__init__.py",
        "constants.py",
        "session_manager.py",
        "updater.py",
        "cli.py",
    ]
    missing = [
        name for name in required if not os.path.isfile(os.path.join(pkg_dir, name))
    ]
    if missing:
        missing_list = ", ".join(missing)
        print(
            f"error: internal package is incomplete at {pkg_dir}. Missing: {missing_list}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        _load_package(pkg_name, pkg_dir)
        _load_module(pkg_name, "constants", pkg_dir)
        _load_module(pkg_name, "session_manager", pkg_dir)
        _load_module(pkg_name, "updater", pkg_dir)
        cli = _load_module(pkg_name, "cli", pkg_dir)
        cli.main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
