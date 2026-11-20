"""Install one verified bundle to the permanent checkout location."""
import argparse
import os
import shutil
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liferpg.journal import read_events  # noqa: E402
from liferpg.preferences import preferences  # noqa: E402
from liferpg.windows_shell import configure_startup  # noqa: E402


def digest(path):
    return sha256(path.read_bytes()).digest()


def install(package):
    package = package.resolve()
    executable = package / "LifeRPG.exe"
    assets = package / "_internal"
    if (not executable.is_file()
            or not (assets / "python312.dll").is_file()
            or not (assets / "assets" / "liferpg.ico").is_file()
            or not (assets / "webgame" / "templates").is_dir()):
        raise ValueError("Неполная сборка LifeRPG.")
    if package == ROOT:
        raise ValueError("Выберите сборку из dist.")
    # Keep the same profile used by direct launches and the normal shortcut.
    workspace = Path(os.environ["LOCALAPPDATA"]) / "LifeRPG"
    events = read_events(workspace / "data" / "web" / "events.jsonl")
    enabled = preferences(events, "system.preferences")["autostart"]
    for source in assets.rglob("*"):
        if not source.is_file():
            continue
        destination = ROOT / source.relative_to(package)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or digest(source) != digest(destination):
            shutil.copy2(source, destination)
        if digest(source) != digest(destination):
            raise OSError(f"Проверка установки не прошла: {destination}")
    target = ROOT / "LifeRPG.exe"
    shutil.copy2(executable, target)
    if digest(executable) != digest(target):
        raise OSError("Проверка EXE не прошла.")
    configure_startup(workspace, enabled, executable=target)
    print(f"Установлено: {target}")
    return workspace


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    install(parser.parse_args().package)
