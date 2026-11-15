"""Launch the local LifeRPG web interface in a Windows application window."""

import argparse
import ctypes
import logging
import os
import secrets
import sys
from pathlib import Path
from threading import Thread

from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from waitress import create_server


def main():
    parser = argparse.ArgumentParser(description="LifeRPG для Windows")
    default_workspace = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        default_workspace = Path(os.environ["LOCALAPPDATA"]) / "LifeRPG"
    parser.add_argument("--workspace", type=Path, default=default_workspace)
    workspace = parser.parse_args().workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    log_file = workspace / "desktop.log"
    logging.basicConfig(
        filename=log_file, encoding="utf-8", level=logging.INFO
    )

    try:
        import webview

        os.environ["DJANGO_SETTINGS_MODULE"] = "webconfig.settings"
        os.environ["LIFERPG_DEBUG"] = "0"
        os.environ.setdefault(
            'LIFERPG_SECRET_KEY', secrets.token_urlsafe(48)
        )
        data_path = workspace / 'data' / 'web'
        os.environ.setdefault('LIFERPG_WEB_DATA', str(data_path))
        os.environ.setdefault("LIFERPG_WEB_DB", str(workspace / "db.sqlite3"))

        application = StaticFilesHandler(get_wsgi_application())
        call_command("migrate", interactive=False, verbosity=0)

        server = create_server(application, host="127.0.0.1", port=0)
        url = f"http://127.0.0.1:{server.effective_port}/"
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            logging.info("LifeRPG: %s", url)
            webview.create_window("LifeRPG", url, min_size=(360, 480))
            webview.start(gui="edgechromium")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
