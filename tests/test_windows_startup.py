"""Startup always targets the installed EXE inside a checkout."""
import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="Windows registry",
)


def test_build_targets_permanent_exe(tmp_path):
    shell = importlib.import_module("liferpg.windows_shell")
    root = tmp_path / "project"
    build = root / "dist" / "test"
    build.mkdir(parents=True)
    (root / "LifeRPG.spec").touch()
    installed = root / "LifeRPG.exe"
    installed.touch()
    with patch.object(shell, "sys", SimpleNamespace(
        executable=str(build / "LifeRPG.exe"), frozen=True,
    )):
        assert shell.installed_executable() == installed


def test_external_install_uses_own_exe(tmp_path):
    shell = importlib.import_module("liferpg.windows_shell")
    exe = tmp_path / "LifeRPG.exe"
    exe.touch()
    with patch.object(shell, "sys", SimpleNamespace(
        executable=str(exe), frozen=True,
    )):
        assert shell.installed_executable() == exe


def test_registry_command_and_disable(tmp_path):
    shell = importlib.import_module("liferpg.windows_shell")
    exe = tmp_path / "Installed App" / "LifeRPG.exe"
    workspace = tmp_path / "Saved Profile"
    with patch.object(shell.winreg, "CreateKeyEx") as registry:
        key = registry.return_value.__enter__.return_value
        expected = f'"{exe}" --workspace "{workspace}" --login'
        with patch.object(shell.winreg, "SetValueEx") as save, patch.object(
            shell.winreg, "QueryValueEx", return_value=(expected, 1),
        ):
            shell.configure_startup(workspace, True, executable=exe)
            command = save.call_args.args[-1]
            assert command == (
                f'"{exe}" --workspace "{workspace}" --login'
            )
        with patch.object(shell.winreg, "DeleteValue") as delete:
            shell.configure_startup(workspace, False, executable=exe)
            delete.assert_called_once_with(
                key, "LifeRPG-" + shell.workspace_id(workspace)
            )
