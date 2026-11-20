"""Launch the local LifeRPG web interface in a Windows application window."""

import argparse
import ctypes
import logging
import os
import secrets
import sys
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from waitress import create_server

from liferpg.automatic_backup import startup_backup
from liferpg.journal import read_events
from liferpg.preferences import preferences
from liferpg.windows_shell import Instance, Tray, configure_startup


def main():
    parser = argparse.ArgumentParser(description="LifeRPG для Windows")
    default_workspace = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        default_workspace = Path(os.environ["LOCALAPPDATA"]) / "LifeRPG"
    parser.add_argument("--workspace", type=Path, default=default_workspace)
    parser.add_argument("--login", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    log_file = workspace / "desktop.log"
    logging.basicConfig(
        filename=log_file, encoding="utf-8", level=logging.INFO
    )

    instance = None
    try:
        instance = Instance(workspace)
        if not instance.first:
            if not args.login:
                instance.signal()
            return 0
        import webview

        os.environ["DJANGO_SETTINGS_MODULE"] = "webconfig.settings"
        os.environ["LIFERPG_DEBUG"] = "0"
        os.environ.setdefault(
            'LIFERPG_SECRET_KEY', secrets.token_urlsafe(48)
        )
        data_path = workspace / 'data' / 'web'
        os.environ.setdefault('LIFERPG_WEB_DATA', str(data_path))
        os.environ.setdefault("LIFERPG_WEB_DB", str(workspace / "db.sqlite3"))

        try:
            startup_backup(data_path / "events.jsonl")
        except (OSError, ValueError):
            logging.exception("LifeRPG: автоматическая копия не создана")
            ctypes.windll.user32.MessageBoxW(
                None, "Не удалось обновить автоматическую резервную копию. "
                "Последняя успешная копия сохранена. "
                "Подробности в desktop.log.",
                "LifeRPG — резервная копия", 0x30,
            )
        profile_loaded = True
        try:
            events = read_events(
                Path(os.environ["LIFERPG_WEB_DATA"]) / "events.jsonl"
            )
        except (OSError, ValueError):
            logging.exception("LifeRPG: системные настройки не прочитаны")
            events = []
            profile_loaded = False
        shell_settings = preferences(events, "system.preferences")
        if profile_loaded and getattr(sys, "frozen", False):
            try:
                configure_startup(workspace, shell_settings["autostart"])
            except OSError:
                logging.exception("LifeRPG: путь автозапуска не обновлён")

        def apply_system(values):
            configure_startup(workspace, values["autostart"])
            shell_settings.update(values)

        application = StaticFilesHandler(get_wsgi_application())
        settings.LIFERPG_APPLY_SYSTEM = apply_system
        call_command("migrate", interactive=False, verbosity=0)

        server = create_server(application, host="127.0.0.1", port=0)
        url = f"http://127.0.0.1:{server.effective_port}/"
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            logging.info("LifeRPG: %s", url)
            quiet = (args.login and shell_settings["autostart"]
                     and shell_settings["quiet"])
            window = webview.create_window(
                "LifeRPG", url, min_size=(360, 480), hidden=quiet
            )
            icon_file = Path(__file__).resolve().parent / "assets/liferpg.ico"
            tray = Tray(window, instance, shell_settings, icon_file)
            window.events.closing += tray.on_closing

            def initialize_tray():
                from System import Action

                deadline = monotonic() + 15
                while monotonic() < deadline:
                    native = window.native
                    if native is not None and native.IsHandleCreated:
                        native.BeginInvoke(Action(tray.attach))
                        return
                    sleep(0.05)
                logging.error("LifeRPG: не удалось подключить значок в трее")

            webview.start(initialize_tray, gui="edgechromium")
        finally:
            server.task_dispatcher.shutdown()
            server.close()
            thread.join(timeout=2)
            logging.info("LifeRPG: окно закрыто")
    except Exception:
        logging.exception("LifeRPG: ошибка запуска")
        ctypes.windll.user32.MessageBoxW(
            None, f"Не удалось запустить LifeRPG. Журнал: {log_file}",
            "LifeRPG", 0x10,
        )
        return 1
    finally:
        if instance is not None:
            instance.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
