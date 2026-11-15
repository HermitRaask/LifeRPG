"""Real Edge smoke test with a disposable save and server restart."""
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

BASE = Path(__file__).resolve().parents[1]
ARTIFACTS = BASE / ".venv" / ("browser-" + uuid4().hex[:10])
ARTIFACTS.mkdir(parents=True)
os.environ["LIFERPG_WEB_DATA"] = str(ARTIFACTS)
os.environ["LIFERPG_WEB_DB"] = str(ARTIFACTS / "db.sqlite3")
os.environ["DJANGO_SETTINGS_MODULE"] = "webconfig.settings"
(ARTIFACTS / "tmp").mkdir()
os.environ["TMP"] = os.environ["TEMP"] = str(ARTIFACTS / "tmp")
sys.path.insert(0, str(BASE))

import django  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

django.setup()
from django.db import connections  # noqa: E402
from webgame import services  # noqa: E402

URL = "http://127.0.0.1:8002"
subprocess.run(
    [sys.executable, "manage.py", "migrate", "--noinput"],
    cwd=BASE, check=True, stdout=subprocess.DEVNULL,
)
server_log = (ARTIFACTS / "server.log").open("w", encoding="utf-8")


def start_server():
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", 8002)) == 0:
            raise RuntimeError("Port 8002 is already in use")
    process = subprocess.Popen(
        [sys.executable, "manage.py", "runserver",
         "127.0.0.1:8002", "--noreload"],
        cwd=BASE, stdout=server_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("Server failed; inspect " + str(ARTIFACTS))
        try:
            with urlopen(URL, timeout=1) as response:
                if response.status == 200:
                    return process
        except (OSError, URLError):
            time.sleep(0.2)
    process.terminate()
    raise RuntimeError("Server did not start")


def stop_server(process):
    process.terminate()
    process.wait(timeout=10)
    # Windows can release the listening socket shortly after process exit.
    for _ in range(50):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", 8002)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError("Owned test server has not released port 8002")


def read_state(**kwargs):
    # Playwright owns an event loop; ORM queries run in their own sync thread.
    def read():
        try:
            return services.refresh(**kwargs)
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result()


def activity(page, key):
    page.goto(URL + "/activities/?activity=" + key)
    page.get_by_label("Активность").select_option(key)
    page.get_by_role("button", name="Рассчитать награду").click()
    page.get_by_role("button", name="Подтвердить выполнение").click()
    page.wait_for_url("**/result/**")
    assert page.get_by_role("heading", name="Действие сохранено").is_visible()


def no_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "Horizontal overflow at " + page.url


process = start_server()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL)
        assert page.locator(".stat-card").count() == 6
        assert page.get_by_role(
            "heading", name="Перки", exact=True
        ).is_visible()
        assert page.get_by_role("heading", name="Модификаторы").is_visible()
        page.screenshot(path=ARTIFACTS / "desktop.png", full_page=True)
        no_overflow(page)
        activity(page, "meditation")
        activity(page, "meditation")
        state = read_state()[1]
        assert state["stats"]["will"]["level"] == 2
        assert state["stats"]["will"]["xp"] == 50
        assert "first-level" in state["achievements"]
        activity(page, "writing")
        assert read_state()[1]["writing"]["xp"] == 50
        page.reload()
        assert read_state()[1]["writing"]["xp"] == 50
        page.goto(URL + "/history/")
        page.get_by_role("link", name="Исправить или отменить").first.click()
        page.get_by_label("Интенсивность").select_option("3")
        page.get_by_role("button", name="Сохранить исправление").click()
        page.wait_for_url("**/result/**")
        page.goto(URL + "/developer/")
        page.get_by_role("button", name="Включить режим").click()
        perk_form = page.locator('form[action="/action/perk/"]')
        perk_form.get_by_label("Перк").select_option("lazy")
        perk_form.get_by_label("Включён").uncheck()
        perk_form.get_by_role("button", name="Применить").click()
        assert "lazy" not in read_state()[1]["perks"]
        page.get_by_role("button", name="Выйти из режима").click()
        activity(page, "meditation")
        events, state = read_state()
        assert events[-1]["payload"]["xp_by_stat"]["will"]["credited_xp"] == 30
        before = state
        connections.close_all()
        stop_server(process)
        process = start_server()
        page.goto(URL)
        assert read_state()[1] == before
        page.screenshot(path=ARTIFACTS / "progress.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        for section in ("", "activities/", "skills/", "quests/",
                        "achievements/", "history/", "settings/"):
            page.goto(URL + "/" + section)
            no_overflow(page)
        page.goto(URL)
        page.screenshot(path=ARTIFACTS / "mobile.png", full_page=True)
        page.get_by_role("button", name="Открыть навигацию").click()
        page.get_by_role("navigation").get_by_role(
            "link", name="История", exact=True
        ).click()
        assert page.get_by_role(
            "heading", name="История", exact=True
        ).is_visible()
        read_state(
            advance=True,
            moment=services.local_now(state["config"]) + timedelta(days=40),
        )
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.goto(URL + "/reports/")
        assert page.locator(".report-link").count() > 1
        page.locator(".report-link").first.click()
        assert page.locator(".report-detail").is_visible()
        page.screenshot(path=ARTIFACTS / "report.png", full_page=True)
        assert not errors, errors
        with services.journal_path().open("ab") as target:
            target.write(b'{"interrupted":')
        response = page.goto(URL)
        assert response.status == 503
        assert "прерванная запись" in page.locator("main").inner_text()
        page.get_by_role("button", name="Создать резервную копию").click()
        assert list((ARTIFACTS / "backups").glob("*.bak"))
        browser.close()
    (ARTIFACTS / "result.json").write_text(json.dumps({
        "status": "passed", "javascript_errors": errors,
        "checks": ["desktop", "mobile", "activity", "level", "achievement",
                   "writing", "correction", "perk", "restart", "reports",
                   "corrupt_tail", "backup"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Browser checks passed. Artifacts:", ARTIFACTS)
finally:
    stop_server(process)
    server_log.close()
